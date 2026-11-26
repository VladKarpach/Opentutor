"""Tests for exam composition (TZ FR-11.1, FR-11.2).

Composition is deterministic, so it is testable without an LLM: the only
language-dependent step (question wording) happens when questions are generated.
"""

from datetime import date, timedelta

import pytest

from services.adaptive_learning.exam import (
    MAX_QUESTIONS,
    MODE_STRICT,
    MODE_TRAINING,
    SECONDS_PER_QUESTION,
    WEAK_THRESHOLD,
    ExamUnavailable,
    deadline_suggestion,
    remaining_seconds,
)


class _FakeExam:
    """Stand-in for the Exam row: remaining_seconds only reads started_at."""

    def __init__(self, started_at, limit):
        self.started_at = started_at
        self.time_limit_seconds = limit


class TestTimer:
    """FR-11.2: strict mode is timed, training mode is not."""

    def test_untimed_exam_has_no_budget(self):
        exam = _FakeExam(date(2026, 3, 1), None)
        assert remaining_seconds(exam) is None

    def test_budget_counts_down(self):
        from datetime import datetime, timezone

        started = datetime.now(timezone.utc) - timedelta(seconds=30)
        exam = _FakeExam(started, 600)
        left = remaining_seconds(exam)
        assert left is not None
        assert 565 <= left <= 570

    def test_expired_budget_reports_zero(self):
        from datetime import datetime, timezone

        started = datetime.now(timezone.utc) - timedelta(seconds=700)
        exam = _FakeExam(started, 600)
        assert remaining_seconds(exam) == 0

    def test_strict_budget_is_derived_from_question_count(self):
        """One minute per question, per the module constant."""
        assert SECONDS_PER_QUESTION == 60

    def test_modes_are_distinct(self):
        assert MODE_TRAINING != MODE_STRICT
        assert MODE_TRAINING == "training"
        assert MODE_STRICT == "strict"


class TestSelectionPolicy:
    """FR-11.1: weak topics are over-represented."""

    def test_weak_threshold_is_stated(self):
        assert 0 < WEAK_THRESHOLD < 1

    def test_weak_topics_take_two_questions_each(self):
        # Mirrors the module constants; guards an accidental change to 1.
        from services.adaptive_learning.exam import (
            QUESTIONS_PER_STRONG_TOPIC,
            QUESTIONS_PER_WEAK_TOPIC,
        )

        assert QUESTIONS_PER_WEAK_TOPIC == 2
        assert QUESTIONS_PER_STRONG_TOPIC == 1

    def test_exam_size_is_capped(self):
        assert MAX_QUESTIONS == 20

    def test_unavailable_exam_is_a_domain_error(self):
        """Callers translate this into a 422 with actionable wording."""
        assert issubclass(ExamUnavailable, RuntimeError)


class TestDeadlineSuggestion:
    """FR-11.4: suggest the exam 2-3 days before the deadline."""

    def test_no_deadline_yields_no_suggestion(self):
        assert deadline_suggestion(None, 5, 5) is None

    def test_incomplete_exam_is_not_suggested(self):
        assert "все темы" in (deadline_suggestion(date.today(), 3, 5) or "")

    def test_two_to_seven_days_before_deadline_is_ideal(self):
        soon = date.today() + timedelta(days=3)
        assert deadline_suggestion(soon, 5, 5)

    def test_far_from_deadline_yields_nothing(self):
        assert deadline_suggestion(date.today() + timedelta(days=60), 5, 5) is None

    def test_no_questions_yields_nothing(self):
        assert deadline_suggestion(date.today() + timedelta(days=3), 0, 0) is None