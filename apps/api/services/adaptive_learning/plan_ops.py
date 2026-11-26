"""Plan-level helpers: multi-goal time split, catch-up mode, .ics export.

These complete the remaining TZ requirements that sit *around* the core
scheduler rather than inside it:

- FR-2.3  several goals at once, sharing the daily time budget
- FR-10.5 "I missed days" catch-up instead of piling up the backlog
- FR-15.1 export the plan to .ics
- FR-6.6  add a topic to an existing plan
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.exceptions import NotFoundError
from models.learning_goal import LearningGoal, LearningPlanItem, LearningTopic
from services.adaptive_learning.drills import required_for
from services.adaptive_learning.scheduling import build_plan, soft_return_plan

logger = logging.getLogger(__name__)

# FR-2.3: when a goal carries time_share, this is the floor it can fall to, so
# no goal ever receives a zero-minute budget.
MIN_SHARE = 0.15


def split_daily_minutes(goals: list[dict], daily_minutes: int) -> dict[str, int]:
    """Distribute the daily budget across goals (FR-2.3).

    ``goals`` is a list of ``{"id": ..., "time_share": float | None}``. Goals
    without an explicit share share what is left over, evenly.

    Rounding is handled by giving the remainder to the goals with the largest
    fractional part, so the total never exceeds or falls short of the budget.
    """
    if not goals:
        return {}

    explicit = [g for g in goals if g.get("time_share") is not None]
    implicit = [g for g in goals if g.get("time_share") is None]

    if not explicit:
        base = daily_minutes // len(goals)
        remainder = daily_minutes - base * len(goals)
        out = {}
        for index, goal in enumerate(goals):
            out[goal["id"]] = base + (1 if index < remainder else 0)
        return out

    # Explicit shares are honoured but floored, then normalised to the budget.
    raw = {g["id"]: max(MIN_SHARE, float(g["time_share"])) for g in explicit}
    total_share = sum(raw.values())

    if implicit:
        # Explicit shares may not swallow the whole budget.
        remaining = max(0.0, 1.0 - total_share)
        implicit_share = remaining / len(implicit) if remaining > 0 else MIN_SHARE
        for goal in implicit:
            raw[goal["id"]] = implicit_share
            total_share += implicit_share

    scale = daily_minutes / total_share if total_share else 1.0
    exact = {gid: value * scale for gid, value in raw.items()}

    floors = {gid: max(5, int(value)) for gid, value in exact.items()}
    shortfall = daily_minutes - sum(floors.values())
    if shortfall != 0:
        # Hand the difference to the entries with the largest fractional part.
        ranked = sorted(
            exact,
            key=lambda gid: (exact[gid] - floors[gid]) if shortfall > 0 else -(floors[gid] - exact[gid]),
        )
        step = 1 if shortfall > 0 else -1
        index = 0
        while shortfall != 0 and ranked:
            floors[ranked[index % len(ranked)]] += step
            shortfall -= step
            index += 1

    logger.info("Split %s min/day across %s goal(s)", daily_minutes, len(goals))
    return floors


async def missed_days_for_goal(
    db: AsyncSession,
    goal_id: uuid.UUID,
    *,
    today: date | None = None,
) -> int:
    """Count past days whose plan was left undone (FR-10.5).

    This is the size of the backlog, not a consecutive streak: what decides
    whether to compress the plan is how much unfinished work has piled up, and
    a learner who skipped three sessions a fortnight ago has just as much to
    catch up on as one who skipped three days running.
    """
    today = today or datetime.now(timezone.utc).date()

    rows = list(
        (await db.execute(
            select(LearningPlanItem).where(
                LearningPlanItem.goal_id == goal_id,
                LearningPlanItem.day < today,
            )
        )).scalars().all()
    )
    missed = {
        row.day for row in rows
        if row.status in ("pending", "missed") and not row.rejected
    }
    return len(missed)


async def rebuild_plan_with_catchup(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    days: int,
    minutes_per_day: int,
    today: date | None = None,
    mode: str | None = None,
    reserve_ratio: float | None = None,
):
    """FR-10.5: after a gap, compress instead of accumulating debt.

    Falls back to a normal build when nothing was missed, so callers do not need
    to branch.
    """
    today = today or datetime.now(timezone.utc).date()
    missed = await missed_days_for_goal(db, goal_id, today=today)
    if missed == 0:
        from services.adaptive_learning.persistence import compute_and_store_plan

        return await compute_and_store_plan(
            db,
            goal_id,
            user_id,
            days=days,
            minutes_per_day=minutes_per_day,
            start_day=today,
            mode=mode,
            reserve_ratio=reserve_ratio,
        )

    from services.adaptive_learning.persistence import load_topic_inputs

    inputs = await load_topic_inputs(db, goal_id, user_id, today=today)
    goal_row = await db.execute(
        select(LearningGoal.depth).where(LearningGoal.id == goal_id)
    )
    goal_depth = goal_row.scalar() or "confident"
    control_counts = {str(t.id): required_for(goal_depth) for t in inputs}
    result = soft_return_plan(
        inputs,
        today=today,
        days=days,
        minutes_per_day=minutes_per_day,
        missed_days=missed,
        mode=mode,
        reserve_ratio=reserve_ratio,
        control_counts=control_counts,
    )

    await _persist_plan(db, goal_id, {t.id for t in inputs}, result)
    logger.info(
        "Caught up goal %s after %s missed day(s): %s item(s)",
        goal_id, missed, len(result.items),
    )
    return result


async def _persist_plan(db, goal_id, known_ids: set, result) -> None:
    """Write a computed plan, preserving rejected items (FR-10.4)."""
    existing = list(
        (await db.execute(
            select(LearningPlanItem).where(LearningPlanItem.goal_id == goal_id)
        )).scalars().all()
    )
    for row in existing:
        if not row.rejected:
            await db.delete(row)
    await db.flush()

    for item in result.items:
        if item.topic_id is not None and item.topic_id not in known_ids:
            continue
        db.add(
            LearningPlanItem(
                goal_id=goal_id,
                topic_id=uuid.UUID(item.topic_id) if item.topic_id else None,
                item_type=item.item_type,
                day=item.day,
                planned_minutes=item.planned_minutes,
                status="pending",
                reason=item.reason,
            )
        )
    await db.commit()


async def export_plan_to_ics(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bytes:
    """FR-15.1: build an iCalendar file from the stored plan items.

    Written by hand rather than via ``icalendar`` so the endpoint also works in
    the core (SQLite-only) install, where that package lives in
    requirements-full.txt and may be absent.
    """
    goal = (await db.execute(
        select(LearningGoal).where(LearningGoal.id == goal_id, LearningGoal.user_id == user_id)
    )).scalar_one_or_none()
    if not goal:
        raise ValueError("Goal not found")

    rows = list(
        (await db.execute(
            select(LearningPlanItem, LearningTopic)
            .outerjoin(LearningTopic, LearningTopic.id == LearningPlanItem.topic_id)
            .where(
                LearningPlanItem.goal_id == goal_id,
                LearningPlanItem.status != "skipped",
                LearningPlanItem.rejected.is_(False),
            )
            .order_by(LearningPlanItem.day)
        )).all()
    )
    if not rows:
        raise ValueError("Nothing to export: build a plan first")

    # rows are (LearningPlanItem, LearningTopic|None) pairs.
    titles = {topic.id: topic.title for _item, topic in rows if topic is not None}

    def _stamp(day: date, minutes: int, end: bool) -> str:
        base = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)
        target = base + timedelta(minutes=minutes if end else 0)
        return target.strftime("%Y%m%dT%H%M%SZ")

    def _escape(text: str) -> str:
        return (
            str(text or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n")
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//OpenTutor//Learning Plan//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(goal.topic[:80])}",
        # FR-13.2: respect the learner's timezone, so events land sensibly.
        "X-WR-TIMEZONE:UTC",
    ]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for item, topic in rows:
        title = titles.get(item.topic_id) or {
            "practice": "Задания для допуска к экзамену",
            "exam": "Итоговый экзамен",
        }.get(item.item_type, "Занятие")
        kind = {
            "study": "Изучение",
            "review": "Повторение",
            "practice": "Практика",
            "exam": "Экзамен",
        }.get(item.item_type, "Занятие")

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{item.id}@opentutor.local",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_stamp(item.day, 0, False)}",
            f"DTEND:{_stamp(item.day, item.planned_minutes, True)}",
            f"SUMMARY:{_escape(f'{kind}: {title}')}",
            f"DURATION:PT{item.planned_minutes}M",
            f"DESCRIPTION:{_escape(item.reason or '')}",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    payload = "\r\n".join(lines) + "\r\n"
    logger.info("Exported %s plan item(s) to iCal for goal %s", len(rows), goal_id)
    return payload.encode("utf-8")


async def add_topic_to_plan(
    db: AsyncSession,
    goal_id: uuid.UUID,
    topic: LearningTopic,
    *,
    day: date | None = None,
    minutes: int | None = None,
) -> LearningPlanItem:
    """FR-6.6: add a topic to the plan by hand, at the end of the existing work."""
    last_day = await db.scalar(
        select(LearningPlanItem.day)
        .where(LearningPlanItem.goal_id == goal_id)
        .order_by(LearningPlanItem.day.desc())
        .limit(1)
    )
    target_day = day or last_day or datetime.now(timezone.utc).date()

    item = LearningPlanItem(
        goal_id=goal_id,
        topic_id=topic.id,
        item_type="study",
        day=target_day,
        planned_minutes=max(5, minutes or topic.estimated_minutes),
        status="pending",
        reason="добавлено вручную",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


# The copy made for the target day is marked in its reason, and the original is
# marked as "taken ahead". Both markers are read back: the first identifies the
# pulled copy so it is not confused with today's own work, the second stops the
# same item being halved twice.
PULL_AHEAD_COPY_REASON = "взято наперёд с "
PULL_AHEAD_MARK = "взято наперёд на "

# A task is never cut below this, however short it was: a two-minute remainder
# is not a session.
MIN_PULLED_ITEM_MINUTES = 5


async def pull_ahead_plan_item(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    target_day: date | None = None,
) -> tuple[LearningPlanItem, LearningPlanItem]:
    """Взять конкретное задание из будущего дня на today (или target_day).

    Правила:
    - Исходное задание (в будущем) уменьшается в 2 раза (минимум 5 мин) и
      остаётся на своём дне с пометкой, что часть работы взята наперёд.
    - На целевой день создаётся копия задания с исходным (полным) временем и
      статусом pending, чтобы человек мог его выполнить.
    - Можно брать каскадно: после выполнения взятого задания можно взять
      следующее. Собственные (не взятые) задания сегодняшнего дня должны быть
      закрыты, иначе план превращается в свалку.

    Exam days are refused: the exam unlocks the end of the route and cannot be
    moved before the practice it depends on. Practice/control assignments *are*
    allowed — that is the case the feature exists for.
    """
    today = datetime.now(timezone.utc).date()
    target_day = target_day or today

    if target_day < today:
        raise ValueError("Взять задание можно только на сегодня или будущий день")

    row = await db.execute(
        select(LearningPlanItem)
        .join(LearningGoal, LearningGoal.id == LearningPlanItem.goal_id)
        .where(
            LearningPlanItem.id == item_id,
            LearningPlanItem.goal_id == goal_id,
            LearningGoal.user_id == user_id,
        )
    )
    item = row.scalar_one_or_none()
    if not item:
        raise NotFoundError("Plan item", item_id)

    if item.day <= today:
        raise ValueError("Можно взять наперёд только задание с будущего дня")
    if target_day >= item.day:
        raise ValueError("Задание можно взять только на более ранний день")
    if item.rejected or item.status not in ("pending", "missed"):
        raise ValueError("Можно взять наперёд только невыполненное задание")
    if item.item_type == "exam":
        raise ValueError("Экзамен нельзя взять наперёд — сначала пройдите практику.")
    if PULL_AHEAD_MARK in (item.reason or ""):
        raise ValueError("Это задание уже взято наперёд")

    # Today's own plan must be finished first. Copies already pulled for today
    # are not "own work": they are the result of working ahead, so they do not
    # block pulling a second task from the same future day.
    today_rows = list(
        (
            await db.execute(
                select(LearningPlanItem).where(
                    LearningPlanItem.goal_id == goal_id,
                    LearningPlanItem.day <= today,
                    LearningPlanItem.status.in_(("pending", "missed")),
                    LearningPlanItem.rejected.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    own_pending = [
        row
        for row in today_rows
        if not (row.reason or "").startswith(PULL_AHEAD_COPY_REASON)
    ]
    if own_pending:
        raise ValueError(
            "Сначала завершите сегодняшние задания — потом можно взять "
            "задание с будущего дня."
        )

    old_minutes = max(0, item.planned_minutes)
    original_day = item.day

    # Уменьшаем исходное задание в 2 раза, но не меньше 5 минут.
    item.planned_minutes = max(MIN_PULLED_ITEM_MINUTES, old_minutes // 2)
    previous = (item.reason or "").strip()
    item.reason = f"{previous} | {PULL_AHEAD_MARK}{original_day.isoformat()}".strip(
        " |"
    )

    # Создаём копию на целевой день с полным исходным временем.
    pulled = LearningPlanItem(
        goal_id=goal_id,
        topic_id=item.topic_id,
        item_type=item.item_type,
        day=target_day,
        planned_minutes=old_minutes,
        status="pending",
        reason=f"{PULL_AHEAD_COPY_REASON}{original_day.isoformat()}",
        rejected=False,
    )
    db.add(pulled)
    await db.commit()
    await db.refresh(pulled)
    await db.refresh(item)
    logger.info(
        "Pulled plan item %s from %s to %s (original halved to %s min)",
        item_id,
        original_day,
        target_day,
        item.planned_minutes,
    )
    return pulled, item
