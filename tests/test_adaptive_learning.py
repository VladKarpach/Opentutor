"""Tests for the deterministic adaptive-learning algorithms (TZ sections 6.1-6.5).

These cover the numeric rules stated in the TZ: interval multipliers, the
confidence correction, strength decay, topological ordering, cycle detection,
the speed coefficient and feasibility warnings.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from config import settings

from services.adaptive_learning.repetition import (
    BASE_INTERVALS,
    STATUS_KNOWN,
    STATUS_WEAK,
    TopicReviewState,
    apply_answer,
    current_strength,
    decay_strength,
    due_topics,
    next_interval_days,
)
from services.adaptive_learning.scheduling import (
    ITEM_EXAM,
    ITEM_PRACTICE,
    ITEM_REVIEW,
    ITEM_STUDY,
    PlanItem,
    TopicInput,
    _estimate_minutes,
    adaptive_difficulty_factor,
    build_plan,
    check_feasibility,
    difficulty_multiplier,
    difficulty_prior,
    find_cycle,
    normalise_fsrs_difficulty,
    normalise_raw_fsrs,
    observed_difficulty_from_score,
    relative_difficulty,
    soft_return_plan,
    speed_coefficient,
    update_relative_difficulty,
    topological_order,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
DAY0 = date(2026, 3, 1)


# ──────────────────────────────────────────────────────────────
# Section 6.3 — repetition
# ──────────────────────────────────────────────────────────────

class TestIntervalMultipliers:
    """TZ 6.3: >=0.8 -> x2.5, 0.5-0.8 -> x1.3, <0.5 -> 1 day + weak flag."""

    def test_high_score_advances_one_ladder_rung(self):
        state = TopicReviewState(repetitions=1, interval_days=3)
        assert next_interval_days(state, 0.9) == 7

    def test_medium_score_stays_on_the_current_rung(self):
        """The x1.3 band, realised as 'stay where you are'."""
        state = TopicReviewState(repetitions=2, interval_days=7)
        assert next_interval_days(state, 0.6) == 7

    def test_low_score_resets_to_one_day(self):
        state = TopicReviewState(interval_days=30)
        assert next_interval_days(state, 0.2) == 1

    def test_low_score_marks_topic_weak(self):
        state = TopicReviewState(interval_days=7, strength=0.9)
        result = apply_answer(state, 0.2, now=NOW)
        assert result.state.status == STATUS_WEAK
        assert result.state.lapses == 1

    def test_base_interval_sequence_grows(self):
        """TZ 6.3 base progression 1 -> 3 -> 7 -> 14 -> 30 for a fresh topic."""
        state = TopicReviewState(interval_days=0, repetitions=0)
        ladder = []
        for _ in range(6):
            state.interval_days = next_interval_days(state, 0.5)
            ladder.append(state.interval_days)
            state.repetitions += 1  # apply_answer() bumps this after scoring
        assert ladder[:5] == [1, 3, 7, 14, 30]

    def test_high_score_on_a_fresh_topic_advances_a_rung(self):
        state = TopicReviewState(interval_days=0, repetitions=0)
        # First correct answer jumps to the second rung rather than the first.
        assert next_interval_days(state, 0.9) == 3

    @pytest.mark.parametrize(
        "score,expected_band",
        [(0.8, "high"), (0.79, "medium"), (0.5, "medium"), (0.49, "low")],
    )
    def test_score_band_boundaries(self, score, expected_band):
        """Band edges from section 6.3: >=0.8 high, >=0.5 medium, else low."""
        # Start mid-ladder so every band produces an observable difference.
        state = TopicReviewState(interval_days=0, repetitions=2)
        interval = next_interval_days(state, score)
        if expected_band == "low":
            assert interval == 1
        elif expected_band == "high":
            assert interval == BASE_INTERVALS[3]
        else:
            assert interval == BASE_INTERVALS[2]


class TestConfidenceCorrection:
    """TZ 6.3 / FR-8.5: confidence adjusts the interval."""

    def test_guessing_correct_does_not_advance_the_ladder(self):
        """FR-8.5: guessed but right -> smaller growth than a confident correct."""
        guessing = TopicReviewState(repetitions=2, interval_days=7)
        confident = TopicReviewState(repetitions=2, interval_days=7)
        assert next_interval_days(guessing, 0.9, confidence="guessing") == 7
        assert next_interval_days(confident, 0.9, confidence="confident") == 14

    def test_confident_failure_stays_at_the_minimum(self):
        plain = TopicReviewState(repetitions=3, interval_days=14)
        overconfident = TopicReviewState(repetitions=3, interval_days=14)
        assert next_interval_days(overconfident, 0.1, confidence="confident") == 1
        assert next_interval_days(plain, 0.1) == 1

    def test_ladder_caps_a_top_rung_topic(self):
        """The ladder never exceeds 30 days, so a perfect score stops growing."""
        top = TopicReviewState(repetitions=9, interval_days=30)
        assert next_interval_days(top, 0.95) == 30


class TestStrengthDecay:
    """TZ 6.3: strength(t) = strength0 * exp(-t / S)."""

    def test_decay_is_exponential(self):
        # strength0=1.0, S=10, t=10 -> exp(-1)
        import math
        expected = math.exp(-1.0)
        assert decay_strength(1.0, 10.0, 10.0) == pytest.approx(expected)

    def test_strength_is_unchanged_at_zero_elapsed_time(self):
        assert decay_strength(0.8, 10.0, 0.0) == pytest.approx(0.8)

    def test_zero_stability_returns_zero(self):
        assert decay_strength(0.9, 0.0, 5.0) == 0.0

    def test_stability_grows_after_success(self):
        state = TopicReviewState(interval_days=3, stability_days=3.0)
        apply_answer(state, 0.9, now=NOW)
        assert state.stability_days > 3.0

    def test_stability_resets_after_failure(self):
        state = TopicReviewState(interval_days=7, stability_days=40.0)
        apply_answer(state, 0.1, now=NOW)
        assert state.stability_days == 1.0


class TestDueAndWeakest:
    """TZ FR-9.3: due reviews first, then weakest topics."""

    def test_due_topics_excludes_future(self):
        states = {
            "due": TopicReviewState(next_review_at=NOW - timedelta(days=1)),
            "later": TopicReviewState(next_review_at=NOW + timedelta(days=5)),
        }
        assert due_topics(states, now=NOW) == ["due"]


# ──────────────────────────────────────────────────────────────
# Sections 6.4 / FR-5 — scheduling and graph
# ──────────────────────────────────────────────────────────────

class TestGraph:
    """TZ FR-5.2, FR-5.3: DAG check and topological order."""

    def test_linear_chain_orders_correctly(self):
        topics = {
            "c": TopicInput(id="c", title="C", estimated_minutes=10, prerequisites=["b"]),
            "b": TopicInput(id="b", title="B", estimated_minutes=10, prerequisites=["a"]),
            "a": TopicInput(id="a", title="A", estimated_minutes=10),
        }
        assert topological_order(topics) == ["a", "b", "c"]

    def test_cycle_is_detected(self):
        topics = {
            "a": TopicInput(id="a", title="A", estimated_minutes=10, prerequisites=["b"]),
            "b": TopicInput(id="b", title="B", estimated_minutes=10, prerequisites=["a"]),
        }
        cycle = find_cycle(topics)
        assert cycle is not None
        assert "a" in cycle and "b" in cycle

    def test_acyclic_graph_has_no_cycle(self):
        topics = {
            "a": TopicInput(id="a", title="A", estimated_minutes=10),
            "b": TopicInput(id="b", title="B", estimated_minutes=10, prerequisites=["a"]),
        }
        assert find_cycle(topics) is None

    def test_dangling_prerequisite_is_ignored(self):
        topics = {
            "a": TopicInput(id="a", title="A", estimated_minutes=10, prerequisites=["ghost"]),
        }
        assert find_cycle(topics) is None
        assert topological_order(topics) == ["a"]

    def test_weakest_ready_topic_goes_first(self):
        """FR-5.4: the gap is closed before new material is added."""
        topics = {
            "strong": TopicInput(
                id="strong", title="Strong", estimated_minutes=10,
                state=TopicReviewState(strength=0.95),
            ),
            "weak": TopicInput(
                id="weak", title="Weak", estimated_minutes=10,
                state=TopicReviewState(strength=0.1),
            ),
        }
        assert topological_order(topics) == ["weak", "strong"]


class TestSpeedCoefficient:
    """TZ section 6.5: median(actual / planned), clamped to [0.5, 2.0]."""

    def test_median_of_ratios(self):
        actual = [20.0, 20.0, 60.0]
        planned = [10.0, 10.0, 10.0]
        assert speed_coefficient(actual, planned) == pytest.approx(2.0)

    def test_clamped_to_upper_bound(self):
        assert speed_coefficient([100.0], [1.0]) == 2.0

    def test_clamped_to_lower_bound(self):
        assert speed_coefficient([1.0], [100.0]) == 0.5

    def test_empty_input_defaults_to_one(self):
        assert speed_coefficient([], []) == 1.0

    def test_zero_planned_is_skipped(self):
        assert speed_coefficient([10.0], [0.0]) == 1.0


class TestFeasibility:
    """TZ FR-6.4: warn and propose options when the deadline is impossible."""

    def test_feasible_when_enough_time(self):
        ok, warnings, suggestions = check_feasibility(required_minutes=600, days=10, minutes_per_day=60)
        assert ok is True
        assert warnings == []

    def test_infeasible_reports_warning_and_options(self):
        ok, warnings, suggestions = check_feasibility(
            required_minutes=1000, days=5, minutes_per_day=60
        )
        assert ok is False
        assert warnings
        assert len(suggestions) == 3


class TestPlanBuilding:
    """TZ FR-6.1-6.3 and section 6.4."""

    def _topics(self, n=3):
        return [
            TopicInput(id=f"t{i}", title=f"Topic {i}", estimated_minutes=30,
                       prerequisites=[f"t{i-1}"] if i else [])
            for i in range(n)
        ]

    def test_plan_respects_day_capacity(self):
        topics = self._topics(4)
        plan = build_plan(topics, start_day=DAY0, days=3, minutes_per_day=60)
        for day, items in plan.days.items():
            total = sum(i.planned_minutes for i in items)
            assert total <= 60 + 5, f"day {day} overfilled: {total}"

    def test_orders_topologically(self):
        plan = build_plan(self._topics(3), start_day=DAY0, days=5, minutes_per_day=90)
        first_day = min(plan.items, key=lambda i: (i.day, i.title))
        assert first_day.topic_id == "t0"

    def test_known_topics_are_skipped(self):
        topics = [
            TopicInput(id="known", title="Known", estimated_minutes=60,
                       state=TopicReviewState(strength=0.95)),
            TopicInput(id="new", title="New", estimated_minutes=60),
        ]
        plan = build_plan(topics, start_day=DAY0, days=2, minutes_per_day=60)
        assert "known" not in {i.topic_id for i in plan.items}

    def test_due_topics_become_review_items(self):
        topics = [
            TopicInput(id="due", title="Due", estimated_minutes=60,
                       state=TopicReviewState(strength=0.5, next_review_at=NOW - timedelta(days=2))),
            TopicInput(id="fresh", title="Fresh", estimated_minutes=60),
        ]
        plan = build_plan(topics, start_day=DAY0, days=2, minutes_per_day=60, today=NOW)
        assert any(i.item_type == ITEM_REVIEW for i in plan.items)

    def test_speed_factor_increases_minutes(self):
        topics = self._topics(2)
        normal = build_plan(topics, start_day=DAY0, days=5, minutes_per_day=60, speed=1.0)
        slow = build_plan(topics, start_day=DAY0, days=5, minutes_per_day=60, speed=1.5)
        assert slow.total_minutes > normal.total_minutes

    def test_cycle_warning_is_surfaced(self):
        topics = [
            TopicInput(id="a", title="A", estimated_minutes=10, prerequisites=["b"]),
            TopicInput(id="b", title="B", estimated_minutes=10, prerequisites=["a"]),
        ]
        plan = build_plan(topics, start_day=DAY0, days=2, minutes_per_day=60)
        assert any("cycle" in w.lower() for w in plan.warnings)

    def test_infeasible_plan_reports_suggestions(self):
        topics = [TopicInput(id=f"t{i}", title=f"T{i}", estimated_minutes=600) for i in range(4)]
        plan = build_plan(topics, start_day=DAY0, days=1, minutes_per_day=30)
        assert plan.feasible is False
        assert plan.suggestions

    def test_items_are_json_serialisable(self):
        plan = build_plan(self._topics(2), start_day=DAY0, days=2, minutes_per_day=60)
        for item in plan.items:
            payload = item.to_dict()
            assert isinstance(payload["day"], str)

    def test_plan_ends_with_practice_and_exam_days(self):
        plan = build_plan(
            self._topics(4), start_day=DAY0, days=8, minutes_per_day=60
        )
        practice = [i for i in plan.items if i.item_type == ITEM_PRACTICE]
        exam = [i for i in plan.items if i.item_type == ITEM_EXAM]

        assert len(practice) == 1
        assert len(exam) == 1
        assert practice[0].topic_id is None
        assert exam[0].topic_id is None
        assert "Практика" in practice[0].title
        assert exam[0].title == "Экзамен"
        assert practice[0].day == DAY0 + timedelta(days=6)
        assert exam[0].day == DAY0 + timedelta(days=7)
        study_days = [
            i.day for i in plan.items if i.item_type in (ITEM_STUDY, ITEM_REVIEW)
        ]
        assert study_days
        assert max(study_days) < practice[0].day

    def test_short_plan_appends_practice_and_exam_after_study_days(self):
        plan = build_plan(
            self._topics(2), start_day=DAY0, days=2, minutes_per_day=60
        )
        practice = next(i for i in plan.items if i.item_type == ITEM_PRACTICE)
        exam = next(i for i in plan.items if i.item_type == ITEM_EXAM)

        assert practice.day == DAY0 + timedelta(days=2)
        assert exam.day == DAY0 + timedelta(days=3)
        assert any("добавлены отдельно" in warning for warning in plan.warnings)

    def test_caller_speed_overrides_mode(self):
        topics = self._topics(2)
        plan = build_plan(
            topics,
            start_day=DAY0,
            days=3,
            minutes_per_day=60,
            mode="self_paced",
            speed=1.0,
        )
        assert plan.speed == 1.0


class TestSoftReturn:
    """TZ FR-10.5: missed days produce a gentler catch-up plan."""

    def test_soft_return_drops_some_topics(self):
        topics = [TopicInput(id=f"t{i}", title=f"T{i}", estimated_minutes=30) for i in range(6)]
        normal = build_plan(topics, start_day=DAY0, days=7, minutes_per_day=60)
        soft = soft_return_plan(topics, today=DAY0, days=7, minutes_per_day=60, missed_days=4)
        assert len(soft.items) < len(normal.items)
        assert any("Catch-up" in w for w in soft.warnings)


# ──────────────────────────────────────────────────────────────
# Adaptive topic difficulty (new feature)
# ──────────────────────────────────────────────────────────────


class TestNormaliseFsrsDifficulty:
    """FSRS difficulty is normalised to 0..1; None is neutral (0.5)."""

    def test_none_is_neutral(self):
        assert normalise_fsrs_difficulty(None) == 0.5

    def test_zero_is_zero(self):
        assert normalise_fsrs_difficulty(0.0) == 0.0

    def test_one_is_one(self):
        assert normalise_fsrs_difficulty(1.0) == 1.0

    def test_above_one_clamps_to_one(self):
        assert normalise_fsrs_difficulty(5.0) == 1.0
        assert normalise_fsrs_difficulty(10.0) == 1.0

    def test_below_zero_clamps_to_zero(self):
        assert normalise_fsrs_difficulty(-1.0) == 0.0

    def test_midpoint_stays_midpoint(self):
        assert normalise_fsrs_difficulty(0.5) == 0.5


class TestAdaptiveDifficultyFactor:
    """adaptive_factor = clamp(1.0 + k * (norm - 0.5), min, max)."""

    def test_no_fsrs_data_returns_one(self):
        assert adaptive_difficulty_factor(None) == 1.0

    def test_neutral_difficulty_returns_one(self):
        assert adaptive_difficulty_factor(0.5) == 1.0

    def test_hard_difficulty_increases_factor(self):
        k = settings.adaptive_difficulty_k
        expected = min(
            settings.adaptive_difficulty_max,
            max(settings.adaptive_difficulty_min, 1.0 + k * (1.0 - 0.5)),
        )
        assert adaptive_difficulty_factor(1.0) == expected

    def test_easy_difficulty_decreases_factor(self):
        k = settings.adaptive_difficulty_k
        expected = min(
            settings.adaptive_difficulty_max,
            max(settings.adaptive_difficulty_min, 1.0 + k * (0.0 - 0.5)),
        )
        assert adaptive_difficulty_factor(0.0) == expected

    def test_factor_is_clamped(self):
        # Norm of 999 → 1.0; factor = 1.0 + 0.8 * 0.5 = 1.4, clamped to [0.7, 1.6].
        assert adaptive_difficulty_factor(999.0) == 1.4


class TestNormaliseRawFsrs:
    """Raw FSRS difficulty is 1..10; the planner's scale is 0..1."""

    def test_raw_one_is_zero(self):
        assert normalise_raw_fsrs(1.0) == 0.0

    def test_raw_ten_is_one(self):
        assert normalise_raw_fsrs(10.0) == 1.0

    def test_raw_midpoint_is_half(self):
        assert normalise_raw_fsrs(5.5) == 0.5

    def test_learning_progress_default_is_midway(self):
        # A fresh card starts at 5.0, which must not read as "maximum hard".
        assert normalise_raw_fsrs(5.0) == pytest.approx(4 / 9)

    def test_none_stays_none(self):
        assert normalise_raw_fsrs(None) is None

    def test_out_of_range_clamps(self):
        assert normalise_raw_fsrs(0.0) == 0.0
        assert normalise_raw_fsrs(99.0) == 1.0


