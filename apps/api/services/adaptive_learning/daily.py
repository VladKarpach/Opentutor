"""The guided daily session — "open it and start".

The adaptive planner already knows which topics are scheduled and when. What it
did not do was turn that schedule into something a learner can start without
first reading the plan, choosing a topic and deciding how much time to spend on
what. This module builds that missing layer:

    review (5-10 min) -> theory -> practice

It is deliberately deterministic. The model writes explanations and tasks, but
the *shape* of the day is arithmetic over the plan and the stored topic states,
so the session still exists when the LLM is unavailable.

Two behaviours matter for the "daily mode" promise:

- an unfinished past day is carried forward. Pending items are shifted by the
  size of the gap, so the learner is not silently given a new topic while the
  old one is still open, and the plan dates move with them;
- a weak topic (recent low score, low strength, or a due review) is put first
  and gets the longer variant of the warm-up.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.learning_goal import (
    LearningAnswer,
    LearningGoal,
    LearningPlanItem,
    LearningQuestion,
    LearningTopic,
)
from config import settings
from services.adaptive_learning.persistence import (
    compute_and_store_plan,
    compute_speed,
    load_states,
)
from services.adaptive_learning.repetition import TopicReviewState
from services.adaptive_learning.scheduling import (
    EXAM_DAY_TITLE,
    PRACTICE_DAY_TITLE,
    adaptive_difficulty_factor,
    difficulty_multiplier,
)
from services.adaptive_learning.topic_flow import saved_explanation

logger = logging.getLogger(__name__)

# The warm-up is deliberately short: it is a door into the session, not a
# second lesson. A weak topic gets the upper end of the range.
REVIEW_MINUTES = 5
REVIEW_MINUTES_WEAK = 10

# A lesson is not just "half theory, half tasks". Reading and re-reading an
# explanation, then actually working through the tasks, are different activities.
# Theory gets the larger share; the minimums stop a short plan item from turning
# either half into a token five minutes.
THEORY_SHARE = 0.6
MIN_THEORY_MINUTES = 10
MIN_PRACTICE_MINUTES = 8
MAX_PRACTICE_MINUTES = 30

# When a future day is taken early, its topics move to today and a shortened
# review stays behind. The marker in the reason is what later tells the daily
# builder that this is a 40% review, not a normal full lesson.
PULL_REVIEW_REASON_PREFIX = "досрочное повторение"
PULL_REVIEW_SHARE = 0.4

WEAK_SCORE = 0.7
WEAK_STRENGTH = 0.6
RECENT_LOW_DAYS = 3

PENDING = ("pending", "missed")
DONE = ("done", "skipped")


@dataclass
class DailyStep:
    """One step of the session, ready to be serialised.

    ``topic`` is optional because the exam-prep and exam days are not lessons
    about one subtopic. They still carry the first topic as a useful entry point
    for the practice picker, but their title comes from the plan item.
    """

    kind: str
    topic: LearningTopic | None
    minutes: int
    reason: str | None = None
    item: LearningPlanItem | None = None
    theory: str = ""
    strength: float | None = None
    status: str | None = None
    practice_ready: bool = False
    title: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _end_of_day(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc)


def _topic_order(topics: list[LearningTopic]) -> dict[str, int]:
    return {str(topic.id): index for index, topic in enumerate(topics)}


def _pending(items: list[LearningPlanItem]) -> list[LearningPlanItem]:
    return [item for item in items if not item.rejected and item.status in PENDING]


async def _load_items(db: AsyncSession, goal_id: uuid.UUID) -> list[LearningPlanItem]:
    rows = await db.execute(
        select(LearningPlanItem).where(LearningPlanItem.goal_id == goal_id)
    )
    return list(rows.scalars().all())


def _shift_overdue(
    items: list[LearningPlanItem],
    topic_titles: dict[str, str],
    today: date,
) -> tuple[int, list[str]]:
    """Move pending work by the number of days the learner skipped.

    Only pending/missed items move. A completed item is history and must stay
    where it happened. All pending items from the oldest overdue day onward move
    by the same offset, which preserves the spacing of the plan instead of
    piling every missed day onto today.
    """
    overdue = [
        item
        for item in items
        if item.day < today and not item.rejected and item.status in PENDING
    ]
    if not overdue:
        return 0, []

    first_day = min(item.day for item in overdue)
    shift = (today - first_day).days
    if shift <= 0:
        return 0, []

    titles = sorted(
        {topic_titles.get(str(item.topic_id), "") for item in overdue if item.topic_id}
        - {""}
    )

    for item in items:
        if item.rejected or item.status not in PENDING:
            continue
        if item.day < first_day:
            continue
        item.day = item.day + timedelta(days=shift)
        if item.status == "missed":
            item.status = "pending"

    logger.info(
        "Daily session shifted %s plan item(s) by %s day(s)", len(overdue), shift
    )
    return shift, titles


async def _recent_low_scores(
    db: AsyncSession,
    user_id: uuid.UUID,
    topic_ids: list[uuid.UUID],
    today: date,
) -> dict[str, float]:
    """Latest score under the pass mark for each topic in the last few days.

    A single bad answer should change tomorrow's session, not wait for FSRS to
    decide the topic is due. The newest answer wins, so a later good answer
    clears the flag.
    """
    if not topic_ids:
        return {}

    since = datetime.combine(
        today - timedelta(days=RECENT_LOW_DAYS), time.min, tzinfo=timezone.utc
    )
    rows = await db.execute(
        select(LearningQuestion.topic_id, LearningAnswer.score)
        .join(LearningAnswer, LearningAnswer.question_id == LearningQuestion.id)
        .where(
            LearningAnswer.user_id == user_id,
            LearningQuestion.topic_id.in_(topic_ids),
            LearningAnswer.created_at >= since,
        )
        .order_by(LearningAnswer.created_at.desc())
    )

    latest: dict[str, float] = {}
    for topic_id, score in rows.all():
        key = str(topic_id)
        if key not in latest:
            latest[key] = float(score or 0.0)
    return {key: score for key, score in latest.items() if score < WEAK_SCORE}


def _choose_review(
    topics: list[LearningTopic],
    today_items: list[LearningPlanItem],
    states: dict[str, TopicReviewState],
    low_scores: dict[str, float],
    order: dict[str, int],
    scheduled_for: date,
) -> tuple[LearningTopic | None, LearningPlanItem | None, str]:
    """Pick the warm-up topic, preferring an actual review over a new lesson.

    Priority:
    1. a review item the planner put on today;
    2. a topic answered badly in the last few days;
    3. a topic whose review is due;
    4. a weak topic with practice history;
    5. the last studied topic, as a short recall warm-up.
    """
    review_items = [item for item in today_items if item.item_type == "review"]
    if review_items:
        item = min(review_items, key=lambda row: order.get(str(row.topic_id), 999))
        topic = next((t for t in topics if str(t.id) == str(item.topic_id)), None)
        if topic:
            return topic, item, item.reason or "подошёл срок повторения"

    low_topics = [t for t in topics if str(t.id) in low_scores]
    if low_topics:
        topic = min(
            low_topics,
            key=lambda t: (low_scores.get(str(t.id), 1.0), order.get(str(t.id), 999)),
        )
        return topic, None, "в прошлый раз были ошибки — закрепляем тему"

    due = [
        topic
        for topic in topics
        if (state := states.get(str(topic.id)))
        and state.next_review_at is not None
        and state.next_review_at <= _end_of_day(scheduled_for)
    ]
    if due:
        topic = min(
            due, key=lambda t: (states[str(t.id)].strength, order.get(str(t.id), 999))
        )
        return topic, None, "подошёл срок повторения"

    weak = [
        topic
        for topic in topics
        if (state := states.get(str(topic.id)))
        and (state.strength < WEAK_STRENGTH or state.status == "weak")
        and (state.last_review_at is not None or state.repetitions > 0)
    ]
    if weak:
        topic = min(
            weak, key=lambda t: (states[str(t.id)].strength, order.get(str(t.id), 999))
        )
        return topic, None, "тема просела — начинаем с короткого повторения"

    # A plain warm-up: the most recently reviewed topic with some history. It
    # costs five minutes and keeps yesterday's material from evaporating, even
    # when FSRS does not consider it due yet.
    studied = [
        topic
        for topic in topics
        if (state := states.get(str(topic.id)))
        and (state.last_review_at is not None or state.repetitions > 0)
    ]
    if studied:
        # Most recently reviewed first. ``last_review_at`` is the only datum
        # that says "this was yesterday"; strength alone would keep returning
        # the strongest old topic.
        topic = min(
            studied,
            key=lambda t: (
                0
                if states[str(t.id)].last_review_at is None
                else -states[str(t.id)].last_review_at.timestamp(),
                order.get(str(t.id), 999),
            ),
        )
        return topic, None, "короткое повторение перед новой темой"

    return None, None, ""


def _choose_studies(
    today_items: list[LearningPlanItem],
    order: dict[str, int],
) -> list[LearningPlanItem]:
    """The new topics scheduled for the day being run.

    The planner can fit more than one short topic into a day. Every study item
    gets its own theory/practice pair, otherwise the extra items would stay
    pending forever and the plan would grow a backlog one day at a time.

    A review-only day stays review-only: pulling a future study item forward
    would mix two different lessons and make the next day's plan nonsensical.
    """
    studies = [item for item in today_items if item.item_type == "study"]
    return sorted(studies, key=lambda row: order.get(str(row.topic_id), 999))


def _step(
    kind: str,
    topic: LearningTopic | None,
    minutes: int,
    *,
    reason: str | None = None,
    item: LearningPlanItem | None = None,
    theory: str = "",
    state: TopicReviewState | None = None,
    practice_ready: bool = False,
    title: str | None = None,
) -> DailyStep:
    return DailyStep(
        kind=kind,
        topic=topic,
        minutes=max(5, minutes),
        reason=reason,
        item=item,
        theory=theory,
        strength=round(state.strength, 3) if state else None,
        status=state.status if state else None,
        practice_ready=practice_ready,
        title=title,
    )


def _empty_response(
    goal: LearningGoal,
    *,
    today: date,
    shift: int,
    carried: list[str],
    completed: bool,
    message: str | None,
    next_session_at: date | None,
    remaining: int,
    empty_reason: str | None = None,
) -> dict:
    return {
        "goal_id": str(goal.id),
        "goal_title": goal.topic,
        "date": today.isoformat(),
        "scheduled_for": (next_session_at or today).isoformat(),
        "minutes_per_day": goal.minutes_per_day,
        "total_minutes": 0,
        "steps": [],
        "shifted_days": shift,
        "carried_topics": carried,
        "completed": completed,
        "empty_reason": empty_reason,
        "message": message,
        "next_session_at": next_session_at.isoformat() if next_session_at else None,
        "remaining_topics": remaining,
    }


def _serialise(step: DailyStep) -> dict:
    return {
        "kind": step.kind,
        "topic_id": str(step.topic.id) if step.topic else None,
        "title": step.title or (step.topic.title if step.topic else ""),
        "description": step.topic.description if step.topic else None,
        "minutes": step.minutes,
        "reason": step.reason,
        "item_id": str(step.item.id) if step.item else None,
        "has_explanation": bool(step.theory),
        "theory": step.theory or None,
        "strength": step.strength,
        "status": step.status,
        "practice_ready": step.practice_ready,
    }


async def pull_day(
    db: AsyncSession,
    goal: LearningGoal,
    user_id: uuid.UUID,
    *,
    target_day: date | None = None,
    today: date | None = None,
) -> dict:
    """Take a future day's work now and leave a shortened review behind.

    The rule this implements is deliberately asymmetric. The learner gets the
    full lesson today; the original day is not emptied and does not silently
    disappear from the plan. It keeps a review worth 40% of the moved time, so
    tomorrow still has a real (but shorter) session.

    Only the earliest eligible future day is offered by default. The caller may
    name a later day explicitly, but the same guards apply: today's work must be
    finished, exams are never pulled forward, and a leftover shortened review is
    never pulled again (otherwise the review would chase itself through the
    calendar).
    """
    today = today or _utcnow().date()
    items = await _load_items(db, goal.id)
    pending = _pending(items)

    if not pending:
        raise ValueError("В плане нет заданий, которые можно взять заранее.")

    if any(item.day <= today for item in pending):
        raise ValueError(
            "Сначала завершите сегодняшние задания — потом можно взять следующий день."
        )

    eligible = [
        item
        for item in pending
        if item.day > today
        and item.item_type not in ("exam", "practice")
        and not _is_pull_review(item)
    ]
    if not eligible:
        raise ValueError("В плане нет будущих заданий, которые можно взять заранее.")

    if target_day is not None:
        if target_day <= today:
            raise ValueError("Взять можно только будущий день.")
        target_items = [item for item in eligible if item.day == target_day]
        if not target_items:
            raise ValueError(
                "На этот день нет заданий, которые можно взять: возможно, "
                "он уже взят или содержит только короткое повторение."
            )
        target = target_day
    else:
        target = min(item.day for item in eligible)
        target_items = [item for item in eligible if item.day == target]

    if any(
        item.day == target and item.item_type == "exam" and not item.rejected
        for item in pending
    ):
        raise ValueError("Экзамен нельзя взять заранее — сначала пройдите практику.")

    total_minutes = sum(max(0, item.planned_minutes) for item in target_items)
    review_minutes = max(5, int(round(total_minutes * PULL_REVIEW_SHARE)))

    review_topic_id = next(
        (item.topic_id for item in target_items if item.topic_id is not None),
        None,
    )

    for item in target_items:
        previous = (item.reason or "").strip()
        item.day = today
        item.status = "pending" if item.status == "missed" else item.status
        item.reason = (
            f"взято заранее с {target.strftime('%d.%m')}"
            + (f" · {previous}" if previous else "")
        )

    db.add(
        LearningPlanItem(
            goal_id=goal.id,
            topic_id=review_topic_id,
            item_type="review",
            day=target,
            planned_minutes=review_minutes,
            status="pending",
            reason=(
                f"{PULL_REVIEW_REASON_PREFIX}: занятие "
                f"{target.strftime('%d.%m')} взято заранее (40% времени)"
            ),
            rejected=False,
        )
    )
    await db.commit()

    logger.info(
        "Pulled %s item(s) from %s to %s; left a %s-min review",
        len(target_items),
        target,
        today,
        review_minutes,
    )

    return {
        "goal_id": str(goal.id),
        "from_day": target.isoformat(),
        "moved_items": len(target_items),
        "review_minutes": review_minutes,
        "review_topic_id": str(review_topic_id) if review_topic_id else None,
        "remaining_items": 1,
        "message": (
            f"Занятие со {target.strftime('%d.%m')} перенесено на сегодня. "
            f"На {target.strftime('%d.%m')} останется повторение на "
            f"{review_minutes} мин."
        ),
    }


def _is_pull_review(item: LearningPlanItem) -> bool:
    """Whether this item is the shortened review left by an early pull."""
    return (item.reason or "").startswith(PULL_REVIEW_REASON_PREFIX)


async def prepare_session(
    db: AsyncSession,
    goal: LearningGoal,
    user_id: uuid.UUID,
    *,
    today: date | None = None,
) -> dict:
    """Build (and, when needed, move) today's guided session.

    The function is idempotent for a given day: the first call may shift the
    plan; later calls see no overdue pending items and return the same shape.
    """
    today = today or _utcnow().date()

    topics = list(
        (
            await db.execute(
                select(LearningTopic)
                .where(LearningTopic.goal_id == goal.id)
                .order_by(LearningTopic.order_index)
            )
        )
        .scalars()
        .all()
    )
    if not topics:
        return _empty_response(
            goal,
            today=today,
            shift=0,
            carried=[],
            completed=True,
            empty_reason="no_topics",
            message="Сначала разбейте цель на темы — тогда появится занятие на день.",
            next_session_at=None,
            remaining=0,
        )

    topic_by_id = {str(topic.id): topic for topic in topics}
    order = _topic_order(topics)

    items = await _load_items(db, goal.id)
    if not [item for item in items if not item.rejected]:
        days = max(1, (goal.deadline - today).days) if goal.deadline else 30
        await compute_and_store_plan(
            db,
            goal.id,
            user_id,
            days=days,
            minutes_per_day=goal.minutes_per_day,
            speed=await compute_speed(db, goal.id),
            start_day=today,
            trigger="replan",
            context="ежедневное занятие",
        )
        items = await _load_items(db, goal.id)

    shift, carried = _shift_overdue(
        items,
        {str(topic.id): topic.title for topic in topics},
        today,
    )
    if shift:
        await db.commit()

    pending = _pending(items)
    remaining = len({str(item.topic_id) for item in pending if item.topic_id})

    today_items = [item for item in pending if item.day == today]
    next_day = min((item.day for item in pending if item.day > today), default=None)

    # If today is already finished, do not silently start tomorrow's lesson.
    # The learner asked for "what to do today"; the honest answer is that today
    # is done and when the next session is.
    if not today_items and shift == 0:
        today_done = [
            item
            for item in items
            if item.day == today and item.status in DONE and not item.rejected
        ]
        if today_done and next_day:
            return _empty_response(
                goal,
                today=today,
                shift=shift,
                carried=carried,
                completed=True,
                message=(
                    f"На сегодня всё. Следующее занятие — {next_day.strftime('%d.%m')}."
                ),
                next_session_at=next_day,
                remaining=remaining,
            )

        if next_day:
            # A rest day in the plan. Showing the next scheduled lesson is more
            # useful than an empty page, so the learner can run it early.
            scheduled_for = next_day
            today_items = [item for item in pending if item.day == scheduled_for]
        else:
            return _empty_response(
                goal,
                today=today,
                shift=shift,
                carried=carried,
                completed=True,
                message=(
                    "План пройден. Можно переходить к экзамену "
                    "или повторить слабые темы."
                ),
                next_session_at=None,
                remaining=remaining,
            )

    scheduled_for = today_items[0].day if today_items else today
    states = await load_states(db, goal.id, user_id)
    low_scores = await _recent_low_scores(
        db, user_id, [topic.id for topic in topics], today
    )

    study_items = _choose_studies(today_items, order)
    study_pairs = [
        (item, topic_by_id[str(item.topic_id)])
        for item in study_items
        if str(item.topic_id) in topic_by_id
    ]
    special_items = [
        item for item in today_items if item.item_type in ("practice", "exam")
    ]
    # The practice and exam days are not lessons: adding a warm-up or a theory
    # block because some unrelated topic happens to be due would turn a
    # task-only day back into a normal lesson. If a study item is also scheduled
    # here, it is still processed below.
    practice_only = bool(special_items) and not study_pairs

    review_topic, review_item, review_reason = _choose_review(
        topics, today_items, states, low_scores, order, scheduled_for
    )
    if practice_only:
        review_topic, review_item, review_reason = None, None, ""

    # A shortened review left behind by "take tomorrow's lesson early" is not a
    # normal review-only day. It must stay short: the topic was already studied
    # and practised today, so repeating a full lesson tomorrow would defeat the
    # point of working ahead.
    is_pull_review = bool(
        review_item
        and (review_item.reason or "").startswith(PULL_REVIEW_REASON_PREFIX)
    )

    # A recent failure changes the shape of the day: repeat that topic instead
    # of stacking a new one on top of it. "More review" is not just a longer
    # warm-up; it means the lesson itself stays on the weak topic.
    focus_after_error = bool(review_topic and str(review_topic.id) in low_scores)
    if is_pull_review:
        lesson_pairs = []
    elif focus_after_error:
        lesson_pairs = [(review_item, review_topic)] if review_topic else []
    elif study_pairs:
        # Every topic the planner put on this day gets a theory/practice pair.
        # Otherwise a day with two short topics would leave one pending forever.
        lesson_pairs = study_pairs
    elif review_topic:
        # A review-only day still gets a theory step for the topic being reviewed.
        lesson_pairs = [(review_item, review_topic)]
    else:
        lesson_pairs = []

    if not review_topic and not lesson_pairs and not special_items:
        return _empty_response(
            goal,
            today=today,
            shift=shift,
            carried=carried,
            completed=True,
            message=(
                "На этот день по плану нет новых тем. "
                "Можно повторить слабую тему или перейти к экзамену."
            ),
            next_session_at=next_day,
            remaining=remaining,
        )

    explanations: dict[str, str] = {}

    async def theory_for(topic: LearningTopic) -> str:
        key = str(topic.id)
        if key not in explanations:
            explanations[key] = await saved_explanation(db, topic.id, user_id)
        return explanations[key]

    review_theory = await theory_for(review_topic) if review_topic else ""
    lesson_texts: dict[str, str] = {}
    for _, topic in lesson_pairs:
        lesson_texts[str(topic.id)] = await theory_for(topic)

    budget = max(5, goal.minutes_per_day)

    review_minutes = REVIEW_MINUTES
    if review_topic:
        if is_pull_review and review_item is not None:
            # The plan item already carries the reduced 40% allocation.
            review_minutes = max(5, review_item.planned_minutes)
        else:
            weak = (
                str(review_topic.id) in low_scores
                or (
                    states.get(str(review_topic.id))
                    and states[str(review_topic.id)].strength < WEAK_STRENGTH
                )
                or (
                    review_item is not None
                    and review_item.planned_minutes >= REVIEW_MINUTES_WEAK
                )
            )
            if weak:
                review_minutes = REVIEW_MINUTES_WEAK
            review_minutes = min(review_minutes, max(5, budget // 4))

    remaining_budget = max(5, budget - (review_minutes if review_topic else 0))

    def split_minutes(total: int) -> tuple[int, int]:
        """Split one lesson into theory and practice.

        Theory leads (60%) because the learner has to read and make notes before
        the tasks make sense. The minimums keep a short plan item from producing
        a five-minute theory block with an eight-minute practice block; instead
        the lesson grows to a realistic minimum.
        """
        total = max(MIN_THEORY_MINUTES + MIN_PRACTICE_MINUTES, total)
        practice = max(
            MIN_PRACTICE_MINUTES,
            min(MAX_PRACTICE_MINUTES, int(round(total * (1.0 - THEORY_SHARE)))),
        )
        theory = max(MIN_THEORY_MINUTES, total - practice)
        if theory + practice > total:
            # A very short lesson cannot fit both minimums; keep theory at its
            # floor and shrink practice rather than silently making theory tiny.
            practice = max(5, total - theory)
        return theory, practice

    if is_pull_review:
        lesson_budgets: list[int] = []
    elif focus_after_error or not study_pairs:
        lesson_budgets = [remaining_budget for _ in lesson_pairs]
    else:
        # Distribute the remaining budget across lessons weighted by topic
        # difficulty: hard topics get a larger slice, easy ones a smaller one.
        # The static estimate is combined with the learner's actual results,
        # so a topic that turned out harder than expected keeps gaining share.
        # Each topic is guaranteed at least MIN_TOPIC_MINUTES. Any rounding
        # remainder goes to the hardest topic.
        weights = []
        for _, topic in lesson_pairs:
            state = states.get(str(topic.id))
            weights.append(
                difficulty_multiplier(topic.difficulty)
                * adaptive_difficulty_factor(state.fsrs_difficulty if state else None)
            )
        weight_total = sum(weights)
        if weight_total > 0:
            raw_shares = [
                remaining_budget * w / weight_total for w in weights
            ]
        else:
            raw_shares = [remaining_budget / len(lesson_pairs)] * len(lesson_pairs)

        # Floor each share at the minimum topic minutes.
        floored = [
            max(settings.min_topic_minutes, int(round(share)))
            for share in raw_shares
        ]
        floored_total = sum(floored)

        if len(lesson_pairs) == 1:
            lesson_budgets = [remaining_budget]
        elif floored_total > remaining_budget:
            # Even the minimums don't fit: scale down, keeping the floor.
            # Give the hardest topic priority when trimming.
            order = sorted(
                range(len(lesson_pairs)),
                key=lambda i: weights[i],
                reverse=True,
            )
            lesson_budgets = list(floored)
            excess = floored_total - remaining_budget
            for i in order:
                if excess <= 0:
                    break
                can_reduce = max(0, lesson_budgets[i] - settings.min_topic_minutes)
                cut = min(can_reduce, excess)
                lesson_budgets[i] -= cut
                excess -= cut
        else:
            # Distribute any rounding remainder to the hardest topic.
            remainder = remaining_budget - floored_total
            if remainder > 0:
                hardest = max(
                    range(len(lesson_pairs)),
                    key=lambda i: weights[i],
                )
                floored[hardest] += remainder
            lesson_budgets = floored

    steps: list[DailyStep] = []
    if review_topic:
        steps.append(
            _step(
                "review",
                review_topic,
                review_minutes,
                reason=review_reason,
                item=review_item,
                theory=review_theory,
                state=states.get(str(review_topic.id)),
                practice_ready=bool(review_theory or review_topic.description),
            )
        )

    for (item, topic), item_minutes in zip(lesson_pairs, lesson_budgets):
        theory_minutes, practice_minutes = split_minutes(item_minutes)
        text = lesson_texts.get(str(topic.id), "")
        theory_reason = (
            "разбираем тему заново после ошибок"
            if focus_after_error
            else (
                item.reason if item and item.item_type == "study" else "повторение темы"
            )
        )
        steps.append(
            _step(
                "theory",
                topic,
                theory_minutes,
                reason=theory_reason,
                item=item,
                theory=text,
                state=states.get(str(topic.id)),
                practice_ready=bool(text or topic.description),
            )
        )
        steps.append(
            _step(
                "practice",
                topic,
                practice_minutes,
                reason=(
                    "закрепляем тему, в которой были ошибки"
                    if focus_after_error
                    else "закрепляем теорию на небольшой задаче"
                ),
                item=item,
                theory=text,
                state=states.get(str(topic.id)),
                practice_ready=bool(text or topic.description),
            )
        )

    # The last two plan days are task-only by design. The first topic is used as
    # the opening topic for the practice picker; the learner can switch to any
    # other topic from the panel.
    focus_topic = topics[0] if topics else None
    for special in special_items:
        if special.item_type == "practice":
            steps.append(
                _step(
                    "exam_prep",
                    focus_topic,
                    special.planned_minutes,
                    reason=special.reason
                    or "решаем задания по всем темам, чтобы открыть экзамен",
                    item=special,
                    title=PRACTICE_DAY_TITLE,
                )
            )
        elif special.item_type == "exam":
            steps.append(
                _step(
                    "exam",
                    focus_topic,
                    special.planned_minutes,
                    reason=special.reason or "итоговая проверка знаний по цели",
                    item=special,
                    title=EXAM_DAY_TITLE,
                )
            )

    message: str | None = None
    pulled_any = any(
        step.item and "взято заранее" in (step.item.reason or "")
        for step in steps
    )
    has_practice_day = any(item.item_type == "practice" for item in special_items)
    has_exam_day = any(item.item_type == "exam" for item in special_items)
    if has_practice_day and has_exam_day:
        message = (
            "Сегодня день практики и экзамена: сначала задания для допуска, "
            "затем итоговая проверка."
        )
    elif has_practice_day:
        message = (
            "Сегодня день практики: решаем задания по всем темам, чтобы открыть "
            "экзамен. Теории и новой темы в этот день нет."
        )
    elif has_exam_day:
        message = (
            "Сегодня день экзамена. Практика для допуска уже пройдена — "
            "можно начинать итоговую проверку."
        )
    elif is_pull_review:
        message = (
            "Короткое повторение после досрочного занятия: 40% обычного времени."
        )
    elif shift:
        message = (
            f"План сдвинут на {shift} дн.: сначала возвращаемся к пропущенной теме."
        )
    elif scheduled_for > today:
        message = (
            "План на сегодня выполнен. Это занятие со "
            f"{scheduled_for.strftime('%d.%m')} — можно начать сейчас."
        )
    elif pulled_any:
        message = (
            "Вы взяли занятие заранее. На его исходный день останется короткое "
            "повторение — 40% обычного времени."
        )

    logger.info(
        "Daily session for goal %s: %s step(s), %s min, shifted=%s",
        goal.id,
        len(steps),
        sum(step.minutes for step in steps),
        shift,
    )

    return {
        "goal_id": str(goal.id),
        "goal_title": goal.topic,
        "date": today.isoformat(),
        "scheduled_for": scheduled_for.isoformat(),
        "minutes_per_day": goal.minutes_per_day,
        "total_minutes": sum(step.minutes for step in steps),
        "steps": [_serialise(step) for step in steps],
        "shifted_days": shift,
        "carried_topics": carried,
        "completed": False,
        "empty_reason": None,
        "message": message,
        "next_session_at": next_day.isoformat() if next_day else None,
        "remaining_topics": remaining,
    }


async def prepare_agenda(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    today: date | None = None,
) -> dict:
    """Build today's guided session for *every* goal that has work today.

    The per-goal endpoint remains the focused workspace entry point. This one is
    the shape the global calendar promises: if today's column contains blocks
    from several goals, "Сегодня" contains a runnable session for each of them.

    Only goals that already have a stored plan participate. Preparing a goal
    without a plan would create a new schedule as a side effect of opening the
    page, which is not what "show me today's blocks" means. Plans that have
    fallen behind are carried forward exactly as they are in the single-goal
    flow; that is what makes the projected calendar days actionable.
    """
    today = today or _utcnow().date()

    goals = list(
        (
            await db.execute(
                select(LearningGoal)
                .where(
                    LearningGoal.user_id == user_id,
                    LearningGoal.status.in_(("active", "paused")),
                )
                .order_by(LearningGoal.created_at, LearningGoal.topic)
            )
        )
        .scalars()
        .all()
    )
    if not goals:
        return {"date": today.isoformat(), "sessions": [], "total_minutes": 0}

    planned_rows = await db.execute(
        select(LearningPlanItem.goal_id)
        .where(
            LearningPlanItem.goal_id.in_([goal.id for goal in goals]),
            LearningPlanItem.rejected.is_(False),
        )
        .distinct()
    )
    planned_ids = {row[0] for row in planned_rows.all()}
    if not planned_ids:
        return {"date": today.isoformat(), "sessions": [], "total_minutes": 0}

    sessions: list[dict] = []
    for goal in goals:
        if goal.id not in planned_ids:
            continue
        payload = await prepare_session(db, goal, user_id, today=today)
        # A rest day or a finished day may return tomorrow's lesson so the
        # learner can run it early. That is useful in the single-goal view, but
        # it is not a block in today's column of the global calendar.
        if payload["steps"] and payload["scheduled_for"] == today.isoformat():
            sessions.append(payload)

    total_minutes = sum(session["total_minutes"] for session in sessions)
    logger.info(
        "Daily agenda for user %s: %s goal(s), %s min",
        user_id,
        len(sessions),
        total_minutes,
    )
    return {
        "date": today.isoformat(),
        "sessions": sessions,
        "total_minutes": total_minutes,
    }
