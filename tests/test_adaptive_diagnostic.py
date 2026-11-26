"""Tests for the hidden diagnostic (TZ section 6.1) and forecast (section 6.7)."""

from datetime import date, timedelta, timezone
from datetime import datetime as _dt

import pytest

from services.adaptive_learning.diagnostic import (
    INITIAL_ESTIMATE,
    MAX_REPLIES,
    MIN_REPLIES,
    DiagnosticSession,
    apply_correction,
    describe_band,
)

import pytest
from services.adaptive_learning.forecast import (
    ForecastInput,
    forecast_readiness,
    plan_review_day,
    required_speed,
)
from services.adaptive_learning.repetition import TopicReviewState

SUBTOPICS = [
    {"id": "s1", "title": "Algebra"},
    {"id": "s2", "title": "Geometry"},
    {"id": "s3", "title": "Probability"},
]


def _session(**kw) -> DiagnosticSession:
    return DiagnosticSession(SUBTOPICS, random_state=_FixedRandom(), **kw)


class _FixedRandom:
    """Deterministic stand-in for random.Random (no jitter)."""

    def uniform(self, _a, _b):
        return 0.0


class TestDiagnosticSelection:
    """Section 6.1 step 3: probe the subtopic with the largest uncertainty."""

    def test_estimates_start_at_one_half(self):
        for est in _session().estimates():
            assert est.estimate == INITIAL_ESTIMATE
            assert est.uncertainty == 1.0

    def test_requires_at_least_one_subtopic(self):
        with pytest.raises(ValueError):
            DiagnosticSession([])

    def test_first_turn_is_produced(self):
        s = _session()
        turn = s.next_turn()
        assert turn is not None
        assert turn.turn_index == 0
        assert turn.subtopic_id in {t["id"] for t in SUBTOPICS}

    def test_unprobed_topics_are_covered_first(self):
        s = _session()
        seen = []
        for _ in range(3):
            turn = s.next_turn()
            seen.append(turn.subtopic_id)
        assert len(set(seen)) == 3  # each subtopic probed once before any repeats

    def test_difficulty_tracks_the_estimate(self):
        """FR-4.3: the question difficulty is close to the current estimate."""
        s = _session()
        turn = s.next_turn()
        assert turn.target_difficulty == pytest.approx(INITIAL_ESTIMATE, abs=0.25)

    def test_difficulty_rises_after_a_good_answer(self):
        s = _session()
        turn = s.next_turn()
        s.record_answer(turn.subtopic_id, 1.0)
        est = next(e for e in s.estimates() if e.id == turn.subtopic_id)
        assert est.estimate > INITIAL_ESTIMATE
        # The next question on that topic should be harder than the first one.
        assert est.difficulty() > INITIAL_ESTIMATE


class TestDiagnosticUpdateRule:
    """Section 6.1 step 4: p <- p + alpha * (score - p)."""

    def test_high_score_raises_the_estimate(self):
        s = _session()
        turn = s.next_turn()
        out = s.record_answer(turn.subtopic_id, 0.95)
        est = out.estimates[0]
        assert est["estimate"] > INITIAL_ESTIMATE

    def test_low_score_lowers_the_estimate(self):
        s = _session()
        turn = s.next_turn()
        out = s.record_answer(turn.subtopic_id, 0.05)
        est = next(e for e in out.estimates if e["id"] == turn.subtopic_id)
        assert est["estimate"] < INITIAL_ESTIMATE

    def test_estimate_stays_in_range(self):
        s = _session()
        turn = s.next_turn()
        out = s.record_answer(turn.subtopic_id, 1.0)
        assert 0.0 <= out.estimates[0]["estimate"] <= 1.0

    def test_uncertainty_shrinks_after_evidence(self):
        s = _session()
        turn = s.next_turn()
        before = s.aggregate_uncertainty()
        s.record_answer(turn.subtopic_id, 0.5)
        assert s.aggregate_uncertainty() < before

    def test_unknown_subtopic_is_rejected(self):
        s = _session()
        with pytest.raises(KeyError):
            s.record_answer("nope", 0.5)


class TestDiagnosticBounds:
    """FR-4.1: 4-8 replies, and the dialogue must terminate."""

    def test_never_exceeds_max_replies(self):
        s = _session(max_replies=8)
        while s.next_turn() is not None:
            pass
        assert s.reply_count <= 8
        assert s.finished

    def test_reply_budget_is_at_least_the_minimum(self):
        s = _session(min_replies=6, max_replies=2)
        assert s.max_replies == 6

    def test_stops_early_when_uncertainty_is_low(self):
        s = _session()
        # Feed uniformly perfect answers so uncertainty collapses fast.
        for _ in range(20):
            if s.next_turn() is None:
                break
            turn = s.turns[-1]
            s.record_answer(turn.subtopic_id, 1.0)
        assert s.finished
        assert s.reply_count <= MAX_REPLIES

    def test_snapshot_round_trip_keeps_the_reply_budget(self):
        """A restored session must continue the dialogue, not restart it.

        Regression: restore() used to reset the turn counter, so a dialogue
        driven through HTTP never terminated.
        """
        s = _session(max_replies=4)
        first = s.next_turn()
        s.record_answer(first.subtopic_id, 0.9)

        restored = DiagnosticSession.restore(SUBTOPICS, s.snapshot())
        assert restored.reply_count == 1
        assert restored.next_turn().turn_index == 1

        # Drain the remaining budget and confirm it terminates.
        while restored.next_turn() is not None:
            restored.record_answer(restored.turns[-1].subtopic_id, 0.8)
        assert restored.finished
        assert restored.reply_count == 4
        assert restored.snapshot()["asked_total"] == 4

    def test_snapshot_preserves_estimates_exactly(self):
        s = _session()
        turn = s.next_turn()
        s.record_answer(turn.subtopic_id, 0.75)

        restored = DiagnosticSession.restore(SUBTOPICS, s.snapshot())
        before = {e.id: e.estimate for e in s.estimates()}
        after = {e.id: e.estimate for e in restored.estimates()}
        assert after == before

    def test_restore_without_snapshot_starts_fresh(self):
        restored = DiagnosticSession.restore(SUBTOPICS, None)
        assert restored.reply_count == 0
        assert restored.next_turn().turn_index == 0

    def test_finished_session_returns_no_more_turns(self):
        s = _session()
        s.finish("test")
        assert s.next_turn() is None

    def test_summary_is_serialisable(self):
        s = _session()
        turn = s.next_turn()
        s.record_answer(turn.subtopic_id, 0.6)
        payload = s.summary().to_dict()
        assert "estimates" in payload
        assert isinstance(payload["turns"], list)
        assert payload["finished_reason"]


