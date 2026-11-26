"""Which topic cards and tests may draw from.

Cards follow the goal rather than the course's chapter tree: the chapter tree is
the base project's own structure, and generating from it meant a learner working
through a goal got questions about material they had never reached.

Two rules, both from how the goal is meant to be worked (FR-5, FR-11):

* before the goal exam is passed, only the current topic produces cards;
* once it is passed, the goal is done and every topic opens.

Grounding is the other half: a topic carries a title and a one-line description,
which is too thin to write factual questions from, so the saved explanation is
preferred and the description is the fallback.
"""

import uuid
from datetime import datetime, timezone

import pytest

from models.learning_goal import Exam, LearningGoal, LearningTopic
from services.adaptive_learning import topic_flow

pytestmark = pytest.mark.asyncio


class _Rows:
    def __init__(self, rows, scalars=None):
        self._rows = rows
        self._scalars = rows if scalars is None else scalars

    def all(self):
        return self._rows

    def scalars(self):
        return _Rows(self._scalars)

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None


class _Session:
    """Replays prepared results in call order."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    async def execute(self, _statement=None):
        self.calls += 1
        # Emptiness, not a count: the list shrinks as results are consumed.
        if not self._results:
            raise AssertionError(f"unexpected extra query (call {self.calls})")
        return self._results.pop(0)


def _topic(title, order, *, desc="A description.", goal_id=uuid.UUID(int=1), manual=None):
    return LearningTopic(
        id=uuid.uuid4(),
        goal_id=goal_id,
        title=title,
        description=desc,
        order_index=order,
        manual_status=manual,
    )


def _exam(score):
    return Exam(id=uuid.uuid4(), status="completed", total_score=score)


def _goal(topic, goal_id):
    return LearningGoal(
        id=goal_id,
        user_id=USER,
        topic=topic,
        status="active",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


USER = uuid.UUID(int=2)
GOAL = uuid.UUID(int=1)


class TestExamPassed:
    async def test_no_exam_means_not_passed(self):
        session = _Session(_Rows([]))
        assert await topic_flow.goal_exam_passed(session, GOAL, USER) is False

    async def test_a_passing_score_counts(self):
        session = _Session(_Rows([(0.85,)]))
        assert await topic_flow.goal_exam_passed(session, GOAL, USER) is True

    async def test_a_failing_score_does_not(self):
        session = _Session(_Rows([(0.4,)]))
        assert await topic_flow.goal_exam_passed(session, GOAL, USER) is False

    async def test_the_exact_mark_passes(self):
        """The bar is inclusive: auto-complete uses >= 0.7, so cards must agree."""
        session = _Session(_Rows([(topic_flow.EXAM_PASS_SCORE,)]))
        assert await topic_flow.goal_exam_passed(session, GOAL, USER) is True

    async def test_any_passing_attempt_counts(self):
        """Failing twice and then passing has passed; a later failure is not a
        reason to take it away again."""
        session = _Session(_Rows([(0.5,), (0.9,), (0.2,)]))
        assert await topic_flow.goal_exam_passed(session, GOAL, USER) is True

    async def test_bar_matches_the_goal_completion_bar(self):
        from services.adaptive_learning.drills import EXAM_PASS_SCORE

        assert topic_flow.EXAM_PASS_SCORE == EXAM_PASS_SCORE


class TestCurrentTopic:
    async def test_starts_at_the_first_topic(self):
        topics = [_topic("A", 0), _topic("B", 1)]
        session = _Session(_Rows(topics), _Rows([]), _Rows([]))
        current, all_topics, passed = await topic_flow.resolve_current_topic(
            session, GOAL, USER
        )
        assert current.title == "A"
        assert len(all_topics) == 2
        assert passed is False

    async def test_skips_a_topic_marked_known(self):
        topics = [_topic("A", 0), _topic("B", 1)]
        session = _Session(
            _Rows(topics),
            _Rows([(topics[0].id, "known")]),
            _Rows([]),
        )
        current, _, _ = await topic_flow.resolve_current_topic(session, GOAL, USER)
        assert current.title == "B"

    async def test_skips_a_topic_marked_skip(self):
        topics = [_topic("A", 0, manual="skip"), _topic("B", 1)]
        session = _Session(_Rows(topics), _Rows([]), _Rows([]))
        current, _, _ = await topic_flow.resolve_current_topic(session, GOAL, USER)
        assert current.title == "B"

    async def test_a_learning_topic_is_not_covered(self):
        """In progress is still the current topic, not a skipped one."""
        topics = [_topic("A", 0), _topic("B", 1)]
        session = _Session(
            _Rows(topics),
            _Rows([(topics[0].id, "learning")]),
            _Rows([]),
        )
        current, _, _ = await topic_flow.resolve_current_topic(session, GOAL, USER)
        assert current.title == "A"

    async def test_exam_passed_leaves_no_current_topic(self):
        """The goal is finished; reporting a topic would keep suggesting work."""
        topics = [_topic("A", 0), _topic("B", 1)]
        session = _Session(_Rows(topics), _Rows([]), _Rows([(0.9,)]))
        current, all_topics, passed = await topic_flow.resolve_current_topic(
            session, GOAL, USER
        )
        assert current is None
        assert passed is True
        assert len(all_topics) == 2

    async def test_everything_covered_but_no_exam_holds_at_the_last(self):
        topics = [_topic("A", 0), _topic("B", 1)]
        session = _Session(
            _Rows(topics),
            _Rows([(topics[0].id, "known"), (topics[1].id, "known")]),
            _Rows([]),
        )
        current, _, passed = await topic_flow.resolve_current_topic(
            session, GOAL, USER
        )
        assert current.title == "B"
        assert passed is False

    async def test_no_topics(self):
        session = _Session(_Rows([]))
        current, topics, passed = await topic_flow.resolve_current_topic(
            session, GOAL, USER
        )
        assert current is None
        assert topics == []
        assert passed is False


class TestTopicsInScope:
    async def test_only_the_current_topic_before_the_exam(self):
        topics = [_topic("A", 0), _topic("B", 1), _topic("C", 2)]
        session = _Session(_Rows(topics), _Rows([]), _Rows([]))
        scope = await topic_flow.topics_in_scope(session, GOAL, USER)
        assert [t.title for t in scope] == ["A"]

    async def test_every_topic_after_the_exam(self):
        """This is the 'everything else opens once you pass' rule."""
        topics = [_topic("A", 0), _topic("B", 1), _topic("C", 2)]
        session = _Session(_Rows(topics), _Rows([]), _Rows([(0.95,)]))
        scope = await topic_flow.topics_in_scope(session, GOAL, USER)
        assert [t.title for t in scope] == ["A", "B", "C"]

    async def test_a_skipped_topic_never_produces_cards(self):
        topics = [_topic("A", 0), _topic("B", 1)]
        session = _Session(_Rows(topics), _Rows([]), _Rows([(0.9,)]))
        scope = await topic_flow.topics_in_scope(session, GOAL, USER)
        assert [t.title for t in scope] == ["A", "B"]

    async def test_no_topics_yields_no_scope(self):
        session = _Session(_Rows([]))
        assert await topic_flow.topics_in_scope(session, GOAL, USER) == []


class TestTopicMaterial:
    async def test_prefers_the_saved_explanation(self):
        topic = _topic("Scope", 0)
        session = _Session(_Rows([{"text": "Local and global scope explained."}]))
        text, source = await topic_flow.topic_material(session, topic, USER)
        assert source == "explanation"
        assert "Local and global scope explained." in text

    async def test_falls_back_to_the_description(self):
        topic = _topic("Scope", 0, desc="Local vs global variables.")
        session = _Session(_Rows([None]))
        text, source = await topic_flow.topic_material(session, topic, USER)
        assert source == "description"
        assert "Local vs global variables." in text

    async def test_the_fallback_warns_the_model(self):
        """Without this the model treats a one-line description as a source and
        invents the specifics, which is the original complaint."""
        topic = _topic("Scope", 0, desc="Local vs global variables.")
        session = _Session(_Rows([None]))
        text, _ = await topic_flow.topic_material(session, topic, USER)
        assert "do not add specifics" in text

    async def test_a_blank_explanation_falls_back(self):
        topic = _topic("Scope", 0, desc="Local vs global.")
        session = _Session(_Rows([{"text": "   "}]))
        text, source = await topic_flow.topic_material(session, topic, USER)
        assert source == "description"
        assert "Local vs global." in text

    async def test_handles_a_bare_string_explanation(self):
        topic = _topic("Scope", 0)
        session = _Session(_Rows(["Plain text note."]))
        text, source = await topic_flow.topic_material(session, topic, USER)
        assert source == "explanation"
        assert "Plain text note." in text

    async def test_a_topic_with_nothing_reports_no_source(self):
        """A bare title must be refused, not prompted on: the model answers by
        asking about the title itself."""
        topic = _topic("Integrals", 0, desc=None)
        session = _Session(_Rows([None]))
        text, source = await topic_flow.topic_material(session, topic, USER)
        assert source == "none"
        assert text == ""

    async def test_no_markdown_heading_in_the_payload(self):
        """A literal '## Title' in the body reads as part of a document, and the
        model then asks about heading levels instead of the subject."""
        topic = _topic("Scope", 0, desc="Local vs global.")
        session = _Session(_Rows([{"text": "Scope explained at length."}]))
        text, _ = await topic_flow.topic_material(session, topic, USER)
        assert "##" not in text

    async def test_a_description_counts_as_usable(self):
        """The bar is a description, not an explanation: refusing every thin topic
        would leave most of a fresh goal unable to produce cards."""
        assert topic_flow.MIN_USABLE_SOURCE == "description"


class TestPickCurrentTopic:
    def test_takes_the_first_uncovered_topic(self):
        a, b, c = _topic("A", 0), _topic("B", 1), _topic("C", 2)
        assert topic_flow.pick_current_topic([a, b, c], {a.id}).title == "B"

    def test_skips_marked_skipped(self):
        a, b = _topic("A", 0, manual="skip"), _topic("B", 1)
        assert topic_flow.pick_current_topic([a, b], set()).title == "B"

    def test_all_covered_returns_the_last(self):
        a, b = _topic("A", 0), _topic("B", 1)
        assert topic_flow.pick_current_topic([a, b], {a.id, b.id}).title == "B"

    def test_no_topics_is_none(self):
        assert topic_flow.pick_current_topic([], set()) is None


class TestGoalProgress:
    """Progress shown next to a goal on the course page."""

    G = uuid.UUID(int=1)
    H = uuid.UUID(int=2)

    def _topic_row(self, goal_id, title, order):
        return (goal_id, uuid.uuid4(), title, order, None)

    async def test_counts_topics_and_done(self):
        rows = [
            self._topic_row(self.G, "A", 0),
            self._topic_row(self.G, "B", 1),
            self._topic_row(self.G, "C", 2),
        ]
        session = _Session(
            _Rows(rows),
            _Rows([(self.G, rows[0][1], "known"), (self.G, rows[1][1], "learning")]),
            _Rows([]),
        )
        p = (await topic_flow.course_goal_progress(
            session, USER, [self.G]
        ))[self.G]
        assert p.topics_total == 3
        assert p.topics_done == 1
        assert p.progress_percent == 33

    async def test_reports_no_exam_by_default(self):
        session = _Session(
            _Rows([self._topic_row(self.G, "A", 0)]), _Rows([]), _Rows([])
        )
        p = (await topic_flow.course_goal_progress(session, USER, [self.G]))[self.G]
        assert p.exam_status == "none"
        assert p.exam_passed is False
        assert p.current_topic_title == "A"

    async def test_an_exam_in_progress_is_visible(self):
        session = _Session(
            _Rows([self._topic_row(self.G, "A", 0)]),
            _Rows([]),
            _Rows([(self.G, "in_progress", None)]),
        )
        p = (await topic_flow.course_goal_progress(session, USER, [self.G]))[self.G]
        assert p.exam_status == "in_progress"

    async def test_a_passed_exam_reports_full_and_clears_the_topic(self):
        session = _Session(
            _Rows([self._topic_row(self.G, "A", 0)]),
            _Rows([]),
            _Rows([(self.G, "completed", 0.82)]),
        )
        p = (await topic_flow.course_goal_progress(session, USER, [self.G]))[self.G]
        assert p.exam_passed is True
        assert p.progress_percent == 100
        # The goal is finished, so there is no next topic to point at.
        assert p.current_topic_title is None

    async def test_a_failed_exam_is_not_a_pass(self):
        session = _Session(
            _Rows([self._topic_row(self.G, "A", 0)]),
            _Rows([]),
            _Rows([(self.G, "completed", 0.4)]),
        )
        p = (await topic_flow.course_goal_progress(session, USER, [self.G]))[self.G]
        assert p.exam_passed is False
        assert p.progress_percent == 0

    async def test_the_best_attempt_wins(self):
        """Failed, passed, failed later: the goal is passed."""
        session = _Session(
            _Rows([self._topic_row(self.G, "A", 0)]),
            _Rows([]),
            _Rows([
                (self.G, "completed", 0.3),
                (self.G, "completed", 0.9),
                (self.G, "completed", 0.5),
            ]),
        )
        p = (await topic_flow.course_goal_progress(session, USER, [self.G]))[self.G]
        assert p.exam_score == 0.9
        assert p.exam_passed is True

    async def test_the_current_topic_skips_covered_ones(self):
        rows = [
            self._topic_row(self.G, "A", 0),
            self._topic_row(self.G, "B", 1),
        ]
        session = _Session(
            _Rows(rows),
            _Rows([(self.G, rows[0][1], "known")]),
            _Rows([]),
        )
        p = (await topic_flow.course_goal_progress(session, USER, [self.G]))[self.G]
        assert p.current_topic_title == "B"
        assert p.topics_done == 1

    async def test_goals_without_topics_report_zero(self):
        session = _Session(_Rows([]), _Rows([]), _Rows([]))
        p = (await topic_flow.course_goal_progress(session, USER, [self.G]))[self.G]
        assert p.topics_total == 0
        assert p.progress_percent == 0
        assert p.current_topic_title is None

    async def test_several_goals_are_answered_together(self):
        """The point of the batch: three queries, not three per goal."""
        rows = [
            self._topic_row(self.G, "A", 0),
            self._topic_row(self.H, "X", 0),
        ]
        session = _Session(
            _Rows(rows),
            _Rows([(self.G, rows[0][1], "known")]),
            _Rows([]),
        )
        got = await topic_flow.course_goal_progress(session, USER, [self.G, self.H])
        assert got[self.G].topics_done == 1
        assert got[self.H].topics_done == 0
        assert session.calls == 3

    async def test_no_goals_asks_nothing(self):
        session = _Session()
        assert await topic_flow.course_goal_progress(session, USER, []) == {}
        assert session.calls == 0


class TestActiveGoalsForCourse:
    async def test_lists_the_working_goals(self):
        session = _Session(_Rows(["goal-a"]))
        goals = await topic_flow.active_goals_for_course(
            session, USER, uuid.UUID(int=3)
        )
        assert len(goals) == 1

    async def test_courses_without_goals_return_nothing(self):
        session = _Session(_Rows([]))
        assert await topic_flow.active_goals_for_course(
            session, USER, uuid.UUID(int=3)
        ) == []

    async def test_goals_with_topics_come_first(self):
        """A goal still waiting for its topics cannot produce cards, and it is
        often the oldest row in the course — picking it by date alone meant
        generating nothing while a real goal sat behind it."""
        empty = _goal("Empty goal", uuid.UUID(int=10))
        usable = _goal("Usable goal", uuid.UUID(int=11))
        session = _Session(_Rows([empty, usable]), _Rows([(usable.id,)]))
        goals = await topic_flow.active_goals_for_course(
            session, USER, uuid.UUID(int=3)
        )
        assert goals[0].id == usable.id
        assert goals[-1].id == empty.id

    async def test_all_goals_are_kept_when_none_has_topics(self):
        a = _goal("A", uuid.UUID(int=10))
        b = _goal("B", uuid.UUID(int=11))
        session = _Session(_Rows([a, b]), _Rows([]))
        goals = await topic_flow.active_goals_for_course(
            session, USER, uuid.UUID(int=3)
        )
        # Still returned, so the UI can say "topics not generated yet" rather
        # than "you have no goal".
        assert len(goals) == 2


class TestGoalsForCourse:
    """The goal picker both the cards and the quiz read from."""

    async def test_returns_every_goal_including_finished_ones(self):
        # active_goals_for_course drops completed goals. The picker must not: a
        # learner revising an earlier goal still needs to reach it.
        active = _goal("Current", uuid.UUID(int=10))
        done = _goal("Finished", uuid.UUID(int=11))
        done.status = "completed"
        # Two goals means the ordering query runs as well.
        session = _Session(_Rows([active, done]), _Rows([(active.id, 2)]))
        goals = await topic_flow.goals_for_course(session, USER, uuid.UUID(int=3))
        assert {g.id for g in goals} == {active.id, done.id}

    async def test_goals_with_topics_come_first(self):
        empty = _goal("Empty goal", uuid.UUID(int=10))
        usable = _goal("Usable goal", uuid.UUID(int=11))
        session = _Session(
            _Rows([empty, usable]), _Rows([(usable.id, 4)])
        )
        goals = await topic_flow.goals_for_course(session, USER, uuid.UUID(int=3))
        assert goals[0].id == usable.id
        assert goals[-1].id == empty.id

    async def test_single_goal_needs_no_second_query(self):
        # With one goal there is nothing to order, so the topic-count query is
        # skipped rather than run for a result that cannot change anything.
        session = _Session(_Rows([_goal("Only", uuid.UUID(int=10))]))
        assert len(await topic_flow.goals_for_course(session, USER, uuid.UUID(int=3))) == 1
        assert session.calls == 1


class TestTopicCountsForGoals:
    """One query for the counts the goal picker shows beside each goal."""

    async def test_no_goals_means_no_query(self):
        session = _Session()
        assert await topic_flow.topic_counts_for_goals(session, []) == {}
        assert session.calls == 0

    async def test_maps_goal_to_its_count(self):
        a, b = uuid.UUID(int=1), uuid.UUID(int=2)
        session = _Session(_Rows([(a, 3), (b, 7)]))
        assert await topic_flow.topic_counts_for_goals(session, [a, b]) == {a: 3, b: 7}
        assert session.calls == 1

    async def test_goals_without_topics_are_absent_from_the_map(self):
        # Absent rather than zero: the caller reads a missing key as "no topics",
        # and a fabricated 0 row would cost a query per goal.
        a = uuid.UUID(int=1)
        session = _Session(_Rows([(a, 2)]))
        counts = await topic_flow.topic_counts_for_goals(session, [a, uuid.UUID(int=2)])
        assert counts == {a: 2}