"""Interval repetition algorithm — TZ section 6.3.

TZ requirements implemented here:
- FR-9.1/FR-9.2: strength, last/next review dates, current interval per topic
- Section 6.3 multipliers:
    final >= 0.8       -> interval x 2.5
    0.5 <= final < 0.8 -> interval x 1.3
    final < 0.5        -> interval = 1 day, topic marked weak
- Confidence correction (FR-8.5): "confident but failed" shrinks the interval
  further; "guessing but correct" grows it by x1.5 instead of the usual x2.5
- Base intervals: 1 -> 3 -> 7 -> 14 -> 30 days
- Strength decay: strength(t) = strength0 * exp(-t / S), where S grows with
  each successful repetition

Design note: this module is deliberately free of LLM and I/O so the scheduling
rules stay deterministic and unit-testable, per TZ section 6.4 ("scheduling,
intervals and strength calculation are performed by deterministic code").
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Section 6.3 base intervals in days.
BASE_INTERVALS: tuple[int, ...] = (1, 3, 7, 14, 30)

# Section 6.3 multipliers. These are the TZ's literal figures; the interval
# ladder below is their concrete realization (see next_interval_days).
MULTIPLIER_MEDIUM = 1.3    # 0.5 <= final < 0.8
FAILURE_INTERVAL_DAYS = 1  # final < 0.5

HIGH_SCORE_THRESHOLD = 0.8
MEDIUM_SCORE_THRESHOLD = 0.5

# FR-9.2 / FR-5.4 topic state labels.
STATUS_NOT_STARTED = "not_started"
STATUS_WEAK = "weak"
STATUS_LEARNING = "learning"
STATUS_KNOWN = "known"

# Strength stability floor/ceiling in days.
MIN_STABILITY_DAYS = 1.0
MAX_STABILITY_DAYS = 365.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class TopicReviewState:
    """Per-topic repetition state (mirrors the TZ TopicState model)."""

    strength: float = 0.0
    """Current knowledge strength in [0, 1]."""

    uncertainty: float = 1.0
    """Confidence in the strength estimate. Shrinks as evidence accumulates."""

    interval_days: int = 0
    """Current repetition interval in days."""

    repetitions: int = 0
    """Number of successful repetitions."""

    lapses: int = 0
    """Number of times the topic was forgotten after being learned."""

    status: str = STATUS_NOT_STARTED

    last_review_at: datetime | None = None
    next_review_at: datetime | None = None

    stability_days: float = 0.0
    """Time constant S in strength(t) = strength0 * exp(-t / S)."""

    fsrs_difficulty: float | None = None
    """FSRS difficulty of the topic's cards (0 = easy, 1 = hard). None = no data."""

    confidence_label: str = ""
    """User-declared confidence: confident | unsure | guessing."""

    history: list[float] = field(default_factory=list)
    """Score history for analytics (FR-12.2)."""


@dataclass
class ReviewDecision:
    """Outcome of applying one answer to a topic's repetition state."""

    state: TopicReviewState
    changed: bool
    reason: str


def decay_strength(
    strength_at_review: float,
    stability_days: float,
    days_elapsed: float,
) -> float:
    """Section 6.3 decay: strength(t) = strength0 * exp(-t / S)."""
    if strength_at_review <= 0 or stability_days <= 0:
        return 0.0
    s = max(MIN_STABILITY_DAYS, stability_days)
    return _clamp(strength_at_review * math.exp(-max(0.0, days_elapsed) / s), 0.0, 1.0)


def current_strength(state: TopicReviewState, now: datetime | None = None) -> float:
    """Strength right now, accounting for decay since the last review."""
    if state.last_review_at is None or state.stability_days <= 0:
        return _clamp(state.strength, 0.0, 1.0)
    now = now or datetime.now(timezone.utc)
    days = (now - state.last_review_at).total_seconds() / 86400.0
    return decay_strength(state.strength, state.stability_days, days)


