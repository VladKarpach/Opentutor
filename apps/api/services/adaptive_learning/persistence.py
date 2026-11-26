"""Persistence bridge between the DB models and the deterministic algorithms.

The algorithms in this package are pure and DB-agnostic; this module is the only
place that knows about SQLAlchemy models. It converts stored topics and states
into ``TopicInput`` values, runs the planner, and writes the result back.

Keeping the split sharp is what makes the scheduling rules testable without a
database, as TZ section 6.4 requires.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.content import CourseContentTree
from models.learning_goal import (
    LearningGoal,
    LearningPlanItem,
    LearningTopic,
    TopicDependency,
    TopicState,
    TopicStrengthPoint,
)
from models.progress import LearningProgress
from services.adaptive_learning import scheduling as sched_mod
from services.adaptive_learning.repetition import TopicReviewState, apply_answer
from services.adaptive_learning import plan_diff as plan_diff_mod
from services.adaptive_learning.drills import required_for
from services.adaptive_learning.scheduling import PlanResult, TopicInput, build_plan

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Topic generation (TZ 6.1 step 1)
# ──────────────────────────────────────────────────────────────

async def generate_topics_for_goal(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    depth: str = "confident",
    language: str = "ru",
    model: str | None = None,
) -> list[LearningTopic]:
    """Split a goal into subtopics via the model and store them with edges.

    TZ section 6.1 step 1: "the model receives the topic and forms a list of
    subtopics". Without this a freshly created goal has no subtopics, and both
    the diagnostic (FR-4) and the planner (FR-6) refuse to run.
    """
    from models.learning_goal import LearningGoal
    from services.adaptive_learning import context as learner_context
    from services.adaptive_learning.content import decompose_topic

    goal = (await db.execute(
        select(LearningGoal).where(LearningGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        raise ValueError("Goal not found")

    # TZ section 9: hand the model what this learner already covered elsewhere,
    # so a repeat subject is not planned from scratch.
    ctx = await learner_context.build(
        db, user_id, goal_id=goal_id, course_id=getattr(goal, "course_id", None)
    )

    plans = await decompose_topic(
        goal.topic,
        depth=depth,
        language=language,
        model=model,
        prior_context=ctx.as_prompt_text(),
    )
    if not plans:
        raise ValueError("The model returned no subtopics")

    # Replace any previous decomposition so replanning is not duplicated.
    for existing in (await db.execute(
        select(LearningTopic).where(LearningTopic.goal_id == goal_id)
    )).scalars().all():
        await db.delete(existing)
    await db.flush()

    start_index = int(
        await db.scalar(
            select(func.coalesce(func.max(LearningTopic.order_index), -1)).where(
                LearningTopic.goal_id == goal_id
            )
        )
    ) + 1

    created: list[LearningTopic] = []
    for offset, plan in enumerate(plans):
        topic = LearningTopic(
            goal_id=goal_id,
            title=plan.title[:300],
            description=plan.description,
            estimated_minutes=plan.estimated_minutes,
            difficulty=plan.difficulty,
            order_index=start_index + offset,
        )
        db.add(topic)
        created.append(topic)
    await db.flush()

    # FR-5.1: resolve the prerequisite titles the model referenced. Only edges
    # pointing at an earlier subtopic are kept, which is what makes the graph a
    # DAG (FR-5.2) by construction.
    by_title: dict[str, LearningTopic] = {}
    for topic in created:
        by_title.setdefault(topic.title.strip().lower(), topic)

    edges = 0
    for plan, topic in zip(plans, created):
        for prereq_title in plan.prerequisites:
            key = prereq_title.strip().lower()
            prereq = by_title.get(key)
            if prereq is None or prereq.id == topic.id:
                continue
            if prereq.order_index >= topic.order_index:
                continue  # would create a cycle
            db.add(TopicDependency(topic_id=topic.id, prerequisite_id=prereq.id))
            edges += 1

    await db.commit()
    logger.info(
        "Generated %s subtopic(s) and %s dependency edge(s) for goal %s",
        len(created), edges, goal_id,
    )
    return created


# ──────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────

async def _card_observations_by_topic(
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None,
    topics: list[LearningTopic],
) -> dict[uuid.UUID, float]:
    """Mean normalised FSRS difficulty of the cards under each topic's source.

    A topic is often pinned to a chapter while its cards sit on the sections
    below it, so descendants count. Only reviewed cards are used: an untouched
    ``LearningProgress`` row carries the default ``fsrs_difficulty = 5.0`` and
    ``fsrs_reps = 0``, which would otherwise read as a real "medium" sample.
    """
    pinned = {t.id: t.source_node_id for t in topics if t.source_node_id is not None}
    if not pinned or course_id is None:
        return {}

    node_rows = await db.execute(
        select(CourseContentTree.id, CourseContentTree.parent_id).where(
            CourseContentTree.course_id == course_id
        )
    )
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for node_id, parent_id in node_rows.all():
        if parent_id is not None:
            children.setdefault(parent_id, []).append(node_id)

    # node id -> topics whose pinned node contains it
    owners: dict[uuid.UUID, list[uuid.UUID]] = {}
    for topic_id, root in pinned.items():
        stack = [root]
        seen: set[uuid.UUID] = set()
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            owners.setdefault(node_id, []).append(topic_id)
            stack.extend(children.get(node_id, ()))

    if not owners:
        return {}

    # Scoped by course rather than by a node id list: a large upload can have
    # more nodes than SQLite's bind-parameter limit, and the course is already
    # the user's own data.
    progress_rows = await db.execute(
        select(
            LearningProgress.content_node_id,
            LearningProgress.fsrs_difficulty,
        ).where(
            LearningProgress.user_id == user_id,
            LearningProgress.course_id == course_id,
            LearningProgress.fsrs_reps > 0,
        )
    )
    samples: dict[uuid.UUID, list[float]] = {}
    for node_id, raw in progress_rows.all():
        normalised = sched_mod.normalise_raw_fsrs(raw)
        if normalised is None:
            continue
        for topic_id in owners.get(node_id, ()):
            samples.setdefault(topic_id, []).append(normalised)

    return {
        topic_id: sum(values) / len(values)
        for topic_id, values in samples.items()
    }


async def load_topic_inputs(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    today: date | None = None,
) -> list[TopicInput]:
    """Build planner inputs for a goal.

    FR-5.6: a topic the learner marked as skipped is dropped entirely; one
    marked as known is kept with zero estimated time so the planner skips it.
    """
    today = today or datetime.now(timezone.utc).date()

    result = await db.execute(
        select(LearningTopic)
        .where(LearningTopic.goal_id == goal_id)
        .order_by(LearningTopic.order_index, LearningTopic.created_at)
    )
    topics = list(result.scalars().all())
    if not topics:
        return []

    goal_row = await db.execute(
        select(LearningGoal.topic, LearningGoal.course_id).where(
            LearningGoal.id == goal_id
        )
    )
    goal_data = goal_row.one_or_none()
    goal_topic = (goal_data.topic if goal_data else "") or ""
    goal_course_id = goal_data.course_id if goal_data else None

    topic_ids = [t.id for t in topics]

    dep_result = await db.execute(
        select(TopicDependency).where(TopicDependency.topic_id.in_(topic_ids))
    )
    prereqs: dict[uuid.UUID, list[str]] = {}
    for dep in dep_result.scalars().all():
        prereqs.setdefault(dep.topic_id, []).append(str(dep.prerequisite_id))

    state_result = await db.execute(
        select(TopicState).where(
            TopicState.goal_id == goal_id,
            TopicState.user_id == user_id,
            TopicState.topic_id.in_(topic_ids),
        )
    )
    states = {s.topic_id: s for s in state_result.scalars().all()}

    # FR-10.1: the plan must follow the person, not the topic list.
    #
    # Two levels, because neither alone is enough. The goal topic is the learner's
    # own phrasing and repeats when they start a goal on the same subject, which
    # is the case that actually happens. Comparing generated subtopic titles across
    # goals mostly finds nothing, because the model names them afresh each time, so
    # that is only used to refine the goal-level answer.
    from services.adaptive_learning import context as ctx_mod

    goal_prior, prior_goal_label = await ctx_mod.prior_goal_strength(
        db, user_id, goal_topic, exclude_goal_id=goal_id
    )
    prior = await ctx_mod.prior_strength_for_titles(
        db, user_id, [t.title for t in topics], exclude_goal_id=goal_id
    )
    if goal_prior or prior:
        logger.info(
            "Goal %s: prior knowledge %.2f from %r, %s matching subtopic(s)",
            goal_id, goal_prior, prior_goal_label[:60], len(prior),
        )

    card_observations = await _card_observations_by_topic(
        db, user_id, goal_course_id, topics
    )

    inputs: list[TopicInput] = []
    for topic in topics:
        if topic.manual_status == "skip":
            logger.debug("Skipping topic %s: marked skipped by the learner", topic.id)
            continue

        stored = states.get(topic.id)
        state = _to_review_state(stored) if stored else None

        # Goal-level knowledge applies to every topic; a per-subtopic match can
        # only add to it, never lower it.
        prior_strength = max(goal_prior, prior.get(topic.title, 0.0))

        minutes = topic.estimated_minutes
        if topic.manual_status == "known":
            # FR-6.2: explicitly known topics are skipped, not studied.
            minutes = 0
            if state is None:
                state = TopicReviewState(
                    strength=1.0,
                    status="known",
                    repetitions=1,
                    interval_days=30,
                )
        elif prior_strength > 0.0:
            # Never graded here, but covered elsewhere: a refresher, not a first
            # pass. Only the time is reduced.
            #
            # The state is deliberately left alone. Seeding a strong TopicState
            # looks tempting, but scheduling._estimate_minutes returns 0 for
            # strength >= 0.8, so the planner would drop the topic entirely and
            # the whole goal would come back empty. A short session the learner
            # can actually do is the honest outcome.
            factor = ctx_mod.time_factor_for_prior(prior_strength)
            minutes = max(5, int(round(topic.estimated_minutes * factor)))

        # How the learner is actually doing relative to the initial estimate.
        # Graded answers are folded into the stored value by
        # ``apply_scored_answer``; card reviews are read live from the material
        # pinned to the topic. Both are relative to the same prior, so they can
        # simply be averaged.
        relative_difficulty = state.fsrs_difficulty if state else None
        card_relative = sched_mod.relative_difficulty(
            topic.difficulty, card_observations.get(topic.id)
        )
        if card_relative is not None:
            relative_difficulty = (
                card_relative
                if relative_difficulty is None
                else (relative_difficulty + card_relative) / 2
            )

        inputs.append(
            TopicInput(
                id=str(topic.id),
                title=topic.title,
                estimated_minutes=minutes,
                prerequisites=prereqs.get(topic.id, []),
                state=state,
                prior_strength=prior_strength,
                available_from=today,
                difficulty=topic.difficulty,
                fsrs_difficulty=relative_difficulty,
            )
        )

    return inputs


def _to_review_state(stored: TopicState) -> TopicReviewState:
    """Map the DB row onto the dataclass the algorithms expect."""
    return TopicReviewState(
        strength=stored.strength,
        uncertainty=stored.uncertainty,
        interval_days=stored.interval_days,
        repetitions=stored.repetitions,
        lapses=stored.lapses,
        status=stored.status,
        last_review_at=stored.last_review_at,
        next_review_at=stored.next_review_at,
        stability_days=stored.stability_days,
        confidence_label=stored.confidence_label or "",
        fsrs_difficulty=stored.fsrs_difficulty,
    )


async def ensure_topic_state(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    topic_id: uuid.UUID,
) -> TopicState:
    """Fetch or create the TopicState row for one topic."""
    result = await db.execute(
        select(TopicState).where(
            TopicState.goal_id == goal_id,
            TopicState.user_id == user_id,
            TopicState.topic_id == topic_id,
        )
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = TopicState(goal_id=goal_id, user_id=user_id, topic_id=topic_id)
        db.add(state)
        await db.flush()
    return state


# ──────────────────────────────────────────────────────────────
# Planning
# ──────────────────────────────────────────────────────────────

async def compute_and_store_plan(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    days: int,
    minutes_per_day: int,
    speed: float | None = None,
    start_day: date | None = None,
    replace: bool = True,
    trigger: str = "replan",
    context: str = "",
    mode: str | None = None,
    reserve_ratio: float | None = None,
) -> PlanResult:
    """Run the planner and persist the result as plan items.

    FR-10.4: previously rejected items are preserved so a rejected change is not
    silently re-applied on the next replan.
    """
    today = datetime.now(timezone.utc).date()
    start_day = start_day or today

    inputs = await load_topic_inputs(db, goal_id, user_id, today=today)
    # Контрольные задания: по одной серии на каждую тему (по умолчанию 3 на тему
    # при confident/deep, 2 — при overview). Значение берётся из глубины цели,
    # чтобы при пересборке плана控制ные задания появлялись в расписании цели
    # и в «Моих задачах» одинаково.
    goal_row = await db.execute(
        select(LearningGoal.depth).where(LearningGoal.id == goal_id)
    )
    goal_depth = goal_row.scalar() or "confident"
    control_counts = {str(t.id): required_for(goal_depth) for t in inputs}

    result = build_plan(
        inputs,
        start_day=start_day,
        days=days,
        minutes_per_day=minutes_per_day,
        speed=speed,
        today=today,
        mode=mode,
        reserve_ratio=reserve_ratio,
        control_counts=control_counts,
    )

    before_plan: list = []
    if replace:
        existing = list((await db.execute(
            select(LearningPlanItem).where(LearningPlanItem.goal_id == goal_id)
        )).scalars().all())
        # FR-10.4: snapshot before deleting, otherwise there is nothing left to
        # diff against or to show the learner.
        before_plan = await plan_diff_mod.snapshot(
            db, goal_id, include_rejected=True
        )
        for row in existing:
            if row.rejected:
                # FR-10.4: a rejected change must not be silently re-applied.
                logger.debug("Keeping rejected plan item %s", row.id)
                continue
            await db.delete(row)
        await db.flush()

    known_ids = {t.id for t in inputs}

    # FR-10.4: a declined change must not reappear as a duplicate row. The
    # rejected rows above survive on purpose, so the topic has to be skipped when
    # the fresh plan would add a second item for it.
    declined: set[str] = set()
    if replace:
        declined = {
            str(row.topic_id)
            for row in existing
            if row.rejected and row.topic_id
        }

    for item in result.items:
        # Practice and exam days are not tied to a topic, so they have no id to
        # validate against the topic list. They are always written.
        if item.topic_id is not None and item.topic_id not in known_ids:
            continue
        if item.topic_id is not None and item.topic_id in declined:
            logger.debug(
                "Skipping %s for goal %s: change previously declined",
                item.topic_id, goal_id,
            )
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

    await db.flush()

    # Record what actually changed, so the learner can be shown the difference and
    # decline it. Done after the flush so the new rows are readable.
    if replace:
        # Nothing to compare against on the very first build: that is the initial
        # plan, not a change, and asking the learner to accept it would be noise.
        if before_plan:
            after = await plan_diff_mod.snapshot(db, goal_id)
            await plan_diff_mod.record(
                db,
                goal_id,
                user_id,
                before=before_plan,
                after=after,
                trigger=trigger,
                context=context,
            )

    await db.commit()
    logger.info(
        "Stored plan for goal %s: %s item(s) over %s day(s), feasible=%s",
        goal_id, len(result.items), len(result.days), result.feasible,
    )
    return result


# ──────────────────────────────────────────────────────────────
# Answer -> state
# ──────────────────────────────────────────────────────────────

async def apply_scored_answer(
    db: AsyncSession,
    *,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    topic_id: uuid.UUID,
    final_score: float,
    confidence: str | None = None,
    now: datetime | None = None,
) -> TopicReviewState:
    """FR-9.2: fold a graded answer into the stored topic state.

    Returns the updated dataclass so callers can see the new interval.
    """
    now = now or datetime.now(timezone.utc)
    stored = await ensure_topic_state(db, goal_id, user_id, topic_id)
    state = _to_review_state(stored)

    decision = apply_answer(state, final_score, now=now, confidence=confidence or "")

    stored.strength = decision.state.strength
    stored.uncertainty = decision.state.uncertainty
    stored.interval_days = decision.state.interval_days
    stored.repetitions = decision.state.repetitions
    stored.lapses = decision.state.lapses
    stored.stability_days = decision.state.stability_days
    stored.status = decision.state.status
    stored.last_review_at = decision.state.last_review_at
    stored.next_review_at = decision.state.next_review_at
    stored.confidence_label = confidence

    # Difficulty feedback: move the topic's relative difficulty toward what
    # this answer says about it. A guess is not evidence either way, so it is
    # left out rather than counted as a success.
    if (confidence or "") != "guessing":
        difficulty_row = await db.execute(
            select(LearningTopic.difficulty).where(LearningTopic.id == topic_id)
        )
        topic_difficulty = difficulty_row.scalar_one_or_none()
        stored.fsrs_difficulty = sched_mod.update_relative_difficulty(
            stored.fsrs_difficulty,
            topic_difficulty,
            sched_mod.observed_difficulty_from_score(final_score),
        )

    # FR-12.1: record the point on the strength chart in the same transaction
    # as the state update, so the graph and the topic map can never disagree.
    db.add(
        TopicStrengthPoint(
            goal_id=goal_id,
            topic_id=topic_id,
            user_id=user_id,
            strength=decision.state.strength,
            interval_days=decision.state.interval_days,
            was_lapse=bool(decision.state.lapses > state.lapses),
            recorded_at=now,
        )
    )

    await db.flush()
    logger.info("Topic %s after answer: %s", topic_id, decision.reason)
    return decision.state


async def compute_speed(
    db: AsyncSession,
    goal_id: uuid.UUID,
    *,
    limit: int = 10,
) -> float:
    """FR-10.3 / section 6.5: speed from actual vs planned minutes.

    Uses completed plan items; falls back to 1.0 while there is no history.
    """
    from services.adaptive_learning.scheduling import speed_coefficient

    result = await db.execute(
        select(LearningPlanItem)
        .where(
            LearningPlanItem.goal_id == goal_id,
            LearningPlanItem.status == "done",
            LearningPlanItem.actual_minutes.is_not(None),
        )
        .order_by(LearningPlanItem.day.desc())
        .limit(limit)
    )
    rows = [r for r in result.scalars().all() if r.planned_minutes > 0]
    if not rows:
        return 1.0

    actual = [float(r.actual_minutes or 0) for r in rows]
    planned = [float(r.planned_minutes) for r in rows]
    speed = speed_coefficient(actual, planned)
    logger.info("Speed coefficient for goal %s: %.2f", goal_id, speed)
    return speed


async def load_states(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, TopicReviewState]:
    """All stored states for a goal, keyed by topic id string."""
    result = await db.execute(
        select(TopicState).where(
            TopicState.goal_id == goal_id,
            TopicState.user_id == user_id,
        )
    )
    return {str(row.topic_id): _to_review_state(row) for row in result.scalars().all()}