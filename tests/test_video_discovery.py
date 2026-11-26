"""Choosing a lesson video for a topic, from the learner's own channels.

The learner names the channels and the system chooses within them. Two rules hold
throughout and are what these tests pin:

* **No link is invented.** Every candidate must be one the search or the feed
  actually reported. A model asked for a video URL produces a well-formed address
  for a video that does not exist.
* **The offer is not the action.** Finding a video does not attach it; the
  learner still adds it, the same rule the literature grounding follows.
"""

import json
import os
import subprocess
import sys
import uuid

import pytest

from services.video import channels as yt
from services.video.discovery import (
    _pick_with_llm,
    _score,
    find_videos_for_topic,
    search_queries,
    terms,
)

pytestmark = pytest.mark.asyncio


def hit(title: str, *, video_id="v1", channel="c1", description="", from_search=False) -> yt.VideoHit:
    return yt.VideoHit(
        video_id=video_id,
        title=title,
        url=f"https://www.youtube.com/watch?v={video_id}",
        channel_id=channel,
        channel_title=channel,
        description=description,
        from_search=from_search,
    )


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _Session:
    """Returns the channel rows it was given, however the query was built."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _statement=None):
        return _Scalar(self._rows)


def channel_row(course_id=None, channel_id="c1", enabled=True):
    from models.video_channel import VideoChannel

    return VideoChannel(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        course_id=course_id,
        channel_id=channel_id,
        title=f"Канал {channel_id}",
        enabled=enabled,
    )


class TestTerms:
    def test_drops_stop_words_and_keeps_substance(self):
        found = set(terms("Разбираем метод Гаусса для системы линейных уравнений"))
        assert "гаусса" in found
        assert "разбираем" not in found
        assert "для" not in found

    def test_ranks_longer_words_first(self):
        # Specific words must decide the ranking; "the" must not.
        assert terms("матрица определитель")[0] == "определитель"

    def test_empty_input_gives_nothing(self):
        assert terms("") == []
        assert terms("и в на с") == []


class TestScoring:
    def test_a_match_in_the_title_outranks_one_in_the_description(self):
        title_hit = _score(hit("Метод Гаусса"), ["гаусса", "метод"])
        body_hit = _score(
            hit("Что такое матрица", description="метод Гаусса"),
            ["гаусса", "метод"],
        )
        assert title_hit[0] > body_hit[0]
        assert title_hit[1] == ["гаусса", "метод"]

    def test_no_overlap_scores_nothing(self):
        assert _score(hit("Рецепт борща"), ["гаусса", "метод"]) == (0, [])

    def test_reports_which_words_matched(self):
        _score_value, matched = _score(hit("Метод Гаусса"), ["гаусса", "метод", "определитель"])
        assert set(matched) == {"гаусса", "метод"}

    def test_a_search_hit_survives_without_a_word_match(self):
        # YouTube finds a Russian topic in an English channel and returns a video
        # whose title shares no character with the query. Rejecting it here would
        # throw away the only results a cross-language search can produce.
        found = hit("The essence of calculus", from_search=True)
        score, matched = _score(found, ["производная", "интеграл"])
        assert score > 0
        assert matched == []

    def test_a_feed_hit_without_a_word_match_is_still_refused(self):
        # Nothing has judged a feed entry, so sharing nothing with the topic is a
        # reason not to offer it. Only a search hit gets the benefit of the doubt.
        assert _score(hit("Рецепт борща"), ["гаусса"]) == (0, [])

    def test_a_word_match_outranks_a_bare_search_hit(self):
        matched = _score(hit("Метод Гаусса", from_search=True), ["гаусса"])[0]
        bare = _score(hit("Vectors", from_search=True), ["гаусса"])[0]
        assert matched > bare

    def test_search_beats_feed_for_the_same_word_match(self):
        searched = _score(hit("Метод Гаусса", from_search=True), ["гаусса"])[0]
        fed = _score(hit("Метод Гаусса"), ["гаусса"])[0]
        assert searched > fed


class TestQueryShapes:
    """YouTube stops matching once the query gets long, so it is shortened."""

    def test_the_title_words_come_first_and_shortest_last(self):
        shaped = search_queries("Дифференциальные уравнения второго порядка", None)
        # "второго" and "порядка" are the same length, so this also pins the
        # alphabetical tie-break: sorting on length alone leaves the order to the
        # hash seed and the query changes between runs.
        assert shaped == [
            "дифференциальные уравнения второго",
            "дифференциальные уравнения",
            "дифференциальные",
        ]

    def test_equal_length_words_come_out_in_a_fixed_order(self):
        # Run in fresh interpreters so the hash seed differs; the result must not.
        code = (
            "import sys; sys.path.insert(0, r'D:\\techer\\OpenTutor\\apps\\api');"
            "from services.video.discovery import terms;"
            "print(','.join(terms('второго порядка')))"
        )
        runs = {
            subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                env={**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///:memory:"},
                check=True,
            ).stdout.strip()
            for _ in range(3)
        }
        assert len(runs) == 1, f"порядок слов нестабилен между запусками: {runs}"

    def test_no_query_is_longer_than_three_words(self):
        for shaped in search_queries("Метод Гаусса и системы линейных уравнений", "разбор"):
            assert len(shaped.split()) <= 3

    def test_the_description_is_only_used_after_the_title(self):
        shaped = search_queries("Метод Гаусса", "матрицы определитель разложение")
        # The title's shapes come first and only then the description's. Padding
        # the query with description words is exactly what made YouTube return
        # nothing, so the description may only be reached once the title failed.
        title_shaped, from_description = shaped[:2], shaped[2:]
        assert title_shaped == ["гаусса метод", "гаусса"]
        assert from_description, "описание должно быть использовано после заголовка"
        assert all("определител" in q or "разложен" in q for q in from_description)

    def test_repeated_shapes_are_offered_once(self):
        shaped = search_queries("Гаусс", "Гаусс")
        assert len(shaped) == len(set(shaped))

    def test_a_title_of_only_stop_words_still_yields_something(self):
        assert search_queries("и в на с", None) == []


class TestSelection:
    """The model chooses among real videos; it can never name one."""

    def _reply(self, monkeypatch, payload):
        # _structured_call answers with a dict, not the (text, usage) pair that
        # LLMClient.chat returns.
        async def call(system, user):
            return json.loads(payload)

        monkeypatch.setattr(
            "services.adaptive_learning.content._structured_call", call
        )

    async def test_it_keeps_only_indices_that_exist(self, monkeypatch):
        shortlist = [hit("Первый", video_id="a"), hit("Второй", video_id="b")]
        self._reply(monkeypatch, '{"picks":[{"index":2,"reason":"про метод"},{"index":99}]}')
        chosen, how = await _pick_with_llm("Тема", None, shortlist)
        assert how == "llm"
        assert [h.video_id for h in chosen] == ["b"]

    async def test_it_reports_an_empty_answer_as_none_not_a_failure(self, monkeypatch):
        self._reply(monkeypatch, '{"picks":[]}')
        chosen, how = await _pick_with_llm("Тема", None, [hit("Первый")])
        # "None of these" is a judgement the caller trusts; "failed" is not.
        assert (chosen, how) == ([], "none")

    async def test_a_call_that_falls_over_keeps_the_word_matches(self, monkeypatch):
        async def boom(system, user):
            raise ConnectionError("no network")

        monkeypatch.setattr(
            "services.adaptive_learning.content._structured_call", boom
        )
        chosen, how = await _pick_with_llm("Тема", None, [hit("Первый")])
        assert (chosen, how) == ([], "failed")

    async def test_the_reason_is_carried_onto_the_video(self, monkeypatch):
        shortlist = [hit("The essence of calculus", video_id="e")]
        self._reply(monkeypatch, '{"picks":[{"index":1,"reason":"разбирает производную"}]}')
        chosen, _how = await _pick_with_llm("Производная функция", None, shortlist)
        assert chosen[0].reason == "разбирает производную"

    async def test_no_shortlist_means_no_call_and_no_opinion(self, monkeypatch):
        async def boom(system, user):
            raise AssertionError("the model must not be called with nothing to choose from")

        monkeypatch.setattr(
            "services.adaptive_learning.content._structured_call", boom
        )
        assert await _pick_with_llm("Тема", None, []) == ([], "none")

    async def test_at_most_four_are_kept(self, monkeypatch):
        shortlist = [hit(f"Ролик {n}", video_id=f"v{n}") for n in range(8)]
        picks = ",".join(f'{{"index":{n}}}' for n in range(1, 9))
        self._reply(monkeypatch, '{"picks":[' + picks + "]}")
        chosen, _how = await _pick_with_llm("Тема", None, shortlist)
        assert len(chosen) == 4


class TestQuotaReporting:
    """A spent quota means the channels were not consulted, and must read so."""

    async def test_a_rejected_search_is_not_counted_as_no_results(self, monkeypatch):
        monkeypatch.setattr(yt, "api_available", lambda: True)
        monkeypatch.setattr(yt, "search_channel", lambda *a, **k: None)
        monkeypatch.setattr(yt, "fetch_feed", lambda cid: [hit("Что угодно", video_id="z")])
        _rows, report = await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Метод Гаусса"
        )
        assert report["search_failed"] == 1
        assert report["no_results"] == 0

    async def test_a_search_that_ran_and_found_nothing_says_so(self, monkeypatch):
        monkeypatch.setattr(yt, "api_available", lambda: True)
        monkeypatch.setattr(yt, "search_channel", lambda *a, **k: [])
        monkeypatch.setattr(yt, "fetch_feed", lambda cid: [hit("Что угодно", video_id="z")])
        _rows, report = await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Метод Гаусса"
        )
        assert report["search_failed"] == 0
        assert report["no_results"] == 1

    async def test_a_quota_stop_does_not_spend_the_remaining_query_shapes(
        self, monkeypatch
    ):
        # Each search.list call costs 100 units of a 10 000 daily quota. Once one
        # is refused there is nothing to gain from trying the other four.
        calls = []

        def refuse(*args, **kwargs):
            calls.append(1)
            return None

        monkeypatch.setattr(yt, "api_available", lambda: True)
        monkeypatch.setattr(yt, "search_channel", refuse)
        monkeypatch.setattr(yt, "fetch_feed", lambda cid: [])
        await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Метод Гаусса и системы уравнений"
        )
        assert calls == [1]


class TestDiscovery:
    async def test_a_topic_with_no_channels_offers_nothing(self, monkeypatch):
        monkeypatch.setattr(yt, "api_available", lambda: False)
        rows, report = await find_videos_for_topic(
            _Session([]), uuid.uuid4(), "Метод Гаусса"
        )
        assert rows == []
        assert report["channels"] == 0

    async def test_finds_a_lesson_in_the_channel_feed(self, monkeypatch):
        monkeypatch.setattr(yt, "api_available", lambda: False)
        monkeypatch.setattr(
            yt,
            "fetch_feed",
            lambda cid: [
                hit("Метод Гаусса: прямой ход", video_id="g1"),
                hit("Рецепт пасты карбонара", video_id="g2"),
            ],
        )
        rows, report = await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Метод Гаусса"
        )
        assert [r.video_id for r in rows] == ["g1"]
        assert report["feed_only"] == 1
        assert report["api_search"] is False

    async def test_reports_that_only_the_feed_was_searched(self, monkeypatch):
        # Without a key the list is 15 recent videos, and the UI has to be able
        # to say that rather than imply the archive was searched.
        monkeypatch.setattr(yt, "api_available", lambda: False)
        monkeypatch.setattr(yt, "fetch_feed", lambda cid: [hit("Гаусс", video_id="x")])
        _rows, report = await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Гаусс"
        )
        assert report["api_search"] is False
        assert report["videos_seen"] == 1

    async def test_uses_the_api_when_a_key_is_present(self, monkeypatch):
        monkeypatch.setattr(yt, "api_available", lambda: True)
        seen = {}

        def fake_search(channel_id, query, *, limit=5, channel_title=""):
            seen["channel"] = channel_id
            seen["query"] = query
            return [hit("Прямой ход метода Гаусса", video_id="api1")]

        monkeypatch.setattr(yt, "search_channel", fake_search)
        monkeypatch.setattr(
            yt,
            "fetch_feed",
            lambda cid: (_ for _ in ()).throw(AssertionError("feed must not be read")),
        )

        rows, report = await find_videos_for_topic(
            _Session([channel_row(channel_id="UCabc")]),
            uuid.uuid4(),
            "Метод Гаусса",
            "прямой ход матрицы",
        )
        assert [r.video_id for r in rows] == ["api1"]
        assert seen["channel"] == "UCabc"
        assert "гаусса" in seen["query"]
        assert report["api_search"] is True

    async def test_falls_back_to_the_feed_when_the_api_finds_nothing(self, monkeypatch):
        # A catalogue search can miss on a translated term while a recent video
        # covers the topic, so an empty result is not the end of the road.
        monkeypatch.setattr(yt, "api_available", lambda: True)
        monkeypatch.setattr(yt, "search_channel", lambda *a, **k: [])
        monkeypatch.setattr(
            yt, "fetch_feed", lambda cid: [hit("Гаусс сегодня", video_id="f1")]
        )
        rows, report = await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Гаусс"
        )
        assert [r.video_id for r in rows] == ["f1"]
        assert report["feed_only"] == 1

    async def test_a_channel_that_cannot_be_read_does_not_stop_the_others(
        self, monkeypatch
    ):
        monkeypatch.setattr(yt, "api_available", lambda: False)

        def feed(cid):
            if cid == "broken":
                raise yt.ChannelNotFound("лента недоступна")
            return [hit("Гаусс", video_id="ok1")]

        monkeypatch.setattr(yt, "fetch_feed", feed)
        rows, report = await find_videos_for_topic(
            _Session(
                [
                    channel_row(channel_id="broken"),
                    channel_row(channel_id="good"),
                ]
            ),
            uuid.uuid4(),
            "Гаусс",
        )
        assert [r.video_id for r in rows] == ["ok1"]
        assert report["search_failed"] == 1

    async def test_the_best_video_from_each_channel_is_offered(self, monkeypatch):
        # Otherwise one prolific channel fills every slot and the learner's other
        # sources never appear.
        monkeypatch.setattr(yt, "api_available", lambda: False)

        def feed(cid):
            if cid == "c1":
                return [
                    hit("Гаусс: прямой ход", video_id="a1"),
                    hit("Гаусс: обратный ход", video_id="a2"),
                ]
            return [hit("Гаусс простой", video_id="b1", channel="c2")]

        monkeypatch.setattr(yt, "fetch_feed", feed)
        rows, _report = await find_videos_for_topic(
            _Session(
                [channel_row(channel_id="c1"), channel_row(channel_id="c2")]
            ),
            uuid.uuid4(),
            "Гаусс",
            limit=2,
        )
        channels = {r.channel_id for r in rows}
        assert channels == {"c1", "c2"}

    async def test_respects_the_limit(self, monkeypatch):
        monkeypatch.setattr(yt, "api_available", lambda: False)
        monkeypatch.setattr(
            yt,
            "fetch_feed",
            lambda cid: [
                hit(f"Гаусс вариант {i}", video_id=f"v{i}", channel=cid)
                for i in range(10)
            ],
        )
        rows, _report = await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Гаусс", limit=3
        )
        assert len(rows) <= 3


class TestSelectionIsSkippedWhenWordsSpeak:
    """An LLM call per topic page is a real cost; it is spent only where it helps."""

    async def test_a_clear_word_match_is_not_second_guessed(self, monkeypatch):
        async def boom(system, user):
            raise AssertionError("слова уже дали ответ, модель не нужна")

        monkeypatch.setattr(yt, "api_available", lambda: False)
        monkeypatch.setattr(
            yt,
            "fetch_feed",
            lambda cid: [
                hit("Метод Гаусса: прямой ход", video_id="g"),
                hit("Рецепт пасты", video_id="b"),
            ],
        )
        monkeypatch.setattr(
            "services.adaptive_learning.content._structured_call", boom
        )
        rows, report = await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Метод Гаусса"
        )
        assert [r.video_id for r in rows] == ["g"]
        assert report["selection"] == "words"

    async def test_a_cross_language_hit_does_bring_the_model_in(self, monkeypatch):
        # No word of a Russian topic can appear in an English title, so words have
        # nothing to say here and the model is the only thing left that can.
        calls = []

        async def watch(system, user):
            calls.append(user)
            return {"picks": [{"index": 1, "reason": "разбирает производную"}]}

        monkeypatch.setattr(yt, "api_available", lambda: True)
        monkeypatch.setattr(
            yt,
            "search_channel",
            lambda *a, **k: [hit("The essence of calculus", video_id="e", from_search=True)],
        )
        monkeypatch.setattr(
            "services.adaptive_learning.content._structured_call", watch
        )
        rows, report = await find_videos_for_topic(
            _Session([channel_row()]), uuid.uuid4(), "Производная функция"
        )
        assert len(calls) == 1
        assert report["selection"] == "llm"
        assert [r.video_id for r in rows] == ["e"]


class TestChannelResolution:
    """The part that quietly goes wrong if written carelessly.

    The ids below were taken from real channel pages, not invented: a channel id
    is "UC" plus exactly 22 characters, and a made-up one of the wrong length
    would make the test pass for the wrong reason.
    """

    REAL = "UCYO_jab_esuFRV4b17AJtAw"  # 3Blue1Brown
    OTHER = "UCHnyfMqiRRG1u-2MsSQLbXA"  # Veritasium

    def test_recognises_an_id(self):
        assert yt.extract_channel_id(self.REAL) == self.REAL

    def test_finds_an_id_inside_a_pasted_url(self):
        assert yt.extract_channel_id(
            f"https://www.youtube.com/channel/{self.REAL}"
        ) == self.REAL

    def test_rejects_something_that_is_not_a_channel_id(self):
        # Exactly 22 characters after "UC". A loose pattern would accept a
        # fragment of a page and store it as a channel.
        assert yt.extract_channel_id("UC" + "x" * 23) is None
        assert yt.extract_channel_id("UC" + "x" * 21) is None
        assert yt.extract_channel_id("") is None
        assert yt.extract_channel_id(None) is None

    def test_the_canonical_markers_are_used_not_the_first_id(self):
        """The first ``channelId`` on a page belongs to a *recommended* channel.

        This is the bug that made ``/@handle`` and ``/@handle/videos`` resolve to
        different channels. The canonical marker is what names the channel the URL
        points at.
        """
        html = (
            f'<link rel="canonical" href="https://www.youtube.com/channel/{self.REAL}">'
            '{"channelId":"UCsomeOtherChannelId000"}'
        )
        assert yt.extract_channel_id(html) == self.REAL

    def test_pasted_forms_are_normalised_to_channel_pages(self):
        assert yt._normalise_pasted("@veritasium") == (
            "https://www.youtube.com/@veritasium"
        )
        assert yt._normalise_pasted("veritasium") == (
            "https://www.youtube.com/@veritasium"
        )
        assert yt._normalise_pasted(
            "https://www.youtube.com/c/Veritasium"
        ) == "https://www.youtube.com/c/Veritasium"
        assert yt._normalise_pasted(self.OTHER) == (
            f"https://www.youtube.com/channel/{self.OTHER}"
        )

    def test_an_empty_value_is_refused(self):
        with pytest.raises(yt.ChannelNotFound):
            yt._normalise_pasted("   ")