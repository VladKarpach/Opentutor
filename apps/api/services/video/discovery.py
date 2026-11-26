"""Choosing a channel's videos for a topic.

Same shape as the literature grounding, deliberately: the learner's own sources,
matched by words, with the learner deciding. What differs is the depth of the
match. A book can be searched for a phrase; a channel has to be searched through
YouTube, so the two paths are:

* with a Data API key — search the channel's whole catalogue for the topic's
  words, which is the case the feature exists for;
* without one — read the public feed (the newest 15) and rank those by words,
  which finds something plausible but only ever recent.

Both report which words matched, so the offer can be judged rather than trusted.
A video whose title shares nothing with the topic is not a lesson about it, and
saying "matched on: производная, интеграл" is what lets the learner see that
difference at a glance.
"""

import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.video_channel import VideoChannel
from services.video import channels as yt

logger = logging.getLogger(__name__)

# Words that appear in nearly every educational title and so discriminate
# nothing. Russian and English, plus the video-idiom words YouTube's own
# metadata is full of.
_STOPWORDS = frozenset(
    """
    и или но а в во не что он на я с со как а то все она так его да ты к у же вы
    за бы по только ее мне было вот от меня еще нет о из ему теперь когда даже ну
    вдруг ли если ни быть был него до вас опять уж вам сказал ведь там потом себя
    ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб
    без чего раз тоже себе под будет ж тогда кто этот того потому этого какой
    совсем ним здесь этом один почти мой тем чтобы нее какие много разве три эту
    моя хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
    конечно всю между это часть разбираем разбираемся объясняет объясняю урок
    занятие курс ролик видео смотрите смотри посмотрите узнаем разбираемся
    the a an and or of to in is are was were be for on with as at by from this that
    these those it its into about which who whom whose what how why when we you
    your our their they he she not but can will would should could video lesson
    part episode explained explaining learn learning course watch
    """.split()
)

# Russian technical term -> English equivalent. YouTube's cross-language search
# will happily return an English video for a Russian query, but only if the
# query actually contains words the English titles use. Without this, a Russian
# topic like "дифференциальные уравнения" never finds English calculus videos.
_RU_TO_EN = {
    "дифференциальные": "differential",
    "дифференциальное": "differential",
    "уравнения": "equations",
    "уравнение": "equation",
    "производные": "derivatives",
    "производная": "derivative",
    "интегралы": "integrals",
    "интеграл": "integral",
    "метод гаусса": "gaussian elimination",
    "гауса": "gauss",
    "линейные": "linear",
    "линейное": "linear",
    "системы": "systems",
    "система": "system",
    "машинное": "machine",
    "обучение": "learning",
    "алгоритмы": "algorithms",
    "алгоритм": "algorithm",
    "структуры": "structures",
    "данных": "data",
    "базы": "database",
    "база": "database",
    "программирование": "programming",
    "язык": "language",
    "python": "python",
    "путь": "path",
    "граф": "graph",
    "теория": "theory",
    "вероятность": "probability",
    "статистика": "statistics",
    "математический": "mathematical",
    "анализ": "analysis",
    "функция": "function",
    "функции": "functions",
    "предел": "limit",
    "пределы": "limits",
    "ряд": "series",
    "ряды": "series",
}

# Very short titles are almost never specific enough to be useful.
_MIN_TITLE_CHARS = 8


def terms(text: str) -> list[str]:
    """Content-bearing words, longest first so specific ones rank the results.

    Words of equal length are ordered alphabetically. Sorting on length alone
    leaves ties to the iteration order of a ``set``, which for strings varies with
    the process hash seed: the same topic then produced different queries on
    different runs, and a request that cannot be reproduced is impossible to
    reason about when YouTube returns nothing for it.
    """
    words = re.findall(r"[^\W\d_]{3,}", (text or "").lower(), flags=re.UNICODE)
    kept = [w for w in words if w not in _STOPWORDS]
    return sorted(set(kept), key=lambda w: (-len(w), w))


