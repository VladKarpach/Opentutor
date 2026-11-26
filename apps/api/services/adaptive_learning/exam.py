"""Exam composition and grading — TZ FR-11.

FR-11.1: the exam covers every topic, weighted towards the weak ones.
FR-11.2: two modes — "training" (hints allowed, no timer) and "strict"
(timer, hints disabled).
FR-11.3: the result gives an overall mark, per-topic scores, recommendations
for the remaining time, and feeds the planner.

Composition is deterministic, so a given goal state always produces the same
exam shape. Question wording comes from the model when the questions are
generated, not here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.learning_goal import (
    Exam,
    LearningQuestion,
    LearningTopic,
    TopicState,
)
from services.adaptive_learning.repetition import current_strength

logger = logging.getLogger(__name__)

MODE_TRAINING = "training"
MODE_STRICT = "strict"

# FR-11.2: a strict exam is timed. One minute per question by default.
SECONDS_PER_QUESTION = 60
MAX_QUESTIONS = 20
MIN_QUESTIONS = 3

# FR-11.1: topics below this strength count as weak.
WEAK_THRESHOLD = 0.7
WEAK_SHARE = 0.7

QUESTIONS_PER_STRONG_TOPIC = 1
QUESTIONS_PER_WEAK_TOPIC = 2


class ExamUnavailable(RuntimeError):
    """Not enough stored questions to build a meaningful exam."""


async def _load_questions(
    db: AsyncSession, exam: Exam
) -> list[LearningQuestion]:
    """Resolve the exam's stored question ids, preserving order."""
    ids = [uuid.UUID(str(q)) for q in (exam.question_ids or [])]
    if not ids:
        return []
    rows = list(
        (await db.execute(
            select(LearningQuestion).where(LearningQuestion.id.in_(ids))
        )).scalars().all()
    )
    by_id = {row.id: row for row in rows}
    return [by_id[i] for i in ids if i in by_id]


async def build_exam(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    mode: str = MODE_TRAINING,
    time_limit_seconds: int | None = None,
) -> Exam:
    """Select questions for an exam and persist the run."""
    topics = list(
        (await db.execute(
            select(LearningTopic)
            .where(LearningTopic.goal_id == goal_id)
            .order_by(LearningTopic.order_index)
        )).scalars().all()
    )
    if not topics:
        raise ExamUnavailable("Add subtopics before taking an exam")

    states = {
        str(row.topic_id): row
        for row in (await db.execute(
            select(TopicState).where(
                TopicState.goal_id == goal_id,
                TopicState.user_id == user_id,
            )
        )).scalars().all()
    }

    rows = list(
        (await db.execute(
            select(LearningQuestion).where(
                LearningQuestion.topic_id.in_([t.id for t in topics])
            )
        )).scalars().all()
    )
    # Oldest first, so earlier material gets retested first.
    by_topic: dict[str, list[LearningQuestion]] = {}
    for question in sorted(rows, key=lambda q: (q.created_at is None, q.created_at)):
        by_topic.setdefault(str(question.topic_id), []).append(question)

    weak: list[str] = []
    strong: list[str] = []
    for topic in topics:
        key = str(topic.id)
        if not by_topic.get(key):
            continue  # topic with no questions cannot be examined
        state = states.get(key)
        strength = current_strength(state) if state else 0.0
        (weak if strength < WEAK_THRESHOLD else strong).append(key)

    if not weak and not strong:
        raise ExamUnavailable(
            "No questions for this goal yet. Generate questions per topic first."
        )

    total_target = min(
        MAX_QUESTIONS,
        max(
            MIN_QUESTIONS,
            len(weak) * QUESTIONS_PER_WEAK_TOPIC + len(strong) * QUESTIONS_PER_STRONG_TOPIC,
        ),
    )

    selected: list[LearningQuestion] = []
    used: set[uuid.UUID] = set()

    def take(topic_id: str, count: int) -> None:
        for question in by_topic.get(topic_id, []):
            if len(selected) >= total_target or count <= 0:
                return
            if question.id in used:
                continue
            used.add(question.id)
            selected.append(question)
            count -= 1

    # FR-11.1: weak topics are loaded first and twice over.
    for topic_id in weak:
        take(topic_id, QUESTIONS_PER_WEAK_TOPIC)
    for topic_id in strong:
        take(topic_id, QUESTIONS_PER_STRONG_TOPIC)
    # Top up from anything still unused, so a thin topic does not shrink the exam.
    for topic_id in weak + strong:
        take(topic_id, 1)

    if len(selected) < MIN_QUESTIONS:
        raise ExamUnavailable(
            f"Only {len(selected)} question(s) available; at least {MIN_QUESTIONS} are "
            "needed. Generate more questions per topic."
        )

    limit = time_limit_seconds
    if mode == MODE_STRICT and not limit:
        limit = SECONDS_PER_QUESTION * len(selected)

    exam = Exam(
        goal_id=goal_id,
        user_id=user_id,
        mode=mode,
        status="in_progress",
        time_limit_seconds=limit,
        question_ids=[str(q.id) for q in selected],
        answers=[],
        answered_count=0,
    )
    db.add(exam)
    await db.commit()
    await db.refresh(exam)

    logger.info(
        "Exam %s for goal %s: %s question(s), mode=%s, limit=%s",
        exam.id, goal_id, len(selected), mode, limit,
    )
    return exam


