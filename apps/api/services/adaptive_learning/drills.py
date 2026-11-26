"""Practice series per subtopic, and the gate in front of the exam.

The flow this implements: the plan says what to study; each subtopic carries a
short series of tasks with a rising difficulty; the learner works the series; a
goal whose series are all complete opens the final exam.

Difficulty is assigned in code, not asked of the model. The model is told which
difficulty to write at and produces the wording and the grading criteria — same
split as section 6.4, where content comes from the model and arithmetic from us.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.learning_goal import (
    LearningAnswer,
    LearningGoal,
    LearningQuestion,
    LearningTopic,
    TopicDrill,
)
from schemas.learning import MAX_HINTS
from services.adaptive_learning import content, task_formats

logger = logging.getLogger(__name__)

# "2-3 tasks per topic": an overview only needs two, a proper goal three.
REQUIRED_BY_DEPTH: dict[str, int] = {
    "overview": 2,
    "confident": 3,
    "deep": 3,
}

# The ramp. A learner should feel the series get harder, not random.
DIFFICULTY_RAMP: tuple[float, ...] = (0.3, 0.55, 0.8)

# FR-11.3 / auto-completion: the exam has to be genuinely good, not merely taken.
EXAM_PASS_SCORE = 0.7


class DrillNotReady(RuntimeError):
    """The goal's practice series are not complete, so the exam stays shut."""


def required_for(depth: str | None) -> int:
    return REQUIRED_BY_DEPTH.get((depth or "confident"), 3)


def difficulty_for(index: int) -> float:
    """Difficulty of the index-th task in a series, clamped to the ramp."""
    if index < len(DIFFICULTY_RAMP):
        return DIFFICULTY_RAMP[index]
    return DIFFICULTY_RAMP[-1]


async def ensure_drill(
    db: AsyncSession,
    goal: LearningGoal,
    topic: LearningTopic,
    language: str = "ru",
) -> tuple[TopicDrill, bool]:
    """Return this topic's series, generating it on first use.

    The second element is True when questions were just created, which is the
    caller's signal that the request was slow and worth telling the user about.
    """
    existing = await db.execute(
        select(TopicDrill).where(
            TopicDrill.goal_id == goal.id,
            TopicDrill.topic_id == topic.id,
        )
    )
    found = existing.scalar_one_or_none()
    if found:
        return found, False

    required = required_for(goal.depth)

    # Recent question types are avoided so the series is not three of a kind.
    recent = list(
        (
            await db.execute(
                select(LearningQuestion.question_type)
                .where(LearningQuestion.topic_id == topic.id)
                .order_by(LearningQuestion.created_at.desc())
                .limit(3)
            )
        ).scalars().all()
    )
    last_hints = max(MAX_HINTS, required)

    created: list[LearningQuestion] = []
    for index in range(required):
        difficulty = difficulty_for(index)
        try:
            payload = await content.generate_task(
                topic_title=topic.title,
                difficulty=difficulty,
                avoid_types=recent,
                language=language,
                prior_context="",
            )
        except content.ContentGenerationError as exc:
            # A partial series is worse than none: the learner would see a
            # half-empty list and the exam gate would still be shut.
            for question in created:
                await db.delete(question)
            await db.flush()
            raise RuntimeError(
                f"Не удалось подготовить задания по теме «{topic.title}»: {exc}"
            ) from exc

        recent.append(payload["question_type"])
        question = LearningQuestion(
            topic_id=topic.id,
            question_type=payload["question_type"],
            text=payload["text"],
            criteria=payload["criteria"],
            difficulty=difficulty,
            hints=payload["hints"][:last_hints],
            # How the learner replies, and what that format needs to be checked.
            # Both default to the prose case, so a generator that predates this
            # still produces a task that can be answered.
            format=payload.get("format") or task_formats.DEFAULT_FORMAT,
            payload=payload.get("payload") or {},
        )
        db.add(question)
        created.append(question)

    await db.flush()
    for question in created:
        await db.refresh(question)

    drill = TopicDrill(
        goal_id=goal.id,
        user_id=goal.user_id,
        topic_id=topic.id,
        required_count=required,
        question_ids=[str(q.id) for q in created],
        status="pending",
    )
    db.add(drill)
    await db.commit()
    await db.refresh(drill)

    logger.info(
        "Drill for topic %s: %s task(s), ramp=%s",
        topic.id, required, [difficulty_for(i) for i in range(required)],
    )
    return drill, True


