"""Tests for answer scoring — TZ sections 6.2, 6.6 and FR-8.4-8.7."""

import pytest

from services.adaptive_learning.answer_scoring import (
    CRITERIA,
    ERROR_TYPES,
    MAX_HINT_PENALTY,
    average_breakdown,
    hint_penalty,
    is_stable,
    score_answer,
    summarise_errors,
)


PERFECT = {"correctness": 1.0, "completeness": 1.0, "understanding": 1.0}
MID = {"correctness": 0.6, "completeness": 0.6, "understanding": 0.6}


class TestHintPenalty:
    """FR-8.6: three hint levels, each lowering the grade."""

    def test_no_hints_means_no_penalty(self):
        assert hint_penalty(0) == 0.0

    def test_each_hint_costs_twenty_percent(self):
        assert hint_penalty(1) == pytest.approx(0.2)
        assert hint_penalty(2) == pytest.approx(0.4)

    def test_penalty_is_capped(self):
        assert hint_penalty(10) == MAX_HINT_PENALTY

    def test_negative_hints_are_ignored(self):
        assert hint_penalty(-3) == 0.0


class TestFinalScore:
    """Section 6.2: final = score * (1 - hint_penalty)."""

    def test_no_hints_leaves_score_unchanged(self):
        result = score_answer(MID, hints_used=0)
        assert result.raw_score == pytest.approx(0.6)
        assert result.final_score == pytest.approx(0.6)

    def test_one_hint_reduces_score_to_eighty_percent(self):
        result = score_answer(MID, hints_used=1)
        assert result.final_score == pytest.approx(0.6 * 0.8)

    def test_two_hints_reduce_score_to_sixty_percent(self):
        result = score_answer(MID, hints_used=2)
        assert result.final_score == pytest.approx(0.6 * 0.6)

    def test_formula_matches_the_spec(self):
        """final = score * (1 - hint_penalty), stated directly in section 6.2."""
        result = score_answer({"correctness": 0.8, "completeness": 0.7, "understanding": 0.9}, hints_used=2)
        raw = (0.8 + 0.7 + 0.9) / 3
        assert result.final_score == pytest.approx(raw * (1 - 0.4), abs=1e-4)

    def test_final_score_never_exceeds_one(self):
        result = score_answer({k: 5.0 for k in CRITERIA}, hints_used=0)
        assert result.final_score <= 1.0

    def test_out_of_range_values_are_clamped(self):
        result = score_answer({"correctness": -2.0, "completeness": 9.0, "understanding": 0.5})
        assert all(0.0 <= v <= 1.0 for v in result.breakdown.values())

    def test_average_ignores_absent_criteria(self):
        assert average_breakdown({"correctness": 1.0}) == 1.0

    def test_average_of_empty_is_zero(self):
        assert average_breakdown({}) == 0.0

    def test_empty_breakdown_is_reported(self):
        result = score_answer({})
        assert result.final_score == 0.0
        assert result.warnings


class TestCriteriaStability:
    """Section 6.2/12: fixed criteria keep grading within 0.15."""

    def test_missing_criteria_produce_a_warning_not_a_silent_default(self):
        result = score_answer({"correctness": 1.0})
        assert any("missing criteria" in w for w in result.warnings)

    def test_extra_keys_are_dropped(self):
        result = score_answer({**MID, "vibes": 0.1})
        assert "vibes" not in result.breakdown

    def test_two_regrades_within_tolerance_are_stable(self):
        first = score_answer(MID).final_score
        second = score_answer(MID).final_score
        assert is_stable(first, second)

    def test_large_jump_is_flagged_unstable(self):
        assert is_stable(0.5, 0.6) is True
        assert is_stable(0.5, 0.9) is False


class TestErrorTyping:
    """Section 6.6: errors carry a type from the fixed taxonomy."""

    def test_known_types_are_kept(self):
        result = score_answer(MID, reported_errors=["concept_confusion", "missing_step"])
        assert result.error_types == ["concept_confusion", "missing_step"]

    def test_unknown_types_are_filtered_and_reported(self):
        result = score_answer(MID, reported_errors=["concept_confusion", "made_it_up"])
        assert result.error_types == ["concept_confusion"]
        assert any("unknown error types" in w for w in result.warnings)

    def test_every_taxonomy_value_is_accepted(self):
        result = score_answer(MID, reported_errors=list(ERROR_TYPES))
        assert len(result.error_types) == len(ERROR_TYPES)


class TestErrorSummary:
    """FR-12.3: summarise typical mistakes after N checks."""

    def _answers(self, n, score, errors):
        return [{"score": score, "error_types": errors} for _ in range(n)]

    def test_counts_and_dominant_error(self):
        answers = (
            self._answers(3, 0.4, ["concept_confusion"])
            + self._answers(1, 0.8, ["missing_step"])
        )
        summary = summarise_errors(answers)
        assert summary["counts"]["concept_confusion"] == 3
        assert summary["dominant_error"] == "concept_confusion"
        assert summary["window"] == 4

    def test_mean_score_is_computed(self):
        answers = self._answers(2, 0.5, []) + self._answers(2, 1.0, [])
        assert summarise_errors(answers)["mean_score"] == pytest.approx(0.75)

    def test_empty_input_produces_a_calm_observation(self):
        summary = summarise_errors([])
        assert summary["dominant_error"] == ""
        assert "Ошибок пока не зафиксировано" in summary["observation"]

    def test_window_limits_the_lookback(self):
        answers = self._answers(10, 0.9, []) + self._answers(10, 0.3, ["missing_step"])
        summary = summarise_errors(answers, window=10)
        assert summary["window"] == 10
        # The window keeps the *last* 10, which are the recent low scores,
        # so the stale 0.9 answers must not pull the mean back up.
        assert summary["mean_score"] == pytest.approx(0.3)
        assert summary["dominant_error"] == "missing_step"

    def test_observation_names_the_weak_spot(self):
        answers = self._answers(4, 0.45, ["wrong_application"])
        assert "ошибка в применении" in summarise_errors(answers)["observation"]

    def test_evenly_spread_errors_are_reported_as_such(self):
        answers = self._answers(1, 0.6, ["missing_step"]) + self._answers(
            1, 0.6, ["incomplete"]
        ) + self._answers(1, 0.6, ["concept_confusion"]) + self._answers(1, 0.6, ["wrong_application"])
        assert "равномерно" in summarise_errors(answers)["observation"]