def remaining_seconds(exam: Exam) -> int | None:
    """FR-11.2: wall-clock budget left, or None when untimed."""
    if not exam.time_limit_seconds:
        return None
    started = exam.started_at or datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return max(0, int(exam.time_limit_seconds - elapsed))


def is_expired(exam: Exam) -> bool:
    left = remaining_seconds(exam)
    return left is not None and left <= 0


async def topic_title_map(db: AsyncSession, goal_id: uuid.UUID) -> dict[str, str]:
    rows = list(
        (await db.execute(
            select(LearningTopic).where(LearningTopic.goal_id == goal_id)
        )).scalars().all()
    )
    return {str(t.id): t.title for t in rows}


async def summarise(db: AsyncSession, exam: Exam) -> dict:
    """FR-11.3: overall mark, per-topic scores and recommendations."""
    questions = await _load_questions(db, exam)
    titles = await topic_title_map(db, exam.goal_id)
    answers = list(exam.answers or [])
    by_question = {str(a.get("question_id")): a for a in answers}

    scored = [a for a in answers if a.get("score") is not None]
    total = mean([float(a["score"]) for a in scored]) if scored else 0.0

    per_topic: dict[str, dict] = {}
    for question in questions:
        key = str(question.topic_id)
        entry = per_topic.setdefault(
            key, {"topic_id": key, "title": titles.get(key, ""), "scores": []}
        )
        answer = by_question.get(str(question.id))
        if answer and answer.get("score") is not None:
            entry["scores"].append(float(answer["score"]))

    by_topic_list = [
        {**entry, "score": round(mean(entry["scores"]), 3) if entry["scores"] else 0.0}
        for entry in per_topic.values()
    ]
    by_topic_list.sort(key=lambda e: e["score"])

    weak_topics = [e["topic_id"] for e in by_topic_list if e["score"] < WEAK_THRESHOLD]

    recommendations: list[str] = []
    if weak_topics:
        titles_preview = ", ".join(f"«{e['title']}»" for e in by_topic_list[:3])
        recommendations.append(f"Вернитесь к темам: {titles_preview}.")
    if total >= 0.8:
        recommendations.append("Уверенный результат — можно переходить к следующей цели.")
    elif total >= 0.5:
        recommendations.append("База есть; доберите слабые темы и повторите экзамен.")
    else:
        recommendations.append("Пройдите план целиком, затем повторите экзамен.")

    return {
        "exam_id": str(exam.id),
        "mode": exam.mode,
        "status": exam.status,
        "total_score": round(total, 3),
        "answered": len(scored),
        "total_questions": len(questions),
        "by_topic": by_topic_list,
        "weak_topics": weak_topics,
        "recommendations": recommendations,
    }


def deadline_suggestion(
    goal_deadline: date | None, answered: int, total: int
) -> str | None:
    """FR-11.4: suggest sitting the exam 2-3 days before the deadline."""
    if not goal_deadline or total == 0:
        return None
    if answered < total:
        return "Пройдите все темы плана, затем сдайте экзамен."
    days = (goal_deadline - date.today()).days
    if 2 <= days <= 7:
        return "Подходящее время для экзамена: до срока 2–7 дней."
    return None