"""Answer grading helpers — TZ sections 6.2 and 6.6.

Section 6.2 defines the final score as:

    final = score * (1 - hint_penalty)

where the penalty grows with the number of hints used (FR-8.6: three levels,
each one lowering the grade by a set coefficient).

The LLM supplies the criterion breakdown; this module turns that into the final
score, applies the hint penalty and normalises the error taxonomy so the error
analysis (section 6.6) has something stable to aggregate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Section 6.2 grading criteria, FR-8.4.
CRITERIA = ("correctness", "completeness", "understanding")

# Section 6.2: criteria are fixed when the question is created so the grading
# stays stable. FR-12 asks for a spread of at most 0.15 between repeats, which
# this fixed-criteria rule is what enables.
GRADING_TEMPERATURE = 0.0

# FR-8.6: three hint levels (nudge -> direction -> almost the solution).
# Each level costs the learner part of the grade.
HINT_PENALTY_PER_LEVEL = 0.2
MAX_HINT_PENALTY = 0.6

# Section 6.6 error taxonomy.
ERROR_TYPES = (
    "concept_confusion",   # путаница понятий
    "missing_step",        # пропуск шага
    "wrong_application",   # неверное применение
    "incomplete",          # неполный ответ
)

# FR-8.7: what "disputed and upheld" means for quality tracking.
DISPUTE_RECHECK_DELTA = 0.15

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def hint_penalty(hints_used: int) -> float:
    """FR-8.6: linear penalty per hint level, capped."""
    if hints_used <= 0:
        return 0.0
    return min(MAX_HINT_PENALTY, HINT_PENALTY_PER_LEVEL * hints_used)


@dataclass
class GradeResult:
    """Outcome of scoring one answer.

    Two scores, deliberately. ``final_score`` is what the learner achieved and is
    what they are shown — discounting their own attempt back at them would be a
    lie about their work. ``mastery_score`` is the same answer discounted by how
    sure they were, and that is what the memory system learns from.
    """

    raw_score: float
    final_score: float
    breakdown: dict[str, float]
    hint_penalty: float
    error_types: list[str]
    feedback: str = ""
    unstable: bool = False
    warnings: list[str] = field(default_factory=list)
    mastery_score: float = 0.0


def average_breakdown(breakdown: dict) -> float:
    """Mean of the criteria present, ignoring absent ones."""
    values = [
        _clamp(float(breakdown[key]), 0.0, 1.0)
        for key in CRITERIA
        if key in breakdown and breakdown[key] is not None
    ]
    if not values:
        return 0.0
    return sum(values) / len(values)


def score_answer(
    breakdown: dict,
    *,
    hints_used: int = 0,
    feedback: str = "",
    reported_errors: list[str] | None = None,
) -> GradeResult:
    """Turn a criterion breakdown into the graded outcome (section 6.2).

    Mastery is derived purely from the objective result — correctness, difficulty,
    hints used and time — never from a self-declared confidence.
    """
    warnings: list[str] = []

    clean = {
        key: _clamp(float(value), 0.0, 1.0)
        for key, value in breakdown.items()
        if key in CRITERIA and value is not None
    }

    if not clean:
        warnings.append("no criteria in the breakdown; scored as 0")
        return GradeResult(
            raw_score=0.0,
            final_score=0.0,
            mastery_score=0.0,
            breakdown={},
            hint_penalty=0.0,
            error_types=[],
            feedback=feedback,
            warnings=warnings,
        )

    missing = [key for key in CRITERIA if key not in clean]
    if missing:
        # Section 12 wants stable grades, so flag rather than silently invent.
        warnings.append(f"missing criteria: {', '.join(missing)}")

    raw = average_breakdown(clean)
    penalty = hint_penalty(hints_used)
    final = _clamp(raw * (1.0 - penalty), 0.0, 1.0)
    mastery = final

    errors = [e for e in (reported_errors or []) if e in ERROR_TYPES]
    unknown = [e for e in (reported_errors or []) if e not in ERROR_TYPES]
    if unknown:
        warnings.append(f"unknown error types ignored: {', '.join(sorted(set(unknown)))}")

    logger.debug(
        "Scored answer raw=%.2f penalty=%.2f final=%.2f mastery=%.2f",
        raw, penalty, final, mastery,
    )

    return GradeResult(
        raw_score=round(raw, 4),
        final_score=round(final, 4),
        mastery_score=round(mastery, 4),
        breakdown=clean,
        hint_penalty=round(penalty, 4),
        error_types=errors,
        feedback=feedback,
        warnings=warnings,
    )


def is_stable(previous: float, current: float) -> bool:
    """Section 12: re-grading the same answer must not vary by more than 0.15."""
    return abs(previous - current) <= DISPUTE_RECHECK_DELTA


def summarise_errors(
    answers: list[dict],
    window: int = 20,
) -> dict:
    """Section 6.6 / FR-12.3: aggregate the error taxonomy for the summary.

    ``answers`` is a list of dicts with at least ``score`` and ``error_types``.
    """
    recent = answers[-window:] if window else list(answers)
    counts: dict[str, int] = {key: 0 for key in ERROR_TYPES}
    for item in recent:
        for err in item.get("error_types") or []:
            if err in counts:
                counts[err] += 1

    scored = [float(a.get("score") or 0.0) for a in recent if a.get("score") is not None]
    mean = sum(scored) / len(scored) if scored else 0.0

    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(counts.values())

    summary = {
        "window": len(recent),
        "counts": counts,
        "mean_score": round(mean, 4),
        "dominant_error": top[0][0] if total and top[0][1] else "",
        # FR-12.3: "often confuses X and Y" / "weak spot: application, not definitions".
        "observation": _observation(top, total, mean),
    }
    logger.info(
        "Error summary over %s answer(s): dominant=%s mean=%.2f",
        len(recent), summary["dominant_error"] or "none", mean,
    )
    return summary


_RU_LABELS = {
    "concept_confusion": "путаете понятия",
    "missing_step": "пропускаете шаг",
    "wrong_application": "ошибка в применении",
    "incomplete": "ответ неполный",
}


def _observation(top: list[tuple[str, int]], total: int, mean: float) -> str:
    if not total:
        return "Ошибок пока не зафиксировано."
    dominant, count = top[0]
    share = count / total
    if share >= 0.4:
        return f"Чаще всего вы {_RU_LABELS.get(dominant, dominant)} — это {share:.0%} всех ошибок."
    if mean < 0.5:
        return f"Слабое место: {_RU_LABELS.get(dominant, dominant)}. Средняя оценка {mean:.2f}."
    return f"Ошибки распределены равномерно, слабых мест не выделяется."