class TestDifficultyPrior:
    def test_easy_is_low_and_hard_is_high(self):
        assert difficulty_prior("easy") < difficulty_prior("medium") < difficulty_prior("hard")

    def test_priors_come_from_settings(self):
        assert difficulty_prior("easy") == settings.difficulty_prior_easy
        assert difficulty_prior("medium") == settings.difficulty_prior_medium
        assert difficulty_prior("hard") == settings.difficulty_prior_hard

    def test_unknown_falls_back_to_medium(self):
        assert difficulty_prior("bogus") == settings.difficulty_prior_medium
        assert difficulty_prior(None) == settings.difficulty_prior_medium


class TestRelativeDifficulty:
    """0.5 = as expected; the initial estimate is the anchor."""

    def test_performing_exactly_as_expected_is_neutral(self):
        for level, prior in (
            ("easy", settings.difficulty_prior_easy),
            ("medium", settings.difficulty_prior_medium),
            ("hard", settings.difficulty_prior_hard),
        ):
            assert relative_difficulty(level, prior) == pytest.approx(0.5)

    def test_struggling_with_an_easy_topic_reads_as_harder(self):
        # Easy prior 0.2, observed 0.8 → 0.5 + 0.6 = 1.0 (saturated).
        assert relative_difficulty("easy", 0.8) == 1.0

    def test_breezing_through_a_hard_topic_reads_as_easier(self):
        # Hard prior 0.8, observed 0.2 → 0.5 - 0.6 = 0 (saturated).
        assert relative_difficulty("hard", 0.2) == 0.0

    def test_missing_observation_stays_unknown(self):
        assert relative_difficulty("medium", None) is None

    def test_result_is_clamped(self):
        assert 0.0 <= relative_difficulty("easy", 1.0) <= 1.0
        assert 0.0 <= relative_difficulty("hard", 0.0) <= 1.0


