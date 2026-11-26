"""Progress history and error analysis — TZ FR-12.1 and FR-12.3.

FR-12.1 asks for a strength-over-time graph. TZ section 8 only stores the
current strength, so :class:`TopicStrengthPoint` is appended by
``persistence.apply_scored_answer`` and this module just reads it back.

FR-12.3 / section 6.6: every N graded answers the error statistics are counted
in code (``answer_scoring.summarise_errors``) and the model turns them into a
readable summary. If the model call fails the counts are still stored, so the
screen never shows an empty box just because the LLM was down.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.learning_goal import (
    ErrorSummary,
    LearningAnswer,
    LearningQuestion,
    LearningTopic,
    TopicStrengthPoint,
)
from services.adaptive_learning import answer_scoring, content
from services.adaptive_learning.repetition import MEDIUM_SCORE_THRESHOLD

logger = logging.getLogger(__name__)

# Section 6.6: "раз в N проверок". Five is short enough that the advice stays
# relevant and long enough that the counts mean something.
SUMMARISE_EVERY = 5

# How many recent answers the statistics window covers.
ANALYSIS_WINDOW = 20

# A topic counts as weak here when its mean score is below this.
WEAK_TOPIC_SCORE = MEDIUM_SCORE_THRESHOLD


# ──────────────────────────────────────────────────────────────
# FR-12.1: strength over time
# ──────────────────────────────────────────────────────────────


async def strength_series(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    topic_id: uuid.UUID | None = None,
    days: int | None = None,
) -> dict:
    """Return the recorded strength points plus a goal-level average.

    The goal-level curve averages the topics that had a point at each timestamp,
    which is what the chart draws as the headline line. Per-topic series come
    back too so the same screen can break the line down.
    """
    query = select(TopicStrengthPoint).where(
        TopicStrengthPoint.goal_id == goal_id,
        TopicStrengthPoint.user_id == user_id,
    )
    if topic_id is not None:
        query = query.where(TopicStrengthPoint.topic_id == topic_id)
    if days:
        query = query.where(
            TopicStrengthPoint.recorded_at
            >= datetime.now(timezone.utc) - timedelta(days=days)
        )
    points = list(
        (await db.execute(query.order_by(TopicStrengthPoint.recorded_at))).scalars().all()
    )

    titles = await topic_titles(db, goal_id)

    by_topic: dict[str, list[dict]] = {}
    for point in points:
        by_topic.setdefault(str(point.topic_id), []).append(
            {
                "at": point.recorded_at.isoformat() if point.recorded_at else None,
                "strength": round(point.strength, 3),
                "interval_days": point.interval_days,
                "was_lapse": bool(point.was_lapse),
            }
        )

    series = [
        {
            "topic_id": key,
            "title": titles.get(key, ""),
            "points": values,
            "current": values[-1]["strength"] if values else 0.0,
        }
        for key, values in by_topic.items()
    ]
    series.sort(key=lambda s: s["current"])

    return {
        "goal_id": str(goal_id),
        "overall": _average_curve(points),
        "by_topic": series,
        "total_points": len(points),
        "topic_count": len(series),
    }


def _average_curve(points: list[TopicStrengthPoint]) -> list[dict]:
    """Collapse per-topic points into one average line over time.

    Points that share a recorded_at are averaged together, so a burst of answers
    across several topics shows as a single step instead of a sawtooth.
    """
    buckets: dict[str, list[float]] = {}
    lapses: dict[str, int] = {}
    for point in points:
        key = point.recorded_at.isoformat() if point.recorded_at else ""
        buckets.setdefault(key, []).append(point.strength)
        if point.was_lapse:
            lapses[key] = lapses.get(key, 0) + 1

    curve = [
        {
            "at": key,
            "strength": round(sum(values) / len(values), 3),
            "samples": len(values),
            "lapses": lapses.get(key, 0),
        }
        for key, values in sorted(buckets.items())
    ]
    return curve


# ──────────────────────────────────────────────────────────────
# FR-12.3: error analysis
# ──────────────────────────────────────────────────────────────


async def topic_titles(db: AsyncSession, goal_id: uuid.UUID) -> dict[str, str]:
    rows = list(
        (
            await db.execute(
                select(LearningTopic.id, LearningTopic.title).where(
                    LearningTopic.goal_id == goal_id
                )
            )
        ).all()
    )
    return {str(row[0]): row[1] for row in rows}


async def _recent_answers(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    limit: int = ANALYSIS_WINDOW,
) -> list[dict]:
    """Answers for this goal's questions, newest first in SQL then reversed."""
    rows = list(
        (
            await db.execute(
                select(
                    LearningAnswer.score,
                    LearningAnswer.error_types,
                    LearningAnswer.created_at,
                    LearningQuestion.text,
                    LearningQuestion.topic_id,
                    LearningTopic.title,
                )
                .join(LearningQuestion, LearningQuestion.id == LearningAnswer.question_id)
                .join(LearningTopic, LearningTopic.id == LearningQuestion.topic_id)
                .where(
                    LearningQuestion.topic_id.in_(
                        select(LearningTopic.id).where(LearningTopic.goal_id == goal_id)
                    ),
                    LearningAnswer.user_id == user_id,
                )
                .order_by(LearningAnswer.created_at.desc())
                .limit(limit)
            )
        ).all()
    )

    # Oldest first so `summarise_errors(window=...)` keeps the most recent ones.
    return [
        {
            "score": row[0],
            "error_types": list(row[1] or []),
            "at": row[2],
            "question": row[3],
            "topic_id": str(row[4]),
            "title": row[5],
        }
        for row in reversed(rows)
    ]


