"""Plan diffing and the reasons behind it — TZ FR-10.4.

The TZ asks the learner to see what changed and why ("добавлено 30 минут
повторения темы X"), and to be able to decline it. Both halves are done here in
code: the diff is arithmetic over two snapshots, and the wording is generated
from the trigger that caused the replan, not from the model.

Keeping the wording in code is deliberate. It must match the same numbers the
diff is reporting, and a model asked to explain a diff tends to soften or invent
one. Section 6.4's split is respected: the planner produces dates, this module
describes them.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.learning_goal import LearningPlanItem, LearningTopic, PlanRevision

logger = logging.getLogger(__name__)

# Change kinds, ordered so the UI can group them.
KIND_ADDED = "added"
KIND_REMOVED = "removed"
KIND_MOVED = "moved"
KIND_MINUTES = "minutes"

KIND_LABEL: dict[str, str] = {
    KIND_ADDED: "добавлено",
    KIND_REMOVED: "убрано",
    KIND_MOVED: "перенесено",
    KIND_MINUTES: "изменён объём",
}

ITEM_TYPE_LABEL: dict[str, str] = {
    "study": "изучение",
    "review": "повторение",
    "practice": "практика",
    "check": "проверка",
    "exam": "экзамен",
}

# Plan rows without a topic are the two dedicated end-of-plan days. They still
# need a human title in the change list and in the plan API.
SPECIAL_ITEM_TITLE: dict[str, str] = {
    "practice": "Практика для допуска к экзамену",
    "exam": "Экзамен",
}

# A change smaller than this is treated as noise rather than something to ask
# the learner about.
MIN_MINUTES_DELTA = 5
MIN_DAYS_SHIFT = 1


@dataclass
class SnapshotItem:
    """One plan row as it was before the change."""

    key: tuple[str, str]
    topic_id: str
    topic_title: str
    item_type: str
    day: date
    planned_minutes: int


@dataclass
class PlanDiff:
    changes: list[dict] = field(default_factory=list)
    total_minutes_added: int = 0
    total_minutes_removed: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.changes


def _key(topic_id: str | None, item_type: str) -> tuple[str, str]:
    return (str(topic_id or ""), item_type)


async def snapshot(
    db: AsyncSession, goal_id: uuid.UUID, *, include_rejected: bool = False
) -> list[SnapshotItem]:
    """Read the current plan into a comparable shape.

    Rejected rows are excluded by default: a change the learner already declined
    is not part of "what the plan is", so re-reporting it would loop.
    """
    query = select(LearningPlanItem).where(LearningPlanItem.goal_id == goal_id)
    if not include_rejected:
        query = query.where(LearningPlanItem.rejected.is_(False))

    rows = list((await db.execute(query)).scalars().all())
    if not rows:
        return []

    topic_ids = {row.topic_id for row in rows if row.topic_id}
    titles: dict[uuid.UUID, str] = {}
    if topic_ids:
        found = list(
            (
                await db.execute(
                    select(LearningTopic.id, LearningTopic.title).where(
                        LearningTopic.id.in_(topic_ids)
                    )
                )
            ).all()
        )
        titles = {row[0]: row[1] for row in found}

    return [
        SnapshotItem(
            key=_key(str(row.topic_id) if row.topic_id else "", row.item_type),
            topic_id=str(row.topic_id) if row.topic_id else "",
            topic_title=(
                titles.get(row.topic_id, "Тема")
                if row.topic_id
                else SPECIAL_ITEM_TITLE.get(row.item_type, "Работа")
            ),
            item_type=row.item_type,
            day=row.day,
            planned_minutes=row.planned_minutes,
        )
        for row in rows
    ]


def diff(before: list[SnapshotItem], after: list[SnapshotItem]) -> PlanDiff:
    """Compare two snapshots. Pure function, so it is directly testable."""
    out = PlanDiff()

    old_by_key = {item.key: item for item in before}
    new_by_key = {item.key: item for item in after}

    for key, new in new_by_key.items():
        old = old_by_key.get(key)
        if old is None:
            out.changes.append(
                {
                    "kind": KIND_ADDED,
                    "topic_id": new.topic_id,
                    "topic_title": new.topic_title,
                    "item_type": new.item_type,
                    "before": None,
                    "after": new.day.isoformat(),
                    "delta_minutes": new.planned_minutes,
                    "reason": "добавлено впервые",
                }
            )
            out.total_minutes_added += new.planned_minutes
            continue

        shifted = (old.day - new.day).days
        minutes_delta = new.planned_minutes - old.planned_minutes

        # A moved item that also grew is reported as moved first: the date is the
        # bigger fact, and a separate minutes entry for the same row would read as
        # two changes when it is one.
        if abs(shifted) >= MIN_DAYS_SHIFT:
            out.changes.append(
                {
                    "kind": KIND_MOVED,
                    "topic_id": new.topic_id,
                    "topic_title": new.topic_title,
                    "item_type": new.item_type,
                    "before": old.day.isoformat(),
                    "after": new.day.isoformat(),
                    "delta_minutes": minutes_delta,
                    "reason": "сдвинуто",
                }
            )
            if minutes_delta:
                out.total_minutes_added += max(minutes_delta, 0)
                out.total_minutes_removed += abs(min(minutes_delta, 0))
            continue

        if abs(minutes_delta) >= MIN_MINUTES_DELTA:
            out.changes.append(
                {
                    "kind": KIND_MINUTES,
                    "topic_id": new.topic_id,
                    "topic_title": new.topic_title,
                    "item_type": new.item_type,
                    "before": old.planned_minutes,
                    "after": new.planned_minutes,
                    "delta_minutes": minutes_delta,
                    "reason": "изменён объём",
                }
            )
            out.total_minutes_added += max(minutes_delta, 0)
            out.total_minutes_removed += abs(min(minutes_delta, 0))

    for key, old in old_by_key.items():
        if key in new_by_key:
            continue
        out.changes.append(
            {
                "kind": KIND_REMOVED,
                "topic_id": old.topic_id,
                "topic_title": old.topic_title,
                "item_type": old.item_type,
                "before": old.day.isoformat(),
                "after": None,
                "delta_minutes": -old.planned_minutes,
                "reason": "убрано из плана",
            }
        )
        out.total_minutes_removed += old.planned_minutes

    out.changes.sort(key=lambda c: -abs(c.get("delta_minutes") or 0))
    return out


def describe(change: dict) -> str:
    """One line per change, in the words the TZ uses as an example."""
    title = change.get("topic_title") or "тема"
    kind = change.get("kind")
    what = ITEM_TYPE_LABEL.get(change.get("item_type", ""), "работа")
    delta = int(change.get("delta_minutes") or 0)

    if kind == KIND_ADDED:
        return f"добавлено {delta} мин: {what} темы «{title}»"
    if kind == KIND_REMOVED:
        return f"убрано {abs(delta)} мин: {what} темы «{title}»"
    if kind == KIND_MOVED:
        return f"перенесено на {change['after']}: {what} темы «{title}»"
    if kind == KIND_MINUTES:
        word = "добавлено" if delta > 0 else "убрано"
        return f"{word} {abs(delta)} мин у {what} темы «{title}»"
    return f"изменение по теме «{title}»"


def summary_line(changes: list[dict], *, trigger: str, context: str = "") -> str:
    """The headline sentence shown above the list of changes."""
    if not changes:
        return "План не изменился"

    added = [c for c in changes if c["kind"] in (KIND_ADDED, KIND_MINUTES)
             and int(c.get("delta_minutes") or 0) > 0]
    removed = [c for c in changes if c["kind"] == KIND_REMOVED
               or int(c.get("delta_minutes") or 0) < 0]
    moved = [c for c in changes if c["kind"] == KIND_MOVED]

    bits: list[str] = []
    minutes_added = sum(int(c.get("delta_minutes") or 0) for c in added)
    minutes_removed = sum(abs(int(c.get("delta_minutes") or 0)) for c in removed)
    if minutes_added:
        bits.append(f"+{minutes_added} мин")
    if minutes_removed:
        bits.append(f"−{minutes_removed} мин")
    if moved:
        bits.append(f"перенесено пунктов: {len(moved)}")

    lead = {
        "answer": "после проверки знаний",
        "settings": "после изменения настроек",
        "goal_change": "после изменения цели",
        "catch_up": "после пропущенных дней",
        "manual": "после ручной правки",
    }.get(trigger, "после перепланирования")

    tail = f" ({context})" if context else ""
    return f"План обновлён {lead}{tail}: {', '.join(bits) if bits else 'без изменения объёма'}"


# FR-10.2 triggers, mapped to what the learner is told.
TRIGGER_HINTS = {
    "answer": "оценка изменила интервалы повторения",
    "settings": "изменился бюджет времени или срок",
    "goal_change": "изменилась тема или глубина цели",
    "catch_up": "накопился пропуск, повторения сжаты",
    "manual": "вы изменили план вручную",
}


def reason_for(trigger: str) -> str:
    return TRIGGER_HINTS.get(trigger, "пересчитано по новым данным")


async def record(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    before: list[SnapshotItem],
    after: list[SnapshotItem],
    *,
    trigger: str = "replan",
    context: str = "",
) -> PlanRevision | None:
    """Persist a revision when something actually changed; None when it did not.

    Returning None on an empty diff matters: otherwise every routine replan would
    leave a pending question the learner has to dismiss for no reason.
    """
    result = diff(before, after)
    if result.is_empty:
        return None

    revision = PlanRevision(
        goal_id=goal_id,
        user_id=user_id,
        trigger=trigger,
        summary=summary_line(result.changes, trigger=trigger, context=context),
        changes=[
            {**change, "text": describe(change), "reason": reason_for(trigger)}
            for change in result.changes
        ],
        status="pending",
    )
    db.add(revision)
    await db.commit()
    await db.refresh(revision)

    logger.info(
        "Plan revision %s for goal %s: %s change(s), %s",
        revision.id, goal_id, len(result.changes), revision.summary,
    )
    return revision


async def pending(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> list[PlanRevision]:
    return list(
        (
            await db.execute(
                select(PlanRevision)
                .where(
                    PlanRevision.goal_id == goal_id,
                    PlanRevision.user_id == user_id,
                    PlanRevision.status == "pending",
                )
                .order_by(PlanRevision.created_at.desc())
            )
        ).scalars().all()
    )


async def resolve(
    db: AsyncSession,
    revision: PlanRevision,
    *,
    accept: bool,
) -> PlanRevision:
    """Accept or decline a revision.

    Declining marks the affected plan rows as rejected: they stay in place and
    the next replan leaves them alone, which is what "отклонить" has to mean for
    a plan the learner agreed to before.
    """
    revision.status = "accepted" if accept else "rejected"
    revision.resolved_at = datetime.now(timezone.utc)

    if not accept:
        topic_ids = {
            uuid.UUID(str(change["topic_id"]))
            for change in (revision.changes or [])
            if change.get("topic_id")
        }
        if topic_ids:
            rows = list(
                (
                    await db.execute(
                        select(LearningPlanItem).where(
                            LearningPlanItem.goal_id == revision.goal_id,
                            LearningPlanItem.topic_id.in_(topic_ids),
                            LearningPlanItem.rejected.is_(False),
                        )
                    )
                ).scalars().all()
            )
            for row in rows:
                row.rejected = True
                row.reason = "отклонено вами: " + (row.reason or "")
            logger.info(
                "Rejected revision %s: kept %s plan row(s) unchanged",
                revision.id, len(rows),
            )

    await db.commit()
    await db.refresh(revision)
    return revision