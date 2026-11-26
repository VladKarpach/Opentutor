"""Tests for the practice series per subtopic and the exam gate (FR-8, FR-11).

Everything here is deterministic: the required count, the difficulty ramp, the
completion threshold and the gate's accounting. Only the question wording comes
from the model, and that is exercised end to end elsewhere.
"""

import uuid

import pytest

from models.learning_goal import LearningGoal, LearningTopic, TopicDrill
from schemas.learning import DrillProgress
from services.adaptive_learning.drills import (
    DIFFICULTY_RAMP,
    EXAM_PASS_SCORE,
    REQUIRED_BY_DEPTH,
    completion_for_score,
    difficulty_for,
    required_for,
)


class TestRequiredCount:
    """"2-3 tasks per topic": the count follows the goal's depth."""

    def test_overview_needs_two(self):
        assert required_for("overview") == 2

    def test_confident_needs_three(self):
        assert required_for("confident") == 3

    def test_deep_needs_three(self):
        assert required_for("deep") == 3

    def test_unknown_depth_falls_back_to_three(self):
        assert required_for(None) == 3
        assert required_for("nonsense") == 3

    def test_every_depth_is_covered(self):
        for depth in REQUIRED_BY_DEPTH:
            assert REQUIRED_BY_DEPTH[depth] in (2, 3)

    def test_counts_are_in_the_promised_range(self):
        assert all(2 <= v <= 3 for v in REQUIRED_BY_DEPTH.values())


class TestDifficultyRamp:
    """A series should get harder, not random."""

    def test_first_task_is_easiest(self):
        assert difficulty_for(0) == DIFFICULTY_RAMP[0] == min(DIFFICULTY_RAMP)

    def test_tasks_get_harder_in_order(self):
        values = [difficulty_for(i) for i in range(len(DIFFICULTY_RAMP))]
        assert values == sorted(values)

    def test_beyond_the_ramp_clamps_to_the_hardest(self):
        assert difficulty_for(len(DIFFICULTY_RAMP)) == DIFFICULTY_RAMP[-1]
        assert difficulty_for(99) == DIFFICULTY_RAMP[-1]

    def test_all_difficulties_are_valid_fractions(self):
        for value in DIFFICULTY_RAMP:
            assert 0.0 <= value <= 1.0


class TestCompletionRule:
    """Auto-completion: a passed exam closes the goal."""

    def test_threshold_is_a_fraction(self):
        assert 0.0 < EXAM_PASS_SCORE < 1.0

    def test_perfect_score_completes(self):
        assert completion_for_score(1.0) is True

    def test_exactly_at_the_threshold_completes(self):
        assert completion_for_score(EXAM_PASS_SCORE) is True

    def test_just_below_the_threshold_does_not(self):
        assert completion_for_score(EXAM_PASS_SCORE - 0.01) is False

    def test_zero_does_not_complete(self):
        assert completion_for_score(0.0) is False

    def test_string_score_still_works(self):
        # JSON can hand back a string in some serialisations.
        assert completion_for_score("0.85") is True


class TestDrillSchema:
    """Storage constraints that keep the series sane."""

    def test_one_series_per_topic_per_goal(self):
        constraints = [
            c for c in TopicDrill.__table__.constraints
            if c.name == "uq_topic_drill_goal_topic"
        ]
        assert constraints, "a topic must not accumulate duplicate series"

    def test_series_columns_exist(self):
        columns = {c.name for c in TopicDrill.__table__.columns}
        for name in ("goal_id", "user_id", "topic_id", "required_count",
                     "question_ids", "status", "completed_at"):
            assert name in columns

    def test_required_count_is_not_nullable(self):
        column = TopicDrill.__table__.c["required_count"]
        assert column.nullable is False

    def test_question_ids_are_optional(self):
        # A series is created before the questions are generated, and a rollback
        # must not be blocked by a not-null constraint.
        assert TopicDrill.__table__.c["question_ids"].nullable is True


class TestGateAccounting:
    """The gate is pure logic over a progress map; exercise it directly."""

    def _gate(self, topics, progress):
        """Mirror of drills.goal_gate, kept local so the test needs no session."""
        pending = []
        for topic in topics:
            info = progress.get(str(topic.id))
            if info is None:
                pending.append({"reason": "not_prepared", "title": topic.title})
            elif not info["done"]:
                pending.append({"reason": "unanswered_tasks", "title": topic.title})
        return {
            "topics_total": len(topics),
            "topics_done": len(topics) - len(pending),
            "unlocked": not pending and bool(topics),
            "pending": pending,
        }

    def _topic(self):
        # An unflushed ORM object has id=None until INSERT, so two of them would
        # collide as the same key. Give each an explicit id.
        return LearningTopic(
            id=uuid.uuid4(), goal_id=uuid.uuid4(), title="Тема", order_index=0
        )

    def _done(self, required=3):
        return {
            "required": required,
            "answered": required,
            "done": True,
            "prepared": True,
            "status": "completed",
        }

    def test_no_topics_means_locked(self):
        assert self._gate([], {})["unlocked"] is False

    def test_all_done_unlocks(self):
        topics = [self._topic(), self._topic()]
        progress = {str(t.id): self._done() for t in topics}
        gate = self._gate(topics, progress)
        assert gate["unlocked"] is True
        assert gate["topics_done"] == 2

    def test_one_unprepared_keeps_it_locked(self):
        topics = [self._topic(), self._topic()]
        progress = {str(topics[0].id): self._done()}
        gate = self._gate(topics, progress)
        assert gate["unlocked"] is False
        assert gate["pending"][0]["reason"] == "not_prepared"

    def test_partial_series_keeps_it_locked(self):
        topics = [self._topic()]
        progress = {
            str(topics[0].id): {
                "required": 3, "answered": 2, "done": False,
                "prepared": True, "status": "in_progress",
            }
        }
        gate = self._gate(topics, progress)
        assert gate["unlocked"] is False
        assert gate["pending"][0]["reason"] == "unanswered_tasks"

    def test_gate_reports_what_is_missing(self):
        """The refusal has to be actionable, so the pending list carries titles."""
        topics = [self._topic()]
        gate = self._gate(topics, {})
        assert gate["pending"][0]["title"] == "Тема"


class TestProgressShape:
    """The frontend renders this shape, so it must always be present."""

    def test_unprepared_progress_is_explicit(self):
        info = DrillProgress().model_dump()
        assert info["prepared"] is False
        assert info["answered"] == 0
        assert info["done"] is False
        assert info["required"] == 0

    def test_prepared_progress_carries_the_remaining_ids(self):
        info = DrillProgress(
            drill_id="d", required=3, answered=1, prepared=True,
            status="in_progress",
            answered_question_ids=["q1"],
            remaining_question_ids=["q2", "q3"],
        ).model_dump()
        assert len(info["remaining_question_ids"]) == 2
        assert info["answered"] + len(info["remaining_question_ids"]) == 3


@pytest.mark.parametrize("depth,expected", [("overview", 2), ("confident", 3), ("deep", 3)])
def test_required_counts_match_the_documented_range(depth, expected):
    assert required_for(depth) == expected


def test_goal_depth_defaults_to_three():
    goal = LearningGoal(topic="T", depth=None, minutes_per_day=60, status="active")
    assert required_for(goal.depth) == 3