class TestUpdateRelativeDifficulty:
    """Each answer moves the stored estimate part of the way (EMA)."""

    def test_first_observation_moves_from_neutral(self):
        alpha = settings.adaptive_difficulty_alpha
        target = relative_difficulty("medium", 1.0)  # 1.0
        stored = update_relative_difficulty(None, "medium", 1.0)
        assert stored == pytest.approx(0.5 + alpha * (target - 0.5))

    def test_one_bad_answer_cannot_flip_a_topic(self):
        stored = update_relative_difficulty(None, "medium", 1.0)
        assert stored < 0.7

    def test_repeated_bad_answers_converge(self):
        stored = None
        for _ in range(30):
            stored = update_relative_difficulty(stored, "medium", 1.0)
        assert stored == pytest.approx(1.0, abs=0.01)

    def test_repeated_good_answers_converge_down(self):
        stored = None
        for _ in range(30):
            stored = update_relative_difficulty(stored, "medium", 0.0)
        assert stored == pytest.approx(0.0, abs=0.01)

    def test_as_expected_answers_stay_neutral(self):
        stored = None
        for _ in range(30):
            stored = update_relative_difficulty(
                stored, "hard", settings.difficulty_prior_hard
            )
        assert stored == pytest.approx(0.5)

    def test_no_observation_leaves_current_unchanged(self):
        assert update_relative_difficulty(0.7, "medium", None) == 0.7

    def test_alpha_zero_freezes_the_estimate(self):
        assert update_relative_difficulty(0.42, "medium", 1.0, alpha=0.0) == 0.42