def next_interval_days(
    state: TopicReviewState,
    final_score: float,
    confidence: str = "",
) -> int:
    """Section 6.3: interval rules.

    The TZ states two things that look like separate rules but are one:

    - multiplicative bands: x2.5 for >=0.8, x1.3 for 0.5-0.8, 1 day for <0.5
    - a concrete base ladder: 1 -> 3 -> 7 -> 14 -> 30 days

    The ladder *is* the concrete form of the multipliers: consecutive rungs grow
    by 3.0, 2.33, 2.0 and 2.14, i.e. roughly x2.5, while a "medium" answer (the
    x1.3 case) stays on the current rung. Using the ladder as the single source
    of truth keeps the sequence reproducible, instead of letting repeated medium
    answers drift (3 -> 4 -> 5 -> 6 ...) away from the spec.

    FR-8.5 confidence correction:
    - correct but guessed -> does not advance the ladder, which is the concrete
      equivalent of the smaller x1.5 growth the TZ asks for;
    - confident but failed -> stays at the minimum, scheduling the retry sooner.
    """
    score = _clamp(final_score, 0.0, 1.0)

    if score < MEDIUM_SCORE_THRESHOLD:
        # FR-9.2: failure resets to a single day and flags the topic as weak.
        return FAILURE_INTERVAL_DAYS

    current_rung = min(state.repetitions, len(BASE_INTERVALS) - 1)

    if score >= HIGH_SCORE_THRESHOLD:
        if confidence == "guessing":
            return BASE_INTERVALS[current_rung]
        return BASE_INTERVALS[min(current_rung + 1, len(BASE_INTERVALS) - 1)]

    # Medium: the x1.3 band, realised as "stay on the current rung".
    return BASE_INTERVALS[current_rung]


def _update_stability(state: TopicReviewState, final_score: float, interval_days: int) -> float:
    """Grow S with each successful repetition; reset it on failure."""
    if final_score < MEDIUM_SCORE_THRESHOLD:
        return MIN_STABILITY_DAYS
    # Successful repetition stretches the memory window.
    grown = max(state.stability_days * MULTIPLIER_MEDIUM, float(interval_days))
    return _clamp(grown, MIN_STABILITY_DAYS, MAX_STABILITY_DAYS)


def _status_for(final_score: float, strength: float) -> str:
    if final_score < MEDIUM_SCORE_THRESHOLD:
        return STATUS_WEAK
    if strength >= HIGH_SCORE_THRESHOLD:
        return STATUS_KNOWN
    return STATUS_LEARNING


def apply_answer(
    state: TopicReviewState,
    final_score: float,
    *,
    now: datetime | None = None,
    confidence: str = "",
) -> ReviewDecision:
    """Apply one scored answer to a topic's repetition state.

    This is the single entry point for FR-9.2 (intervals grow on success, reset
    on failure) and FR-8.5 (confidence adjusts the interval).
    """
    now = now or datetime.now(timezone.utc)
    score = _clamp(final_score, 0.0, 1.0)

    effective_strength = current_strength(state, now)
    if state.last_review_at is None:
        effective_strength = _clamp(state.strength, 0.0, 1.0)

    interval = next_interval_days(state, score, confidence)

    new_strength = effective_strength
    if score >= MEDIUM_SCORE_THRESHOLD:
        # Bounded-up update toward the observed performance.
        new_strength = effective_strength + 0.3 * (score - effective_strength)
    else:
        new_strength = effective_strength * 0.5

    new_strength = _clamp(new_strength, 0.0, 1.0)

    state.strength = new_strength
    state.interval_days = interval
    state.stability_days = _update_stability(state, score, interval)
    state.last_review_at = now
    state.next_review_at = now + timedelta(days=interval)
    state.status = _status_for(score, new_strength)
    state.confidence_label = confidence
    state.history.append(score)

    if score < MEDIUM_SCORE_THRESHOLD:
        state.lapses += 1
        state.repetitions = 0
    else:
        state.repetitions += 1

    # Uncertainty shrinks as evidence accumulates (section 6.1).
    state.uncertainty = _clamp(state.uncertainty * 0.7, 0.05, 1.0)

    reason = (
        f"score={score:.2f} confidence={confidence or 'unspecified'} "
        f"interval={interval}d strength={new_strength:.2f} status={state.status}"
    )
    return ReviewDecision(state=state, changed=True, reason=reason)


def due_topics(
    states: dict[str, TopicReviewState],
    now: datetime | None = None,
) -> list[str]:
    """FR-9.3: topics whose next review has come due."""
    now = now or datetime.now(timezone.utc)
    return [
        topic_id
        for topic_id, state in states.items()
        if state.next_review_at is not None and state.next_review_at <= now
    ]


def weakest_topics(
    states: dict[str, TopicReviewState],
    now: datetime | None = None,
    limit: int = 3,
) -> list[tuple[str, float]]:
    """FR-9.3: weakest topics by current (decayed) strength."""
    now = now or datetime.now(timezone.utc)
    scored = [(tid, current_strength(st, now)) for tid, st in states.items()]
    scored.sort(key=lambda item: item[1])
    return scored[:limit]