def search_queries(topic_title: str, topic_description: str | None) -> list[str]:
    """Query shapes to try, most specific first.

    YouTube treats a long query as a filter rather than a topic and returns
    nothing: "производные интегралы" finds a dozen calculus videos, while
    "дифференциальные уравнения второго порядка" finds none — and the first
    alone finds three. Padding the query with words from the topic's description
    is exactly how that happened here, so the description is deliberately left out
    of the first attempt and only used when the title words find nothing.

    Each entry costs a `search.list` call, so the list is short and ordered by how
    likely it is to succeed.
    """
    title_words = terms(topic_title)
    shaped: list[str] = []

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in shaped:
            shaped.append(value)

    # The topic's own words, most specific first. Never more than three: past
    # that YouTube starts intersecting and drops everything.
    if title_words:
        add(" ".join(title_words[:3]))
        add(" ".join(title_words[:2]))
        add(title_words[0])

        # English equivalents for cross-language search: a Russian topic about
        # calculus should also find English-titled calculus videos. Try the
        # English translation of the full phrase and of the first two words.
        en_phrase = " ".join(_RU_TO_EN.get(w, w) for w in title_words[:3])
        if en_phrase != " ".join(title_words[:3]):
            add(en_phrase)
        en_pair = " ".join(_RU_TO_EN.get(w, w) for w in title_words[:2])
        if en_pair != " ".join(title_words[:2]):
            add(en_pair)

    # Nothing from the title worked, so the description may name the subject in
    # words the title left out.
    described = terms(topic_description or "")[:3]
    if described:
        add(" ".join(described[:2]))
        add(described[0])

    return shaped


def _score(hit: yt.VideoHit, wanted: list[str], position: int = 0) -> tuple[int, list[str]]:
    """How well a video fits, and which words decided it.

    A video that came back from a YouTube search is not re-judged here. YouTube
    already decided it was relevant, and it can be relevant *across languages*:
    an English-titled calculus video found for "производная" shares no characters
    with the topic, so demanding a word match would discard the hits that only a
    cross-language search could have found. The local score is used to order the
    results and to report which words matched, not to exclude any.

    A feed result gets no such benefit — nothing has judged it, so a title sharing
    nothing with the topic is not offered.
    """
    title = (hit.title or "").lower()
    body = (hit.description or "").lower()

    title_hits = {t for t in wanted if t in title}
    body_hits = {t for t in wanted if t in body}
    distinct = title_hits | body_hits

    if not distinct:
        if not hit.from_search:
            return 0, []
        # Ranked below everything with a real word match, and below earlier
        # results, but still offered: YouTube put it in the list.
        return 1 + max(0, 10 - position), []

    # A word in the heading is worth much more than one buried in a description,
    # and longer words are more specific than shorter ones.
    score = sum((4 if t in title_hits else 1) * len(t) for t in distinct)
    # Most words of the query present beats one word twice.
    if len(distinct) >= 2:
        score += 6
    if len(title) < _MIN_TITLE_CHARS:
        score -= 4
    if hit.from_search and distinct:
        # A search hit is preferred over a feed item of comparable word match:
        # it was found by asking, not by reading whatever was newest. The bonus
        # only applies when there is at least one real word match — a search
        # hit sharing no words with the topic is not a lesson on it.
        score += 10
    return score, sorted(distinct)


# How many of a channel's results are handed to the model. YouTube's
# channel-scoped search is loose — asked for "Метод Гаусса" inside a maths
# channel it can answer with a video about mathematical explanation in
# general — so the shortlist has to be large enough that a genuinely fitting one
# is in it, and the model has to choose from it.
_SHORTLIST = 12

_MAX_PICK = 4