async def progress_map(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, dict]:
    """Per-topic progress: how many of the required tasks were answered.

    Keyed by topic id as a string so the caller can look it up while iterating
    topics. Topics with no drill yet report ``prepared: False`` and zero counts,
    which is what the merged topic/plan list renders as "not prepared yet".
    """
    drills = list(
        (
            await db.execute(
                select(TopicDrill).where(
                    TopicDrill.goal_id == goal_id,
                    TopicDrill.user_id == user_id,
                )
            )
        ).scalars().all()
    )
    if not drills:
        return {}

    all_ids = [uuid.UUID(str(qid)) for d in drills for qid in (d.question_ids or [])]
    graded: set[uuid.UUID] = set()
    if all_ids:
        rows = await db.execute(
            select(LearningAnswer.question_id).where(
                LearningAnswer.user_id == user_id,
                LearningAnswer.question_id.in_(all_ids),
            )
        )
        graded = {row[0] for row in rows.all()}

    out: dict[str, dict] = {}
    for drill in drills:
        ids = [uuid.UUID(str(qid)) for qid in (drill.question_ids or [])]
        answered = [qid for qid in ids if qid in graded]
        done = len(answered) >= drill.required_count
        out[str(drill.topic_id)] = {
            "drill_id": str(drill.id),
            "required": drill.required_count,
            "answered": len(answered),
            "answered_question_ids": [str(q) for q in answered],
            "remaining_question_ids": [str(q) for q in ids if q not in graded],
            "done": done,
            "prepared": True,
            "status": "completed" if done else ("in_progress" if answered else "pending"),
        }
    return out


async def refresh_status(db: AsyncSession, drill: TopicDrill) -> None:
    """Recompute a drill's status after a new answer and persist it."""
    prog = await progress_map(db, drill.goal_id, drill.user_id)
    info = prog.get(str(drill.topic_id))
    if not info:
        return
    drill.status = info["status"]
    if info["done"] and drill.completed_at is None:
        drill.completed_at = datetime.now(timezone.utc)


async def goal_gate(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> dict:
    """Exam gate for a whole goal.

    The exam is open once every subtopic's series is complete. The response says
    exactly what is missing so the UI can say something useful instead of just
    refusing.
    """
    topics = list(
        (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id == goal_id)
            )
        ).scalars().all()
    )
    prog = await progress_map(db, goal_id, user_id)

    pending: list[dict] = []
    for topic in topics:
        info = prog.get(str(topic.id))
        if info is None:
            pending.append(
                {
                    "topic_id": str(topic.id),
                    "title": topic.title,
                    "reason": "not_prepared",
                    "required": required_for(None),
                    "answered": 0,
                }
            )
        elif not info["done"]:
            pending.append(
                {
                    "topic_id": str(topic.id),
                    "title": topic.title,
                    "reason": "unanswered_tasks",
                    "required": info["required"],
                    "answered": info["answered"],
                }
            )

    return {
        "topics_total": len(topics),
        "topics_done": len(topics) - len(pending),
        "unlocked": not pending and bool(topics),
        "pending": pending,
    }


def completion_for_score(total_score: float) -> bool:
    """Auto-completion rule: a passed exam completes the goal.

    Kept as one function so the threshold is visible and testable rather than
    scattered through the endpoint.
    """
    return float(total_score) >= EXAM_PASS_SCORE