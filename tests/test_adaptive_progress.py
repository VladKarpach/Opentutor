"""Tests for progress history and error analysis (TZ FR-12.1, FR-12.3).

The counting half of section 6.6 is deterministic, so it is tested directly. The
model only supplies wording, which is exercised end to end elsewhere.
"""

from datetime import datetime, timezone

import pytest

from services.adaptive_learning import answer_scoring
from services.adaptive_learning.error_analysis import (
    ANALYSIS_WINDOW,
    SUMMARISE_EVERY,
    WEAK_TOPIC_SCORE,
    _average_curve,
    _per_topic_weakness,
)


class _Point:
    """Minimal stand-in for TopicStrengthPoint: _average_curve reads 3 fields."""

    def __init__(self, strength, recorded_at, was_lapse=False):
        self.strength = strength
        self.recorded_at = recorded_at
        self.was_lapse = was_lapse


def _at(day: int) -> datetime:
    return datetime(2026, 3, day, 12, 0, tzinfo=timezone.utc)


class TestAverageCurve:
    """FR-12.1: the headline line averages the topics at each moment."""

    def test_single_point_passes_through(self):
        curve = _average_curve([_Point(0.4, _at(1))])
        assert len(curve) == 1
        assert curve[0]["strength"] == 0.4
        assert curve[0]["samples"] == 1

    def test_points_shared_in_time_are_averaged(self):
        """A burst of answers across topics is one step, not a sawtooth."""
        curve = _average_curve(
            [_Point(0.2, _at(1)), _Point(0.6, _at(1)), _Point(0.4, _at(1))]
        )
        assert len(curve) == 1
        assert curve[0]["strength"] == pytest.approx(0.4)
        assert curve[0]["samples"] == 3

    def test_curve_is_ordered_by_time(self):
        curve = _average_curve(
            [_Point(0.9, _at(3)), _Point(0.1, _at(1)), _Point(0.5, _at(2))]
        )
        assert [p["at"] for p in curve] == [_at(1).isoformat(), _at(2).isoformat(), _at(3).isoformat()]

    def test_lapses_are_counted_per_step(self):
        curve = _average_curve(
            [_Point(0.5, _at(1), was_lapse=True), _Point(0.4, _at(1))]
        )
        assert curve[0]["lapses"] == 1

    def test_strength_is_rounded_to_three_places(self):
        curve = _average_curve([_Point(1 / 3, _at(1))])
        assert curve[0]["strength"] == 0.333

    def test_empty_input_gives_empty_curve(self):
        assert _average_curve([]) == []


class TestPerTopicWeakness:
    """FR-12.3: name the weak topic, not just a number."""

    def _answers(self):
        return [
            {"topic_id": "t1", "title": "Матрицы", "score": 0.2,
             "error_types": ["concept_confusion", "concept_confusion", "missing_step"]},
            {"topic_id": "t1", "title": "Матрицы", "score": 0.4,
             "error_types": ["concept_confusion"]},
            {"topic_id": "t2", "title": "Векторы", "score": 0.95, "error_types": []},
        ]

    def test_topics_are_sorted_weakest_first(self):
        out = _per_topic_weakness(self._answers())
        assert [e["topic_id"] for e in out] == ["t1", "t2"]

    def test_mean_score_is_computed(self):
        out = _per_topic_weakness(self._answers())
        assert out[0]["mean_score"] == pytest.approx(0.3)

    def test_dominant_error_is_the_most_frequent(self):
        out = _per_topic_weakness(self._answers())
        assert out[0]["dominant_error"] == "concept_confusion"

    def test_weak_flag_follows_the_threshold(self):
        out = _per_topic_weakness(self._answers())
        assert out[0]["weak"] is True
        assert out[1]["weak"] is False

    def test_topic_without_errors_has_no_dominant(self):
        out = _per_topic_weakness(self._answers())
        assert out[1]["dominant_error"] == ""

    def test_empty_input_gives_empty_list(self):
        assert _per_topic_weakness([]) == []


class TestCadence:
    """Section 6.6: "раз в N проверок" — N must be a small positive number."""

    def test_summarise_every_is_sane(self):
        assert 1 < SUMMARISE_EVERY <= 10

    def test_analysis_window_covers_at_least_a_full_cadence(self):
        assert ANALYSIS_WINDOW >= SUMMARISE_EVERY

    def test_weak_topic_threshold_matches_the_engine(self):
        from services.adaptive_learning.repetition import MEDIUM_SCORE_THRESHOLD

        assert WEAK_TOPIC_SCORE == MEDIUM_SCORE_THRESHOLD


class TestSummaryCadence:
    """Section 6.6 cadence, including the very first summary.

    Regression: with no stored summary there is no timestamp to measure from, so
    an earlier version always reported 0 and the first summary could never fire.
    """

    def test_first_summary_counts_every_answer(self):
        import inspect

        from services.adaptive_learning import error_analysis

        source = inspect.getsource(error_analysis.answers_since_last_summary)
        # The count query is built first, and only filtered by time when a
        # summary already exists.
        assert "if newest is not None:" in source
        assert "return 0" not in source

    def test_is_due_is_a_threshold_check(self):
        import inspect

        from services.adaptive_learning import error_analysis

        source = inspect.getsource(error_analysis.is_due)
        assert "SUMMARISE_EVERY" in source
        assert ">=" in source


class TestStatsShape:
    """The counts handed to the model must always carry the taxonomy keys."""

    def test_counts_include_every_error_type(self):
        summary = answer_scoring.summarise_errors([])
        assert set(summary["counts"]) == set(answer_scoring.ERROR_TYPES)

    def test_no_answers_gives_zeroed_counts(self):
        summary = answer_scoring.summarise_errors([])
        assert summary["counts"] == {key: 0 for key in answer_scoring.ERROR_TYPES}
        assert summary["mean_score"] == 0.0
        assert summary["dominant_error"] == ""

    def test_window_keeps_the_most_recent_answers(self):
        answers = [
            {"score": 0.1, "error_types": ["incomplete"]} for _ in range(ANALYSIS_WINDOW)
        ] + [{"score": 1.0, "error_types": []}]
        summary = answer_scoring.summarise_errors(answers, window=ANALYSIS_WINDOW)
        # The last (perfect) answer is inside the window, so the mean rises.
        assert summary["mean_score"] > 0.05

    def test_unknown_error_types_do_not_pollute_counts(self):
        summary = answer_scoring.summarise_errors(
            [{"score": 0.5, "error_types": ["not_a_real_type"]}]
        )
        assert summary["counts"]["missing_step"] == 0
        assert sum(summary["counts"].values()) == 0