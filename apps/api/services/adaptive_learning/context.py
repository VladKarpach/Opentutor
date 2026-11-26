"""What the learner already knows — the context the model reasons from.

TZ section 9: "Контекст запросов: цель, сводка по темам, краткая история (не весь
диалог)". This module builds that context, except it reaches across *all* of the
learner's goals and their course, so the tenth goal is planned against what the
first nine already taught rather than from a blank page.

Everything here is derived from stored state by deterministic code. The model
never contributes to it; it only receives it. Same split as section 6.4.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.learning_goal import (
    ErrorSummary,
    LearningAnswer,
    LearningGoal,
    LearningQuestion,
    LearningTopic,
    TopicState,
)
from services.adaptive_learning.answer_scoring import ERROR_TYPES
from services.adaptive_learning.repetition import (
    HIGH_SCORE_THRESHOLD,
    MEDIUM_SCORE_THRESHOLD,
)

logger = logging.getLogger(__name__)

# A topic is "known" here at the same bar the engine calls high-scoring.
KNOWN_THRESHOLD = HIGH_SCORE_THRESHOLD
WEAK_THRESHOLD = MEDIUM_SCORE_THRESHOLD

# Keep the prompt small: section 9 asks for a brief history, not the whole thing.
MAX_KNOWN_TOPICS = 12
MAX_WEAK_TOPICS = 8
RECENT_ANSWERS = 20
HISTORY_DAYS = 180

# Titles closer than this are treated as the same subject area.
TITLE_SIMILARITY = 0.82

# Same-subject test, used only to carry prior knowledge between goals.
#
# The overlap ratio above is deliberately strict because it de-duplicates titles
# for a prompt, where a false merge is visible and annoying. For deciding that
# "Прямой ход метода Гаусса" is the same subject as "Обратный ход метода Гаусса"
# it is far too strict: one differing qualifier drops the ratio below the
# threshold, so almost nothing would ever match and the feature would quietly do
# nothing. So here the qualifying word is allowed to differ — what matters is that
# enough significant words are shared.
SAME_SUBJECT_MIN_SHARED = 3
SAME_SUBJECT_MIN_RATIO = 0.6


def _titles_overlap(a: str, b: str) -> float:
    """Token overlap between two topic titles, 0-1.

    Deliberately crude: Russian topic titles share function words, so only tokens
    longer than three characters count. This is a grouping hint for the prompt,
    not a semantic match.
    """
    def tokens(text: str) -> set[str]:
        return {t for t in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(t) > 3}

    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _significant_tokens(text: str) -> set[str]:
    """Words that carry subject matter.

    Three letters is the floor, not four: "ход" distinguishes "прямой ход" from
    "обратный ход" and is exactly the word that would otherwise be dropped,
    making a genuine subject match fail. The overlap ratio keeps stray short
    function words from causing matches on their own.
    """
    return {
        t
        for t in "".join(c if c.isalnum() else " " for c in text.lower()).split()
        if len(t) >= 3
    }


# Goal topics are free text written by the learner, not generated subtopics, so
# they are compared by containment: most of the shorter phrase appearing in the
# longer one. Goal-level matching is the reliable signal here — the model gives a
# fresh set of subtopic titles every time, so comparing subtopic titles across
# goals mostly finds nothing.
GOAL_TOPIC_MIN_SHARED = 2
GOAL_TOPIC_MIN_CONTAINMENT = 0.75


def same_goal_topic(a: str, b: str) -> bool:
    """True when two goal topics describe the same study effort.

    "Метод Гаусса: решение систем" and "Метод Гаусса" are the same subject; a
    learned topic like "Прямой ход метода Гаусса" is not the learner's phrasing
    and is deliberately not comparable this way.
    """
    ta, tb = _significant_tokens(a), _significant_tokens(b)
    if not ta or not tb:
        return False
    shared = len(ta & tb)
    if shared < GOAL_TOPIC_MIN_SHARED:
        return False
    return shared / min(len(ta), len(tb)) >= GOAL_TOPIC_MIN_CONTAINMENT


async def prior_goal_strength(
    db: AsyncSession,
    user_id: uuid.UUID,
    topic: str,
    *,
    exclude_goal_id: uuid.UUID | None = None,
) -> tuple[float, str]:
    """Strongest result the learner reached on a comparable earlier goal.

    Returns ``(strength, matched_topic)``. This is what carries "he already did
    this" into a new goal about the same subject, which is the case that actually
    happens: a person writes a new goal for the same course.
    """
    # `.all()`, not `.scalars()`: this selects three columns, and scalars() would
    # hand back only the id, so reading `.topic` would raise.
    query = select(
        LearningGoal.id, LearningGoal.topic, LearningGoal.status
    ).where(LearningGoal.user_id == user_id)
    if exclude_goal_id is not None:
        query = query.where(LearningGoal.id != exclude_goal_id)
    previous = list((await db.execute(query)).all())
    if not previous:
        return 0.0, ""

    candidates: list[uuid.UUID] = []
    for row in previous:
        # Rows are (id, topic, status) tuples.
        if same_goal_topic(topic, row[1]):
            candidates.append(row[0])
    if not candidates:
        return 0.0, ""

    rows = list(
        (
            await db.execute(
                select(TopicState.strength, TopicState.topic_id).where(
                    TopicState.user_id == user_id,
                    TopicState.goal_id.in_(candidates),
                )
            )
        ).all()
    )
    if not rows:
        return 0.0, ""

    strengths = [float(r[0] or 0.0) for r in rows]
    best = max(strengths)
    label = next((r[1] for r in previous if r[0] in candidates), "")
    logger.info(
        "Goal topic %r matches earlier goal %r: best strength %.2f over %s topic(s)",
        topic[:60], label[:60], best, len(strengths),
    )
    return best, label


def same_subject(a: str, b: str) -> bool:
    """True when two titles almost certainly mean the same studied subject.

    Deliberately more forgiving than :func:`_titles_overlap`: a qualifier may
    differ ("прямой" vs "обратной") while the subject is the same.

    The shared-word floor adapts to the shorter title, because a legitimate topic
    name can be two words long ("Метод Гаусса") and must still match itself. That
    does not let short unrelated titles through: with a two-word title every
    significant word has to be shared, so "Теория чисел" cannot match "Теория
    множеств".
    """
    ta, tb = _significant_tokens(a), _significant_tokens(b)
    if not ta or not tb:
        return False
    shared = len(ta & tb)
    floor = min(SAME_SUBJECT_MIN_SHARED, min(len(ta), len(tb)))
    if shared < floor:
        return False
    return shared / max(len(ta), len(tb)) >= SAME_SUBJECT_MIN_RATIO


@dataclass
class LearnerContext:
    """The slice of prior knowledge handed to the model."""

    goals_seen: int = 0
    topics_touched: int = 0
    answers_total: int = 0
    answered_recently: int = 0

    known: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)

    prior_goal_topics: list[str] = field(default_factory=list)
    error_profile: dict[str, int] = field(default_factory=dict)
    dominant_error: str = ""
    mean_score: float = 0.0

    course_title: str = ""
    course_goals: int = 0
    course_documents: int = 0

    def is_empty(self) -> bool:
        return self.answers_total == 0 and self.goals_seen == 0

    def as_prompt_text(self, limit: int = 700) -> str:
        """Render for a prompt, trimmed so it cannot dominate the request.

        Section 9 wants a brief history. This returns "" for a brand-new learner
        so callers can skip the block entirely instead of sending "ничего не знаю".
        """
        parts: list[str] = []

        if self.known:
            parts.append("Уже освоено (не начинать с нуля): " + "; ".join(self.known))
        if self.weak:
            parts.append("Слабые места (нужно повторить или разобрать): " + "; ".join(self.weak))
        if self.in_progress:
            parts.append("В работе: " + "; ".join(self.in_progress))
        if self.prior_goal_topics:
            parts.append(
                "Темы из прошлых целей, не начинать заново: " + "; ".join(self.prior_goal_topics)
            )
        if self.dominant_error and self.answered_recently:
            parts.append(
                f"Типичная ошибка в прошлых ответах: {self.dominant_error} "
                f"({self.mean_score:.0%} средний балл за {self.answered_recently} последних ответов)."
            )
        if self.course_title:
            parts.append(
                f"Материалы курса «{self.course_title}»: "
                f"{self.course_goals} цел., {self.course_documents} док."
            )

        text = "\n".join(parts)
        return text[:limit]


async def build(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    goal_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
) -> LearnerContext:
    """Assemble the learner's prior knowledge across all of their goals.

    ``goal_id`` and ``course_id`` narrow the *inputs* (the goal's own course is
    used when the caller does not say otherwise) but the result still spans every
    goal, because the point is what the person already knows.
    """
    ctx = LearnerContext()

    goals = list(
        (
            await db.execute(
                select(LearningGoal).where(LearningGoal.user_id == user_id)
            )
        ).scalars().all()
    )
    ctx.goals_seen = len(goals)
    goal_ids = [g.id for g in goals]

    if course_id is None and goal_id is not None:
        course_id = next((g.course_id for g in goals if g.id == goal_id), None)

    if course_id is not None:
        await _fill_course(ctx, db, user_id, course_id, goal_ids)

    if not goal_ids:
        return ctx

    topics = list(
        (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id.in_(goal_ids))
            )
        ).scalars().all()
    )
    ctx.topics_touched = len(topics)

    states = list(
        (
            await db.execute(
                select(TopicState).where(
                    TopicState.user_id == user_id,
                    TopicState.goal_id.in_(goal_ids),
                )
            )
        ).scalars().all()
    )

    title_by_id = {t.id: t.title for t in topics}
    # The current goal's own topics are not "prior" knowledge; they are the plan
    # being built right now, and echoing them back only invites the model to
    # repeat the same list.
    current_topic_ids: set[uuid.UUID] = set()
    if goal_id is not None:
        current_topic_ids = {t.id for t in topics if t.goal_id == goal_id}

    known: list[tuple[float, str]] = []
    weak: list[tuple[float, str]] = []
    in_progress: list[tuple[float, str]] = []

    for state in states:
        title = title_by_id.get(state.topic_id)
        if not title:
            continue
        # TopicState.strength is written by apply_scored_answer on every graded
        # answer, so it already is the current strength.
        strength = float(state.strength or 0.0)
        if state.topic_id in current_topic_ids:
            continue
        if strength >= KNOWN_THRESHOLD:
            known.append((strength, title))
        elif strength >= WEAK_THRESHOLD:
            in_progress.append((strength, title))
        else:
            weak.append((strength, title))

    known.sort(reverse=True)
    in_progress.sort(reverse=True)
    weak.sort()
    ctx.known = _dedupe(t for _, t in known)[:MAX_KNOWN_TOPICS]
    ctx.in_progress = _dedupe(t for _, t in in_progress)[:MAX_WEAK_TOPICS]
    ctx.weak = _dedupe(t for _, t in weak)[:MAX_WEAK_TOPICS]

    # Topics that existed in other goals but were never graded still count as
    # "already covered", otherwise a plan would repeat them.
    graded_ids = {s.topic_id for s in states}
    untouched = [
        title_by_id[t.id]
        for t in topics
        if t.id not in graded_ids and t.id not in current_topic_ids
    ]
    ctx.prior_goal_topics = _dedupe(untouched)[:MAX_KNOWN_TOPICS]

    await _fill_error_profile(ctx, db, user_id, goal_ids)
    return ctx


def _dedupe(titles: list[str]) -> list[str]:
    """Drop near-duplicate titles, keeping the first (strongest) occurrence."""
    out: list[str] = []
    for title in titles:
        if any(_titles_overlap(title, kept) >= TITLE_SIMILARITY for kept in out):
            continue
        out.append(title)
    return out


async def _fill_course(
    ctx: LearnerContext,
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    goal_ids: list[uuid.UUID],
) -> None:
    """Course name plus how much material and how many goals hang off it."""
    from models.course import Course

    row = await db.execute(select(Course.name).where(Course.id == course_id))
    ctx.course_title = row.scalar() or ""

    count = await db.execute(
        select(func.count(LearningGoal.id)).where(
            LearningGoal.user_id == user_id,
            LearningGoal.course_id == course_id,
        )
    )
    ctx.course_goals = int(count.scalar() or 0)

    # Ingested material in the course. Optional: the content tree only exists
    # once documents were uploaded, and context must never fail because of it.
    try:
        from models.content import CourseContentTree

        nodes = await db.execute(
            select(func.count(CourseContentTree.id)).where(
                CourseContentTree.course_id == course_id
            )
        )
        ctx.course_documents = int(nodes.scalar() or 0)
    except Exception:  # noqa: BLE001 - optional table, context must not fail
        ctx.course_documents = 0


async def _fill_error_profile(
    ctx: LearnerContext, db: AsyncSession, user_id: uuid.UUID, goal_ids: list[uuid.UUID]
) -> None:
    """Count the error taxonomy across every goal, plus the recent mean score."""
    rows = list(
        (
            await db.execute(
                select(LearningAnswer.error_types, LearningAnswer.score)
                .join(LearningQuestion, LearningQuestion.id == LearningAnswer.question_id)
                .where(
                    LearningQuestion.topic_id.in_(
                        select(LearningTopic.id).where(LearningTopic.goal_id.in_(goal_ids))
                    ),
                    LearningAnswer.user_id == user_id,
                )
                .order_by(LearningAnswer.created_at.desc())
                .limit(RECENT_ANSWERS)
            )
        ).all()
    )
    total = await db.execute(
        select(func.count(LearningAnswer.id))
        .join(LearningQuestion, LearningQuestion.id == LearningAnswer.question_id)
        .where(
            LearningQuestion.topic_id.in_(
                select(LearningTopic.id).where(LearningTopic.goal_id.in_(goal_ids))
            ),
            LearningAnswer.user_id == user_id,
        )
    )
    ctx.answers_total = int(total.scalar() or 0)

    if not rows:
        return

    ctx.answered_recently = len(rows)
    scores = [float(r[1]) for r in rows if r[1] is not None]
    ctx.mean_score = sum(scores) / len(scores) if scores else 0.0

    counts = {key: 0 for key in ERROR_TYPES}
    for error_types, _score in rows:
        for err in error_types or []:
            if err in counts:
                counts[err] += 1
    ctx.error_profile = counts
    top = max(counts.items(), key=lambda kv: kv[1])
    ctx.dominant_error = top[0] if top[1] else ""


# How much of a topic's time survives when the learner already covered it
# elsewhere. A refresher, not a first pass.
PRIOR_REFRESHER = 0.4
PRIOR_FAMILIAR = 0.7


async def prior_strength_for_titles(
    db: AsyncSession,
    user_id: uuid.UUID,
    titles: list[str],
    *,
    exclude_goal_id: uuid.UUID | None = None,
) -> dict[str, float]:
    """Best strength the learner reached on similar subjects in *other* goals.

    This is what makes the plan follow the person rather than the topic list: a
    tenth goal on a subject already mastered in the third one is planned as a
    refresher. Matching is by title overlap, so it is approximate by design, and
    the caller turns it into a time factor rather than a decision.
    """
    if not titles:
        return {}

    other_goals = select(LearningGoal.id).where(LearningGoal.user_id == user_id)
    if exclude_goal_id is not None:
        other_goals = other_goals.where(LearningGoal.id != exclude_goal_id)
    goal_ids = list((await db.execute(other_goals)).scalars().all())
    if not goal_ids:
        return {}

    rows = list(
        (
            await db.execute(
                select(LearningTopic.title, TopicState.strength)
                .join(TopicState, TopicState.topic_id == LearningTopic.id)
                .where(
                    LearningTopic.goal_id.in_(goal_ids),
                    TopicState.user_id == user_id,
                )
            )
        ).all()
    )
    if not rows:
        return {}

    prior: dict[str, float] = {}
    for title, strength in rows:
        value = float(strength or 0.0)
        for target in titles:
            if same_subject(target, title):
                if value > prior.get(target, 0.0):
                    prior[target] = value
    return prior


def time_factor_for_prior(prior_strength: float) -> float:
    """How much of the full estimate a previously-seen topic still needs.

    Difficulty is deliberately not part of this factor: the result scales
    ``estimated_minutes`` and ``scheduling._estimate_minutes`` then applies the
    static difficulty multiplier. Applying it in both places would charge a
    hard topic twice (1.45 × 1.45 ≈ 2.1× its base time).
    """
    if prior_strength >= KNOWN_THRESHOLD:
        return PRIOR_REFRESHER
    if prior_strength >= WEAK_THRESHOLD:
        return PRIOR_FAMILIAR
    return 1.0


def prior_reason(prior_strength: float) -> str:
    """Learner-facing note for a topic shortened because it is familiar."""
    if prior_strength >= KNOWN_THRESHOLD:
        return "уже освоено ранее — короткое повторение"
    if prior_strength >= WEAK_THRESHOLD:
        return "знакомо ранее — время сокращено"
    return ""


def describe_for_ui(ctx: LearnerContext) -> dict:
    return {
        "goals_seen": ctx.goals_seen,
        "topics_touched": ctx.topics_touched,
        "answers_total": ctx.answers_total,
        "answered_recently": ctx.answered_recently,
        "known": ctx.known,
        "weak": ctx.weak,
        "in_progress": ctx.in_progress,
        "prior_goal_topics": ctx.prior_goal_topics,
        "error_profile": ctx.error_profile,
        "dominant_error": ctx.dominant_error,
        "mean_score": round(ctx.mean_score, 3),
        "course_title": ctx.course_title,
        "course_goals": ctx.course_goals,
        "course_documents": ctx.course_documents,
        "summary": ctx.as_prompt_text(),
    }