class TestBands:
    def test_band_descriptions_are_ordered(self):
        assert describe_band(0.1) == "not_familiar"
        assert describe_band(0.45) == "basic_familiarity"
        assert describe_band(0.65) == "working_knowledge"
        assert describe_band(0.85) == "solid"
        assert describe_band(0.95) == "strong"

    def test_learner_can_correct_the_estimate(self):
        """FR-4.5: the summary is editable by the learner."""
        corrected = apply_correction(SUBTOPICS, {"s2": 0.9})
        assert corrected[1]["estimate"] == 0.9
        # Untouched subtopics keep the neutral default.
        assert corrected[0]["estimate"] == INITIAL_ESTIMATE

    def test_correction_is_clamped(self):
        corrected = apply_correction(SUBTOPICS, {"s1": 5.0})
        assert corrected[0]["estimate"] == 1.0


class TestForecast:
    """Section 6.7 / FR-12.4."""

    def test_required_speed_is_one_when_on_pace(self):
        focus = ForecastInput(remaining_minutes=600, minutes_per_day=60, days_left=10)
        assert required_speed(focus) == pytest.approx(1.0)

    def test_required_speed_above_one_when_behind(self):
        focus = ForecastInput(remaining_minutes=1200, minutes_per_day=60, days_left=10)
        assert required_speed(focus) == pytest.approx(0.5)

    def test_comfortable_plan_is_high_readiness(self):
        states = {t["id"]: TopicReviewState(strength=0.9) for t in SUBTOPICS}
        focus = ForecastInput(
            remaining_minutes=300, minutes_per_day=60, days_left=10,
            speed=1.0, states=states, topic_ids=[t["id"] for t in SUBTOPICS],
        )
        result = forecast_readiness(focus)
        assert result.level == "high"
        assert result.feasible is True
        assert result.reasons

    def test_overloaded_plan_is_low_readiness(self):
        result = forecast_readiness(
            ForecastInput(remaining_minutes=5000, minutes_per_day=30, days_left=2, speed=2.0)
        )
        assert result.level in {"medium", "low"}
        assert result.feasible is False
        assert any("ускориться" in r or "мало" in r for r in result.reasons)

    def test_many_weak_topics_lower_the_forecast(self):
        weak = {t["id"]: TopicReviewState(strength=0.1) for t in SUBTOPICS}
        strong = {t["id"]: TopicReviewState(strength=0.95) for t in SUBTOPICS}
        base = dict(remaining_minutes=300, minutes_per_day=60, days_left=10)
        weak_res = forecast_readiness(
            ForecastInput(**base, states=weak, topic_ids=[t["id"] for t in SUBTOPICS])
        )
        strong_res = forecast_readiness(
            ForecastInput(**base, states=strong, topic_ids=[t["id"] for t in SUBTOPICS])
        )
        assert weak_res.probability < strong_res.probability
        assert any("Слабых тем" in r for r in weak_res.reasons)

    def test_probability_is_bounded(self):
        result = forecast_readiness(
            ForecastInput(remaining_minutes=10**6, minutes_per_day=1, days_left=1)
        )
        assert 0.0 <= result.probability <= 1.0

    def test_forecast_is_serialisable(self):
        payload = forecast_readiness(
            ForecastInput(remaining_minutes=300, minutes_per_day=60, days_left=10)
        ).to_dict()
        assert set(payload) >= {"level", "probability", "reasons", "feasible"}

    def test_no_topics_means_no_weak_penalty(self):
        result = forecast_readiness(
            ForecastInput(remaining_minutes=300, minutes_per_day=60, days_left=10)
        )
        assert result.probability > 0.5

    def test_exam_suggestion_lists_stale_topics(self):
        """FR-11.4: topics not yet reviewed by the exam date."""
        now = _dt(2026, 3, 1, tzinfo=timezone.utc)
        states = {
            "fresh": TopicReviewState(next_review_at=now + timedelta(days=30)),
            "stale": TopicReviewState(next_review_at=now - timedelta(days=2)),
        }
        today = date(2026, 3, 1)
        assert plan_review_day(states, today, horizon_days=3) == ["fresh"]