async def _pick_with_llm(
    topic_title: str,
    topic_description: str | None,
    hits: list[yt.VideoHit],
) -> tuple[list[yt.VideoHit], str]:
    """Have the model choose which of these videos are actually a lesson here.

    The URL set is fixed before the call and the answer is a list of *indices*.
    That is the whole safety argument: the model cannot produce a link, only point
    at one YouTube already returned, so an invented video is not representable.
    The opposite failure — a real video that is not about this at all — is what
    this call exists to prevent, since YouTube's own channel search will happily
    answer "Метод Гаусса" with a video about explaining mathematics.

    Returns ``(picks, how)`` where *how* is ``llm``, ``none`` (asked and answered
    that nothing fits), ``no_llm``, ``failed`` or ``unparsed``. The caller keeps
    its word-match ranking for the last three and trusts ``none``.
    """
    from services.adaptive_learning.content import _structured_call, as_data_block

    shortlist = hits[:_SHORTLIST]
    if not shortlist:
        return [], "none"

    catalogue = "\n".join(
        f"{index}. {hit.title}" + (f" — {hit.description[:160]}" if hit.description else "")
        for index, hit in enumerate(shortlist, start=1)
    )
    task = topic_title.strip()
    if topic_description:
        task += f"\n{topic_description.strip()}"

    system = (
        "You decide which videos would genuinely serve as the lesson for a study "
        "topic. You are given a numbered list of videos that are already known to "
        "exist; you choose among them by number and never write a link yourself.\n"
        "Reply with JSON only, no commentary outside it."
    )
    user = (
        f"Topic the lesson must teach:\n{as_data_block(task, 'topic')}\n\n"
        f"Videos from the learner's channels:\n"
        f"{as_data_block(catalogue, 'videos')}\n\n"
        "Choose the videos whose subject IS this topic, not merely something "
        "adjacent to it: a video about mathematical explanation in general is not "
        "a lesson on Gaussian elimination, and a video on a neighbouring topic is "
        "not a lesson on this one. A video in another language is fine if it "
        "teaches the same subject.\n"
        "If none of them is a lesson on this topic, choose none — an empty answer "
        "is correct and better than a wrong one.\n"
        "Reply exactly, up to "
        f"{_MAX_PICK} picks: "
        '{"picks":[{"index":1,"reason":"why, in the language of the topic"}]}'
    )

    try:
        payload = await _structured_call(system, user)
    except (ConnectionError, TimeoutError, RuntimeError, ValueError) as exc:
        # Documented boundary: a failed selection call degrades to the word match
        # that already ran, so the feature still offers something.
        logger.info("Video selection unavailable (%s); keeping word matches", exc)
        return [], "failed"

    picks = payload.get("picks")
    if not isinstance(picks, list):
        return [], "unparsed"
    if not picks:
        return [], "none"

    chosen: list[yt.VideoHit] = []
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        try:
            index = int(pick.get("index", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(shortlist):
            continue
        hit = shortlist[index]
        reason = pick.get("reason")
        if isinstance(reason, str) and reason.strip():
            hit.reason = reason.strip()[:200]
        # ``matched_terms`` stays empty for a cross-language pick: no word of a
        # Russian topic can appear in an English title. ``reason`` carries the
        # justification instead, and the UI shows one or the other.
        chosen.append(hit)
        if len(chosen) >= _MAX_PICK:
            break
    return chosen, "llm" if chosen else "none"


async def active_channels(
    db: AsyncSession, user_id, course_id=None
) -> list[VideoChannel]:
    """The channels to search: the learner's shared ones plus this course's.

    Both scopes in one query. ``course_id is None`` is written as ``IS NULL`` by
    SQLAlchemy for ``==``, which is exactly what a global channel is.
    """
    result = await db.execute(
        select(VideoChannel)
        .where(
            VideoChannel.user_id == user_id,
            VideoChannel.enabled.is_(True),
            or_(
                VideoChannel.course_id == course_id,
                VideoChannel.course_id.is_(None),
            ),
        )
        .order_by(VideoChannel.course_id.is_not(None), VideoChannel.created_at)
    )
    return list(result.scalars().all())


async def find_videos_for_topic(
    db: AsyncSession,
    user_id,
    topic_title: str,
    topic_description: str | None = None,
    *,
    course_id=None,
    limit: int = 6,
) -> tuple[list[yt.VideoHit], dict]:
    """Videos from the learner's channels that could serve as this lesson.

    Two passes. The first collects everything the channels offer and ranks it by
    words, which is enough when a Russian topic meets a Russian channel. The
    second asks the model to pick out of that shortlist, because a Russian topic
    meets an English channel and then no title shares a single character with the
    query — YouTube's cross-language search is the only thing that can find those,
    and it is loose enough on its own that the model has to decide which of its
    answers are really a lesson on this topic.

    Returns the hits and a small report saying how they were found, so the UI can
    be honest about a shallow result instead of presenting it as exhaustive.
    """
    wanted = terms(topic_title)
    described = terms(topic_description or "")[:6]
    query_terms = list(dict.fromkeys(wanted[:8] + described))
    queries = search_queries(topic_title, topic_description)

    channels = await active_channels(db, user_id, course_id)
    report = {
        "channels": len(channels),
        "api_search": yt.api_available(),
        "feed_only": 0,
        "search_failed": 0,
        "no_results": 0,
        "videos_seen": 0,
        "queries": len(queries),
    }
    if not channels:
        return [], report

    scored: list[tuple[int, yt.VideoHit, list[str]]] = []
    # Whether any channel actually returned results from the Data API search.
    # YouTube's channel-scoped search is loose, so when it was used the model
    # must review the shortlist even when word matches exist.
    api_search_used = False

    for channel in channels:
        label = channel.title or channel.channel_id
        found: list[yt.VideoHit] | None = None
        searched = False

        if report["api_search"]:
            for query in queries:
                found = yt.search_channel(
                    channel.channel_id,
                    query,
                    limit=limit,
                    channel_title=label,
                )
                if found is None:
                    # The search did not happen: no usable key, quota spent, or
                    # the network. Tallyed separately from "no results" because
                    # the learner needs to hear the difference.
                    report["search_failed"] += 1
                    break
                searched = True
                if found:
                    api_search_used = True
                    break

        if not found:
            try:
                feed = yt.fetch_feed(channel.channel_id)
            except yt.ChannelNotFound as exc:
                logger.info("Channel %s unreadable: %s", channel.channel_id, exc)
                report["search_failed"] += 1
                continue
            for hit in feed:
                hit.channel_title = hit.channel_title or label
            report["feed_only"] += 1
            if searched:
                # The catalogue was searched properly and had nothing on this
                # topic. The feed is a different slice of the same channel, so
                # reading it is still worth the one request.
                report["no_results"] += 1
            found = feed

        report["videos_seen"] += len(found)
        for position, hit in enumerate(found):
            score, matched = _score(hit, query_terms, position)
            if score <= 0:
                continue
            hit.matched_terms = matched
            scored.append((score, hit, matched))

    scored.sort(key=lambda item: item[0], reverse=True)

    # The shortlist the model chooses from: the best of each channel, so one
    # prolific channel cannot fill the whole list with its own output.
    shortlist: list[yt.VideoHit] = []
    seen_channels: set[str] = set()
    for _score_value, hit, _matched in scored:
        if hit.channel_id in seen_channels:
            continue
        seen_channels.add(hit.channel_id)
        shortlist.append(hit)
        if len(shortlist) >= _SHORTLIST:
            break
    for _score_value, hit, _matched in scored:
        if hit not in shortlist:
            shortlist.append(hit)
        if len(shortlist) >= _SHORTLIST:
            break

    # The model reviews the shortlist whenever the Data API search was actually
    # used: YouTube's channel-scoped search is loose and can return loosely
    # related videos (e.g. "mathematical explanation in general" for a request
    # about Gaussian elimination). Word matches alone are not enough evidence
    # that a video is genuinely a lesson on this topic.
    # When only the public feed was used, word matches are the only signal and
    # the model adds little — skip it there to save cost.
    if api_search_used:
        chosen, how = await _pick_with_llm(topic_title, topic_description, shortlist)
        report["selection"] = how

        if how == "llm":
            # The model's order is the answer — it put the genuinely on-topic ones
            # first — so it is kept rather than re-sorted by the word score.
            return chosen[:limit], report

        if how == "none" and shortlist:
            # The model was asked and answered "none of these". That is a
            # judgement, and it is a better one than the word match: the videos
            # are on the right channel and still are not a lesson on this topic.
            # Reporting them anyway would undo the answer.
            report["rejected"] = len(shortlist)
            return [], report

        # The model could not be reached, or answered something unusable: the
        # word ranking stands.
        return shortlist[:limit], report

    # Feed-only path: everything came from the public feed (newest 15 videos).
    # If every shortlisted hit has a real word match, offer them directly — the
    # learner can see which words decided it. Otherwise there is nothing useful
    # to show.
    if all(hit.matched_terms for hit in shortlist):
        report["selection"] = "words"
        return shortlist[:limit], report

    return [], report