class TestObservedDifficultyFromScore:
    def test_perfect_score_is_easy(self):
        assert observed_difficulty_from_score(1.0) == 0.0

    def test_zero_score_is_hard(self):
        assert observed_difficulty_from_score(0.0) == 1.0

    def test_out_of_range_is_clamped(self):
        assert observed_difficulty_from_score(1.5) == 0.0
        assert observed_difficulty_from_score(-0.5) == 1.0


class TestDifficultyMultiplier:
    """Static multipliers: easy < medium < hard."""

    def test_easy(self):
        assert difficulty_multiplier("easy") == settings.difficulty_multiplier_easy

    def test_medium(self):
        assert difficulty_multiplier("medium") == settings.difficulty_multiplier_medium

    def test_hard(self):
        assert difficulty_multiplier("hard") == settings.difficulty_multiplier_hard

    def test_unknown_falls_back_to_medium(self):
        assert difficulty_multiplier("bogus") == settings.difficulty_multiplier_medium

    def test_none_falls_back_to_medium(self):
        assert difficulty_multiplier(None) == settings.difficulty_multiplier_medium


class TestEstimateMinutesWithDifficulty:
    """_estimate_minutes scales base time by difficulty, then by strength."""

    def test_hard_gets_more_time_than_easy(self):
        easy = TopicInput(id="e", title="Easy", estimated_minutes=30, difficulty="easy")
        hard = TopicInput(id="h", title="Hard", estimated_minutes=30, difficulty="hard")
        easy_min = _estimate_minutes(easy, DAY0)
        hard_min = _estimate_minutes(hard, DAY0)
        assert hard_min > easy_min
        assert easy_min < 30  # easy × 0.7 = 21
        assert hard_min > 30  # hard × 1.45 = 43.5 → 44

    def test_medium_is_unchanged_without_state(self):
        medium = TopicInput(id="m", title="Medium", estimated_minutes=30, difficulty="medium")
        assert _estimate_minutes(medium, DAY0) == 30

    def test_adaptive_factor_applies_without_topic_state(self):
        # Card reviews can carry the signal for a topic that was never graded
        # directly, so the adaptive factor must not require a TopicState.
        card_only = TopicInput(
            id="c",
            title="Cards say hard",
            estimated_minutes=30,
            difficulty="medium",
            fsrs_difficulty=1.0,
        )
        baseline = TopicInput(
            id="b", title="No signal", estimated_minutes=30, difficulty="medium"
        )
        assert _estimate_minutes(card_only, DAY0) > _estimate_minutes(baseline, DAY0)

    def test_difficulty_and_strength_combine(self):
        from services.adaptive_learning.repetition import TopicReviewState

        hard_weak = TopicInput(
            id="hw",
            title="Hard Weak",
            estimated_minutes=30,
            difficulty="hard",
            state=TopicReviewState(strength=0.2, status="weak"),
        )
        minutes = _estimate_minutes(hard_weak, DAY0)
        assert minutes >= 50

    def test_fsrs_difficulty_adds_adaptive_factor(self):
        from services.adaptive_learning.repetition import TopicReviewState

        hard_with_fsrs = TopicInput(
            id="hf",
            title="Hard FSRS",
            estimated_minutes=30,
            difficulty="medium",
            fsrs_difficulty=1.0,
            state=TopicReviewState(strength=0.5),
        )
        hard_without_fsrs = TopicInput(
            id="h2",
            title="Hard NoFSRS",
            estimated_minutes=30,
            difficulty="medium",
            fsrs_difficulty=None,
            state=TopicReviewState(strength=0.5),
        )
        with_fsrs = _estimate_minutes(hard_with_fsrs, DAY0)
        without_fsrs = _estimate_minutes(hard_without_fsrs, DAY0)
        assert with_fsrs > without_fsrs

    def test_as_expected_performance_adds_no_extra_time(self):
        from services.adaptive_learning.repetition import TopicReviewState

        as_expected = TopicInput(
            id="he",
            title="Hard as expected",
            estimated_minutes=30,
            difficulty="hard",
            fsrs_difficulty=0.5,
            state=TopicReviewState(strength=0.5),
        )
        no_data = TopicInput(
            id="hn",
            title="Hard no data",
            estimated_minutes=30,
            difficulty="hard",
            fsrs_difficulty=None,
            state=TopicReviewState(strength=0.5),
        )
        # The static hard multiplier is already the baseline; neutral relative
        # difficulty must not stack a second multiplier on top of it.
        assert _estimate_minutes(as_expected, DAY0) == _estimate_minutes(no_data, DAY0)

    def test_harder_than_expected_gets_more_than_hard_baseline(self):
        from services.adaptive_learning.repetition import TopicReviewState

        struggling = TopicInput(
            id="hs",
            title="Hard and struggling",
            estimated_minutes=30,
            difficulty="hard",
            fsrs_difficulty=1.0,
            state=TopicReviewState(strength=0.5),
        )
        as_expected = TopicInput(
            id="he",
            title="Hard as expected",
            estimated_minutes=30,
            difficulty="hard",
            fsrs_difficulty=0.5,
            state=TopicReviewState(strength=0.5),
        )
        assert _estimate_minutes(struggling, DAY0) > _estimate_minutes(as_expected, DAY0)