def _per_topic_weakness(answers: list[dict]) -> list[dict]:
    """Mean score and dominant error per topic, weakest first.

    FR-12.3 wants to name the weak spot, so each topic also carries the error
    type that dominates it rather than only a number.
    """
    grouped: dict[str, dict] = {}
    for item in answers:
        entry = grouped.setdefault(
            item["topic_id"], {"scores": [], "errors": {}, "title": item.get("title", "")}
        )
        if item.get("score") is not None:
            entry["scores"].append(float(item["score"]))
        for err in item.get("error_types") or []:
            entry["errors"][err] = entry["errors"].get(err, 0) + 1

    out = []
    for topic_id, entry in grouped.items():
        scores = entry["scores"]
        mean = sum(scores) / len(scores) if scores else 0.0
        top_error = (
            max(entry["errors"].items(), key=lambda kv: kv[1])[0]
            if entry["errors"]
            else ""
        )
        out.append(
            {
                "topic_id": topic_id,
                "title": entry["title"],
                "mean_score": round(mean, 3),
                "answers": len(scores),
                "dominant_error": top_error,
                "weak": mean < WEAK_TOPIC_SCORE,
            }
        )
    out.sort(key=lambda e: e["mean_score"])
    return out


async def collect_stats(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[dict, list[dict]]:
    """Deterministic half of FR-12.3: counted statistics, no model involved."""
    answers = await _recent_answers(db, goal_id, user_id)
    summary = answer_scoring.summarise_errors(answers, window=ANALYSIS_WINDOW)
    summary["by_topic"] = _per_topic_weakness(answers)
    summary["weak_topics"] = [e["topic_id"] for e in summary["by_topic"] if e["weak"]]
    summary["total"] = len(answers)
    return summary, answers


async def answers_since_last_summary(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    """How many graded answers arrived after the newest stored summary.

    With no summary yet, every answer counts — otherwise the very first summary
    would never become due, because there is no timestamp to measure from.
    """
    last = await db.execute(
        select(func.max(ErrorSummary.generated_at)).where(
            ErrorSummary.goal_id == goal_id,
            ErrorSummary.user_id == user_id,
        )
    )
    newest = last.scalar()

    query = (
        select(func.count(LearningAnswer.id))
        .join(LearningQuestion, LearningQuestion.id == LearningAnswer.question_id)
        .where(
            LearningQuestion.topic_id.in_(
                select(LearningTopic.id).where(LearningTopic.goal_id == goal_id)
            ),
            LearningAnswer.user_id == user_id,
        )
    )
    if newest is not None:
        query = query.where(LearningAnswer.created_at > newest)

    counted = await db.execute(query)
    return int(counted.scalar() or 0)


async def is_due(db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Section 6.6: a summary is due once N more answers have been graded."""
    return (
        await answers_since_last_summary(db, goal_id, user_id)
    ) >= SUMMARISE_EVERY


async def generate_summary(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    force: bool = False,
    language: str = "ru",
) -> ErrorSummary | None:
    """Build and store an error summary. Returns None when nothing to report."""
    if not force and not await is_due(db, goal_id, user_id):
        return None

    stats, answers = await collect_stats(db, goal_id, user_id)
    if not answers:
        return None

    now = datetime.now(timezone.utc)
    period_start = min(
        (a["at"] for a in answers if a.get("at")), default=now - timedelta(days=7)
    )

    summary_text: str | None = stats.get("observation")
    recommendations: list[str] = []
    confusions: list[str] = []

    # The deterministic observation above is always available; the model only
    # improves on it. A failure here must not lose the counts.
    try:
        written = await content.summarise_error_patterns(
            stats={
                "counts": stats.get("counts", {}),
                "mean_score": stats.get("mean_score"),
                "dominant_error": stats.get("dominant_error"),
                "weak_topics": [t["title"] for t in stats.get("by_topic", []) if t["weak"]][:5],
                "by_topic": [
                    {"title": t["title"], "mean_score": t["mean_score"],
                     "dominant_error": t["dominant_error"]}
                    for t in stats.get("by_topic", [])[:6]
                ],
            },
            examples=[
                {
                    "title": a.get("title"),
                    "error_types": a.get("error_types"),
                    "score": a.get("score"),
                    "question": a.get("question"),
                }
                for a in answers
                if a.get("score") is not None and float(a["score"]) < 0.6
            ],
            language=language,
        )
        summary_text = written["summary"] or summary_text
        recommendations = written["recommendations"]
        confusions = written["confusions"]
    except Exception as exc:  # noqa: BLE001 - counts must survive an LLM outage
        logger.warning("Error summary wording failed, keeping counts: %s", exc)

    stats = {**stats, "confusions": confusions}
    row = ErrorSummary(
        goal_id=goal_id,
        user_id=user_id,
        period_start=period_start,
        period_end=now,
        summary_text=summary_text,
        stats=stats,
        recommendations=recommendations,
        answer_count=len(answers),
        generated_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    logger.info(
        "Stored error summary for goal %s over %s answer(s)", goal_id, len(answers)
    )
    return row


def to_response(row: ErrorSummary) -> dict:
    return {
        "id": str(row.id),
        "goal_id": str(row.goal_id),
        "period_start": row.period_start.isoformat() if row.period_start else None,
        "period_end": row.period_end.isoformat() if row.period_end else None,
        "summary_text": row.summary_text,
        "recommendations": list(row.recommendations or []),
        "answer_count": row.answer_count,
        "stats": row.stats or {},
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }


async def list_summaries(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID, *, limit: int = 10
) -> list[ErrorSummary]:
    return list(
        (
            await db.execute(
                select(ErrorSummary)
                .where(
                    ErrorSummary.goal_id == goal_id,
                    ErrorSummary.user_id == user_id,
                )
                .order_by(ErrorSummary.generated_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    )