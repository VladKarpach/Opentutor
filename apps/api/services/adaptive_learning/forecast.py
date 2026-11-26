"""Readiness forecast — TZ section 6.7 and FR-12.4.

Section 6.7: estimate the probability of finishing on time from the remaining
volume, the current speed, the share of weak topics and the time buffer, and
report it as high / medium / low together with the reasons.

Pure arithmetic, no LLM: the model contributes content, this module contributes
the verdict (same split as section 6.4).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date

from services.adaptive_learning.repetition import (
    MEDIUM_SCORE_THRESHOLD,
    TopicReviewState,
    current_strength,
)

logger = logging.getLogger(__name__)

# A topic is "weak" below this current strength (mirrors the plan builder).
WEAK_THRESHOLD = MEDIUM_SCORE_THRESHOLD

# Weights for the readiness score. Tuned so that the dominant factor is the
# time buffer, with speed and weakness adjusting it.
WEIGHT_BUDGET = 0.5
WEIGHT_SPEED = 0.2
WEIGHT_WEAKNESS = 0.3

HIGH_THRESHOLD = 0.7
MEDIUM_THRESHOLD = 0.4


@dataclass
class ForecastInput:
    """Everything the forecast needs, already aggregated by the caller."""

    remaining_minutes: int
    """Estimated minutes still needed for all topics."""
    minutes_per_day: int
    days_left: int
    speed: float = 1.0
    """Section 6.5 coefficient: >1 means the learner is slower than planned."""
    states: dict[str, TopicReviewState] = field(default_factory=dict)
    topic_ids: list[str] = field(default_factory=list)


@dataclass
class ForecastResult:
    """Readiness verdict plus the reasons behind it."""

    level: str  # high | medium | low
    probability: float
    reasons: list[str]
    required_speed: float
    feasible: bool

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "probability": round(self.probability, 3),
            "reasons": self.reasons,
            "required_speed": round(self.required_speed, 2),
            "feasible": self.feasible,
        }


def required_speed(focus: ForecastInput) -> float:
    """How much faster than the current pace the learner must be to finish.

    < 1 means the remaining work fits in the time left at the current pace.
    """
    available = max(1, focus.days_left) * max(1, focus.minutes_per_day)
    effective_speed = max(0.1, focus.speed)
    # Time needed at the current pace = remaining / speed.
    needed = focus.remaining_minutes / effective_speed
    if needed <= 0:
        return 1.0
    return available / needed


def _weak_ratio(focus: ForecastInput) -> float:
    if not focus.topic_ids:
        return 0.0
    weak = sum(
        1
        for tid in focus.topic_ids
        if (focus.states.get(tid) and current_strength(focus.states[tid]) < WEAK_THRESHOLD)
    )
    return weak / len(focus.topic_ids)


def forecast_readiness(focus: ForecastInput) -> ForecastResult:
    """Section 6.7 / FR-12.4."""
    available = max(0, focus.days_left) * max(1, focus.minutes_per_day)
    speed = required_speed(focus)
    weak = _weak_ratio(focus)

    reasons: list[str] = []

    # 1. Time buffer: linear ramp from "nothing fits" to "double the buffer".
    if focus.remaining_minutes <= 0:
        budget_score = 1.0
    elif available <= 0:
        budget_score = 0.0
    else:
        budget_score = _clamp(focus.remaining_minutes / available, 0.0, 1.0)

    # 2. Speed: a required_speed of 1.0 (exactly on pace) scores full marks.
    speed_score = _clamp(speed / 2.0, 0.0, 1.0)

    probability = _clamp(
        (1.0 - budget_score) * WEIGHT_BUDGET
        + speed_score * WEIGHT_SPEED
        + (1.0 - weak) * WEIGHT_WEAKNESS,
        0.0,
        1.0,
    )

    feasible = speed >= 1.0

    if not feasible:
        need = max(1, math.ceil(speed * 100))
        reasons.append(
            f"Потребуется ускориться примерно в {need}% от текущего темпа, "
            "чтобы уложиться в срок."
        )
    else:
        reasons.append("Текущего темпа достаточно, чтобы уложиться в срок.")

    if weak > 0.34:
        reasons.append(
            f"Слабых тем {round(weak * 100)}% — план стоит пересмотреть в сторону повторений."
        )
    elif weak > 0.0:
        reasons.append(f"Слабых тем {round(weak * 100)}%, остальное в порядке.")

    if budget_score < 0.5:
        reasons.append("Оставшегося времени мало относительно объёма работы.")
    elif budget_score < 0.85:
        reasons.append("Запас по времени небольшой, риск сдвинуть срок.")

    if probability >= HIGH_THRESHOLD:
        level = "high"
    elif probability >= MEDIUM_THRESHOLD:
        level = "medium"
    else:
        level = "low"

    logger.info(
        "Readiness forecast: level=%s probability=%.2f required_speed=%.2f weak=%.0f%%",
        level, probability, speed, weak * 100,
    )

    return ForecastResult(
        level=level,
        probability=probability,
        reasons=reasons,
        required_speed=speed,
        feasible=feasible,
    )


def plan_review_day(
    states: dict[str, TopicReviewState],
    today: date,
    horizon_days: int = 3,
) -> list[str]:
    """FR-11.4: suggest taking the exam 2-3 days before the deadline.

    Returns the subtopics that would still be unreviewed by then, so the caller
    can decide whether to recommend the exam.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime(
        today.year, today.month, today.day, tzinfo=timezone.utc
    ) + timedelta(days=horizon_days)
    return [
        tid
        for tid, st in states.items()
        if st.next_review_at is None or st.next_review_at > cutoff
    ]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))