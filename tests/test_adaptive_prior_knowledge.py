"""Tests for cross-goal prior knowledge feeding the planner.

FR-10.1 / section 6.4: the plan must follow the person, not the topic list. A
subject already mastered in an earlier goal must be planned as a refresher rather
than a full first pass. These are the pure decision functions; the query they sit
on is covered by the live check.
"""

import pytest

from services.adaptive_learning import context as ctx_mod
from services.adaptive_learning.context import (
    PRIOR_FAMILIAR,
    PRIOR_REFRESHER,
    prior_reason,
    prior_strength_for_titles,
    time_factor_for_prior,
)
from services.adaptive_learning.scheduling import _PRIOR_REASON


class TestTimeFactor:
    """A previously-seen topic needs less time, never zero."""

    def test_never_seen_costs_full_time(self):
        assert time_factor_for_prior(0.0) == 1.0

    def test_known_topic_is_a_refresher(self):
        assert time_factor_for_prior(0.9) == PRIOR_REFRESHER

    def test_familiar_topic_costs_more_than_known(self):
        factor = time_factor_for_prior(0.6)
        assert PRIOR_REFRESHER < factor < 1.0

    def test_factor_never_reaches_zero(self):
        """A refresher still needs some minutes; zero would silently drop it."""
        for value in (0.0, 0.1, 0.5, 0.8, 1.0):
            assert time_factor_for_prior(value) > 0.0

    def test_higher_prior_never_costs_more(self):
        low = time_factor_for_prior(0.3)
        high = time_factor_for_prior(0.9)
        assert high <= low


class TestReasons:
    """FR-10.4: the shortened topic must say why."""

    def test_known_gets_a_refresher_reason(self):
        assert "повторение" in prior_reason(0.9)

    def test_familiar_gets_a_shortened_reason(self):
        assert "сокращено" in prior_reason(0.6)

    def test_unknown_prior_gets_no_claim(self):
        assert prior_reason(0.2) == ""

    def test_reasons_are_russian(self):
        for value in (0.6, 0.9):
            text = prior_reason(value)
            assert any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text)

    @pytest.mark.parametrize("value", [0.0, 0.3, 0.6, 0.9, 1.0])
    def test_planner_reason_is_always_filled(self, value):
        assert _PRIOR_REASON(value).strip()


class TestPlannerAndContextAgree:
    """Two places encode the same thresholds; they must not drift apart."""

    @pytest.mark.parametrize("value", [0.0, 0.4, 0.55, 0.75, 0.85, 1.0])
    def test_both_sides_agree_on_the_kind_of_topic(self, value):
        from services.adaptive_learning.scheduling import (
            PRIOR_FAMILIAR_RATIO,
            PRIOR_KNOWN_RATIO,
        )

        ctx_known = value >= ctx_mod.KNOWN_THRESHOLD
        plan_known = value >= PRIOR_KNOWN_RATIO
        assert ctx_known == plan_known, value

        ctx_familiar = ctx_mod.WEAK_THRESHOLD <= value < ctx_mod.KNOWN_THRESHOLD
        plan_familiar = PRIOR_FAMILIAR_RATIO <= value < PRIOR_KNOWN_RATIO
        assert ctx_familiar == plan_familiar, value


class TestTitleMatching:
    """Prior-knowledge matching must catch the same subject and nothing else."""

    def test_identical_titles_match(self):
        assert ctx_mod.same_subject("Метод Гаусса", "Метод Гаусса") is True

    def test_unrelated_topics_do_not_match(self):
        assert ctx_mod.same_subject("Метод Гаусса", "Предел функции") is False

    def test_a_differing_qualifier_still_matches(self):
        """This is the case the strict overlap ratio missed."""
        assert ctx_mod.same_subject(
            "Прямой ход метода Гаусса", "Обратный ход метода Гаусса"
        ) is True

    def test_the_realistic_case_matches(self):
        """Two goals on the same subject produce near-identical subtopic titles."""
        assert ctx_mod.same_subject(
            "Основные понятия систем линейных уравнений",
            "Основные понятия систем линейных уравнений",
        ) is True

    def test_restructured_titles_are_not_matched(self):
        """Documented limit: freely reworded titles for one subject do not match.

        Matching is lexical. Loosening it far enough to catch every rewording
        would start matching unrelated subjects, which would shorten topics the
        learner has never seen. The same-goal case above is the one that matters:
        that is how prior knowledge actually arrives.
        """
        assert ctx_mod.same_subject(
            "Основные понятия систем линейных уравнений",
            "Метод Гаусса для систем линейных уравнений",
        ) is False

    def test_a_shared_word_alone_is_not_enough(self):
        """Two unrelated topics both mentioning "теория" must not match."""
        assert ctx_mod.same_subject("Теория чисел", "Теория множеств") is False

    def test_empty_titles_never_match(self):
        assert ctx_mod.same_subject("", "Метод Гаусса") is False
        assert ctx_mod.same_subject("Метод Гаусса", "") is False

    def test_similarity_threshold_is_sane(self):
        assert 0.5 < ctx_mod.TITLE_SIMILARITY < 1.0


class TestPriorQuery:
    """Signature guards: the caller must be able to exclude the current goal."""

    def test_accepts_exclude_goal(self):
        import inspect

        params = inspect.signature(prior_strength_for_titles).parameters
        assert "exclude_goal_id" in params
        assert params["exclude_goal_id"].default is None

    @pytest.mark.asyncio
    async def test_empty_titles_short_circuits(self):
        """No titles means no query, so no session is touched."""
        result = await prior_strength_for_titles(None, None, [])
        assert result == {}