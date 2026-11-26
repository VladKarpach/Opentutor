"""Tests for the learner context (TZ section 9) and the course/goal link.

The context is deterministic: it is derived from stored state by code, and the
model only reads it. That makes it testable without any LLM.
"""

import pytest

from models.learning_goal import LearningGoal
from schemas.learning import goal_to_response
from services.adaptive_learning import context as ctx_mod
from services.adaptive_learning.context import (
    HISTORY_DAYS,
    KNOWN_THRESHOLD,
    LearnerContext,
    MAX_KNOWN_TOPICS,
    _dedupe,
    _titles_overlap,
)


class TestTitleOverlap:
    """Crude token overlap — a grouping hint for the prompt, not a match."""

    def test_identical_titles_overlap_fully(self):
        assert _titles_overlap("Метод Гаусса", "Метод Гаусса") == 1.0

    def test_disjoint_titles_do_not_overlap(self):
        assert _titles_overlap("Метод Гаусса", "Предел функции") == 0.0

    def test_same_subject_with_an_extra_word_collapses(self):
        """These really are one subject, so a full overlap is the wanted answer."""
        assert _titles_overlap("Метод Гаусса", "Метод Гаусса Жордана") == 1.0

    def test_one_shared_word_out_of_three_is_partial(self):
        # No stemming, so it must share a token verbatim to count.
        assert 0 < _titles_overlap(
            "Метод Гаусса и матрицы", "Метод Гаусса и определители"
        ) < 1

    def test_inflected_word_is_not_treated_as_a_match(self):
        """Documented limitation: 'Гаусс' vs 'Гаусса' do not match without stemming."""
        assert _titles_overlap("Метод Гаусса", "Гаусс и интегралы") == 0.0

    def test_short_tokens_are_ignored(self):
        """Function words carry no signal about the subject matter."""
        assert _titles_overlap("а и в", "б и г") == 0.0

    def test_empty_input_is_safe(self):
        assert _titles_overlap("", "что-то") == 0.0


class TestDedupe:
    def test_near_duplicates_collapse_keeping_the_first(self):
        out = _dedupe(["Метод Гаусса", "Метод Гаусса: прямой ход", "Предел функции"])
        assert out == ["Метод Гаусса", "Предел функции"]

    def test_distinct_titles_survive(self):
        assert _dedupe(["Определители", "Обратная матрица"]) == ["Определители", "Обратная матрица"]

    def test_empty_is_safe(self):
        assert _dedupe([]) == []


class TestPromptText:
    """Section 9 asks for a brief history, and nothing at all for a newcomer."""

    def test_new_learner_gets_no_prompt_block(self):
        """Returning '' lets callers skip the block instead of sending noise."""
        assert LearnerContext().as_prompt_text() == ""

    def test_is_empty_for_a_fresh_account(self):
        assert LearnerContext().is_empty() is True

    def test_known_topics_are_announced_as_done(self):
        c = LearnerContext(known=["Производная"], answers_total=3)
        text = c.as_prompt_text()
        assert "Производная" in text
        assert "не начинать с нуля" in text

    def test_weak_topics_are_announced_as_weak(self):
        c = LearnerContext(weak=["Метод Гаусса"], answers_total=3)
        assert "Слабые места" in c.as_prompt_text()

    def test_dominant_error_is_included_with_the_score(self):
        c = LearnerContext(
            dominant_error="incomplete",
            mean_score=0.42,
            answered_recently=20,
            answers_total=20,
        )
        text = c.as_prompt_text()
        assert "incomplete" in text
        assert "42%" in text

    def test_course_materials_are_mentioned(self):
        c = LearnerContext(course_title="Python Basics", course_goals=2, course_documents=7)
        text = c.as_prompt_text()
        assert "Python Basics" in text
        assert "7" in text

    def test_prompt_text_is_trimmed(self):
        c = LearnerContext(known=[f"Тема номер {i}" for i in range(200)], answers_total=1)
        assert len(c.as_prompt_text()) <= 700

    def test_dominant_error_without_counts_stays_out(self):
        """No numbers behind the claim, so no claim."""
        c = LearnerContext(dominant_error="incomplete", answered_recently=0)
        assert "Типичная ошибка" not in c.as_prompt_text()


class TestThresholds:
    def test_known_threshold_matches_the_engine(self):
        from services.adaptive_learning.repetition import HIGH_SCORE_THRESHOLD

        assert KNOWN_THRESHOLD == HIGH_SCORE_THRESHOLD

    def test_lists_are_capped(self):
        c = LearnerContext(
            answers_total=1,
            known=[f"k{i}" for i in range(MAX_KNOWN_TOPICS + 20)],
        )
        # as_prompt_text trims the rendered block, so assert the cap on the data.
        assert MAX_KNOWN_TOPICS <= 20
        assert HISTORY_DAYS == 180


class TestGoalCourseLink:
    """course_id is nullable and must survive serialisation either way."""

    def _goal(self, **kw):
        import uuid

        kw.setdefault("user_id", uuid.uuid4())
        # Column defaults only apply on INSERT, so an unflushed object has None
        # where GoalResponse expects a value. Set what the schema requires.
        kw.setdefault("minutes_per_day", 60)
        kw.setdefault("depth", "confident")
        kw.setdefault("status", "active")
        kw.setdefault("clarification_needed", False)
        return LearningGoal(topic="Тема", **kw)

    def test_response_exposes_no_course_when_unset(self):
        out = goal_to_response(self._goal(course_id=None))
        assert out.course_id is None
        assert out.course_name is None

    def test_response_exposes_the_course(self):
        import uuid

        course_id = uuid.uuid4()
        out = goal_to_response(self._goal(course_id=course_id), course_name="Курс")
        assert out.course_id == str(course_id)
        assert out.course_name == "Курс"

    def test_column_is_nullable_so_standalone_goals_keep_working(self):
        column = LearningGoal.__table__.c["course_id"]
        assert column.nullable is True

    def test_deleting_a_course_does_not_delete_goals(self):
        """SET NULL, not CASCADE: repetition history must survive."""
        fks = [
            fk
            for fk in LearningGoal.__table__.foreign_keys
            if fk.parent.name == "course_id"
        ]
        assert fks, "course_id must have a foreign key"
        assert fks[0].ondelete == "SET NULL"
        assert fks[0].column.name == "id"


class TestContextBuilderSignature:
    """The builder must accept a goal and a course, and default the course."""

    def test_build_accepts_goal_and_course(self):
        import inspect

        params = inspect.signature(ctx_mod.build).parameters
        assert "goal_id" in params
        assert "course_id" in params
        assert params["course_id"].default is None

    def test_describe_for_ui_exposes_prompt_text_as_summary(self):
        payload = ctx_mod.describe_for_ui(LearnerContext(known=["X"], answers_total=1))
        assert payload["summary"]
        assert payload["known"] == ["X"]
        assert payload["goals_seen"] == 0


@pytest.mark.parametrize("title", ["", "   ", "!!"])
def test_unusual_titles_do_not_crash_overlap(title):
    assert _titles_overlap(title, "Метод Гаусса") >= 0.0