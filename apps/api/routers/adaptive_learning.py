"""Adaptive learning endpoints — TZ section 10.

Route groups implemented, mapped from the TZ:

- ``/api/learning-goals``            TZ /goals           (U1, U3)
- ``/api/learning-goals/{id}/diagnostic``  TZ /goals/{id}/diagnostic  (U2)
- ``/api/learning-goals/{id}/plan``   TZ /goals/{id}/plan            (U3, U7)
- ``/api/learning-goals/{id}/progress`` TZ /goals/{id}/progress       (U9)
- ``/api/learning-goals/{id}/review``  TZ /goals/{id}/review          (U6)
- ``/api/learning-goals/{id}/exam``    TZ /goals/{id}/exam            (U8)
- ``/api/learning-topics/{id}``       TZ /topics/{id}                 (U4)
- ``/api/learning-questions/{id}``    TZ /questions/{id}/answer      (U5)
- ``/api/profile``                    TZ /auth profile                (FR-1.2)

Mounted under its own prefix rather than reusing ``routers/goals.py``, which is
the project's older course-scoped ``StudyGoal`` model. The TZ describes a
different entity (topic + deadline + time budget), so keeping them separate
avoids changing existing behaviour.

LLM-dependent endpoints return 503 with a clear message when no provider is
configured, instead of failing deep in the stack. The deterministic endpoints
(plan, progress, grading arithmetic) work with no LLM at all.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from libs.exceptions import ConflictError, NotFoundError, ValidationError
from models.learning_goal import (
    DiagnosticSessionRecord,
    Exam as ExamModel,
    LearningAnswer,
    LearningGoal,
    LearningPlanItem,
    LearningQuestion,
    LearningTopic,
    TopicDependency,
    TopicResource,
    TopicState,
    TopicDrill as TopicDrillModel,
    PlanRevision as PlanRevisionModel,
    UserProfile,
)
from models.generated_asset import GeneratedAsset
from models.ingestion import WrongAnswer
from models.user import User
from schemas.learning import (
    AddToPlanRequest,
    AnswerResponse,
    ComprehensionAnswerRequest,
    ComprehensionLatestResponse,
    ComprehensionStartRequest,
    ComprehensionStartResponse,
    ComprehensionTurnResponse,
    CorrectionRequest,
    CourseGoalsGroup,
    CourseGoalsResponse,
    DailyAgendaResponse,
    DailyCompleteRequest,
    DailyCompleteResponse,
    DailyPullRequest,
    DailyPullResponse,
    DailySessionResponse,
    DisputeRequest,
    DrillProgress,
    DueReviewItem,
    DueReviewResponse,
    OverdueGoal,
    CascadeReplanRequest,
    CascadeReplanResponse,
    CascadeReplanResult,
    DrillResponse,
    ErrorAnalysisResponse,
    ErrorSummaryResponse,
    ExamAnswerRequest,
    ExamAnswerResponse,
    ExamQuestionResponse,
    ExamResultResponse,
    GoalScheduleResponse,
    LearnerContextResponse,
    LearningIndex,
    MaterialCandidatesResponse,
    TopicResourceCreate,
    TopicResourceResponse,
    TopicSourcesResponse,
    PlanChange,
    PlanRevisionList,
    PlanRevisionResponse,
    ScheduledTopic,
    StartExamRequest,
    StrengthHistoryResponse,
    CreateGoalRequest,
    CreateTopicsRequest,
    DiagnosticReplyRequest,
    DiagnosticStartRequest,
    DiagnosticStateResponse,
    DiagnosticTurnResponse,
    ExplainRequest,
    ExplainResponse,
    TopicNoteIndexResponse,
    TopicNoteRequest,
    TopicNoteResponse,
    GenerateTaskRequest,
    GenerateTopicsRequest,
    GoalResponse,
    HintResponse,
    PlanItemPatch,
    PlanItemResponse,
    PlanResponse,
    ProgressResponse,
    ProfileResponse,
    PullAheadRequest,
    PullAheadResponse,
    QuestionResponse,
    ReplanRequest,
    SubmitAnswerRequest,
    TopicProgressItem,
    TopicResponse,
    UpdateGoalRequest,
    UpdateProfileRequest,
    UpdateTopicRequest,
    goal_to_response,
    topic_to_response,
)
from services.adaptive_learning import (
    answer_scoring,
    code_review,
    comprehension,
    content,
    daily,
    error_analysis,
    exam as exam_service,
    context as learner_context,
    drills as drill_ops,
    task_formats,
    topic_flow,
    topic_material,
    topic_notes,
    plan_diff,
    forecast as forecast_mod,
    plan_ops,
)
from services.adaptive_learning.diagnostic import DiagnosticSession
from services.adaptive_learning.persistence import (
    apply_scored_answer,
    compute_and_store_plan,
    compute_speed,
    ensure_topic_state,
    generate_topics_for_goal,
    load_states,
    load_topic_inputs,
)
from services.auth.dependency import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# The plan's final two rows are not tied to one topic. The API still has to give
# them a readable title, because the calendar and the plan list show one line per
# row and an empty title would look like a bug.
_SPECIAL_PLAN_TITLES = {
    "practice": "Практика для допуска к экзамену",
    "exam": "Экзамен",
}


def _plan_item_title(
    item_type: str, topic_id: uuid.UUID | str | None, titles: dict[str, str]
) -> str:
    if topic_id:
        return titles.get(str(topic_id), "Тема")
    return _SPECIAL_PLAN_TITLES.get(item_type, "Работа")


def _same_plan_topic(
    row_topic_id: uuid.UUID | None, planned_topic_id: str | None
) -> bool:
    if row_topic_id is None or planned_topic_id is None:
        return row_topic_id is None and planned_topic_id is None
    return str(row_topic_id) == planned_topic_id


_LLM_HINT = (
    "No LLM provider is configured, so this feature needs a model. "
    "Install Ollama or set an API key (OPENAI_API_KEY / ANTHROPIC_API_KEY / etc.)."
)


def _require_llm() -> None:
    """Fail fast with a clear message rather than inside the provider client."""
    try:
        from services.llm.router import get_registry
    except Exception as exc:  # pragma: no cover - defensive
        raise ValidationError(_LLM_HINT) from exc
    try:
        get_registry().get()
    except Exception as exc:
        raise ValidationError(_LLM_HINT) from exc


async def _get_goal(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> LearningGoal:
    result = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == goal_id, LearningGoal.user_id == user_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise NotFoundError("Goal", goal_id)
    return goal


async def _get_topic(db: AsyncSession, topic_id: uuid.UUID, user_id: uuid.UUID):
    result = await db.execute(
        select(LearningTopic)
        .join(LearningGoal, LearningGoal.id == LearningTopic.goal_id)
        .where(LearningTopic.id == topic_id, LearningGoal.user_id == user_id)
    )
    topic = result.scalar_one_or_none()
    if not topic:
        raise NotFoundError("Topic", topic_id)
    return topic


async def _apply_estimates_to_topics(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    estimates: list[dict],
    *,
    force: bool = False,
) -> int:
    """Fold a diagnostic's per-topic estimates into ``TopicState`` (FR-4.4).

    The planner reads ``TopicState`` and nothing else. Until the estimates were
    written here the dialogue's whole output stopped at the session row, so the
    plan was built as if the learner knew nothing on every topic — the inferred
    level never reached it.

    ``force`` distinguishes the two callers. The dialogue's own guess leaves a
    topic alone when the learner has already practised it: a real graded answer
    beats an inference from two probes, and overwriting it would lose the answer
    history. A manual correction (FR-4.5) is the learner speaking, so it always
    wins.

    Returns how many topic states were written.
    """
    if not estimates:
        return 0

    topic_ids = []
    for entry in estimates:
        try:
            topic_ids.append(uuid.UUID(str(entry.get("id"))))
        except (ValueError, TypeError, AttributeError):
            logger.warning("Diagnostic estimate has an unusable topic id: %r", entry)
    if not topic_ids:
        return 0

    # Scoped to the goal and the learner, so a stale estimate cannot name a topic
    # from a different goal and be applied through this one.
    owned = await db.execute(
        select(LearningTopic.id).where(
            LearningTopic.id.in_(topic_ids),
            LearningTopic.goal_id == goal_id,
        )
    )
    owned_ids = {row[0] for row in owned.all()}
    if not owned_ids:
        return 0

    written = 0
    for entry in estimates:
        try:
            topic_id = uuid.UUID(str(entry.get("id")))
        except (ValueError, TypeError, AttributeError):
            continue
        if topic_id not in owned_ids:
            continue
        try:
            value = float(entry.get("estimate"))
        except (TypeError, ValueError):
            continue
        estimate = max(0.0, min(1.0, value))

        state = await ensure_topic_state(db, goal_id, user_id, topic_id)
        # A practised topic keeps what its real answers said. The check is on
        # `last_review_at` rather than on strength, because strength is also what
        # a previous run of this same dialogue wrote.
        if not force and state.last_review_at is not None:
            continue

        state.strength = estimate
        state.status = _status_for_estimate(estimate)
        written += 1

    if written:
        await db.flush()
        logger.info("Goal %s: diagnostic wrote %d topic estimate(s)", goal_id, written)
    return written


def _status_for_estimate(estimate: float) -> str:
    """The status band a stored strength implies.

    Same thresholds ``correct_diagnostic`` used, moved here so the automatic and
    manual paths cannot drift apart.
    """
    if estimate >= 0.8:
        return "known"
    if estimate < 0.5:
        return "weak"
    return "learning"


async def _set_topic_source(
    db: AsyncSession,
    user_id: uuid.UUID,
    topic,
    node_id: str | None,
) -> None:
    """Pin a topic to one passage, or clear the pin.

    The node is checked against the goal's own course rather than trusted: the
    column is a plain id and the request comes from the client, so without this
    a topic could be pointed at another course's text.
    """
    if not node_id:
        topic.source_node_id = None
        return

    from libs.exceptions import ValidationError

    try:
        wanted = uuid.UUID(str(node_id))
    except ValueError as exc:
        raise ValidationError("Некорректный идентификатор материала") from exc

    goal_row = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == topic.goal_id, LearningGoal.user_id == user_id
        )
    )
    goal = goal_row.scalar_one_or_none()
    if goal is None or goal.course_id is None:
        raise ValidationError("Цель не привязана к курсу — материал выбрать нельзя")

    from models.content import CourseContentTree

    node_row = await db.execute(
        select(CourseContentTree).where(
            CourseContentTree.id == wanted,
            CourseContentTree.course_id == goal.course_id,
        )
    )
    # One read: a result set is consumed by the first scalar call, so asking it
    # twice raised NoResultFound and turned a valid pin into a server error.
    node = node_row.scalar_one_or_none()
    if node is None:
        raise ValidationError("Такого материала в курсе нет")
    if not (node.content or "").strip():
        raise ValidationError("Этот раздел пустой — из него нечего объяснять")

    topic.source_node_id = wanted


async def _grounding_for_topic(
    db: AsyncSession,
    user_id: uuid.UUID,
    topic,
    *,
    limit: int = 4,
    source_file: str | None = None,
    ground: bool = True,
) -> tuple[list[dict], str]:
    """Passages to explain this topic from, and a label naming their files.

    The learner can name the file at generation time (``source_file``) or ask
    for no literature at all (``ground=False``). Both are explicit and win over
    the guesses below. Otherwise a topic's pinned ``source_node_id`` is used
    alone, and failing that the course's material is searched. Either way the
    explanation carries a reference back, which is what FR-3.4 asks for and what
    lets a learner check the model against their own book.
    """
    from services.adaptive_learning import topic_material as topic_material_service

    if not ground:
        return [], ""

    goal_row = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == topic.goal_id, LearningGoal.user_id == user_id
        )
    )
    goal = goal_row.scalar_one_or_none()
    if goal is None or goal.course_id is None:
        return [], ""

    # A file chosen for this generation is more specific than an old node pin:
    # it is the most recent explicit instruction. An unknown file yields no
    # passages rather than silently falling back to the noisy course-wide match.
    if source_file:
        chosen = await topic_material_service.file_passages(
            db,
            goal.course_id,
            source_file,
            topic_title=topic.title,
            topic_description=topic.description,
            limit=limit,
        )
        return chosen, source_file if chosen else ""

    if topic.source_node_id:
        pinned = await topic_material_service.get_passages_by_ids(
            db, goal.course_id, [topic.source_node_id]
        )
        if pinned:
            return pinned, pinned[0].get("source_file") or ""

    passages = await topic_material_service.find_material_for_topic(
        db,
        goal.course_id,
        topic.title,
        topic.description,
        limit=limit,
    )
    if not passages:
        return [], ""

    files = [p.get("source_file") for p in passages if p.get("source_file")]
    label = ", ".join(list(dict.fromkeys(files))[:3])
    return passages, label


async def _topics_with_explanations(
    db: AsyncSession, user_id: uuid.UUID, topics: list[LearningTopic]
) -> set[uuid.UUID]:
    """Which of these topics already have a saved explanation, in one query.

    Asked per topic this would be N+1 on the topic list, which is the screen
    that most needs the answer — it is where the learner sees which topics can
    produce a note or a quiz and which cannot.
    """
    if not topics:
        return set()
    rows = await db.execute(
        select(GeneratedAsset.metadata_, GeneratedAsset.content).where(
            GeneratedAsset.user_id == user_id,
            GeneratedAsset.asset_type == "explanation",
        )
    )
    titles = {str(t.id) for t in topics}
    found: set[uuid.UUID] = set()
    for metadata, content in rows.all():
        raw = (metadata or {}).get("topic_id")
        if not raw or str(raw) not in titles:
            continue
        text = content.get("text") if isinstance(content, dict) else content
        if isinstance(text, str) and text.strip():
            try:
                found.add(uuid.UUID(str(raw)))
            except ValueError:
                continue
    return found


async def _topic_count(db: AsyncSession, goal_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count(LearningTopic.id)).where(LearningTopic.goal_id == goal_id)
        )
        or 0
    )


async def _profile_row(db: AsyncSession, user_id: uuid.UUID) -> UserProfile | None:
    row = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    return row.scalar_one_or_none()


async def _ensure_profile(db: AsyncSession, user_id: uuid.UUID) -> UserProfile:
    """The learner's profile row, created on first use.

    The guided daily mode needs somewhere to remember the chosen goal. Creating
    the profile here keeps the older goal endpoints working for accounts that
    have never opened settings.
    """
    profile = await _profile_row(db, user_id)
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        await db.flush()
    return profile


async def _active_goal_id(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """The explicitly selected goal, if it still exists and is still active.

    The status check is deliberate: a goal can be paused, completed or deleted
    from another screen after it was selected. Returning None makes the UI fall
    back to its old nearest-deadline choice instead of opening a closed goal.
    """
    profile = await _profile_row(db, user_id)
    goal_id = getattr(profile, "active_goal_id", None)
    if not goal_id:
        return None
    row = await db.execute(
        select(LearningGoal.id).where(
            LearningGoal.id == goal_id,
            LearningGoal.user_id == user_id,
            LearningGoal.status == "active",
        )
    )
    return row.scalar_one_or_none()


async def _choose_active_goal(
    db: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID
) -> None:
    profile = await _ensure_profile(db, user_id)
    profile.active_goal_id = goal_id


async def _clear_active_goal(
    db: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID
) -> None:
    """Stop pointing at a goal that is being closed or deleted."""
    profile = await _profile_row(db, user_id)
    if profile is not None and profile.active_goal_id == goal_id:
        profile.active_goal_id = None


async def _mark_special_plan_items_done(
    db: AsyncSession, goal_id: uuid.UUID, item_types: tuple[str, ...]
) -> int:
    """Close the task-only/exam rows once their real event happened.

    The practice day is done when the exam gate opens (or when the learner
    explicitly completes the practice session); the exam day is done when the
    exam is finished. Keeping this in one place stops the calendar from showing
    a passed exam as a permanently pending task.
    """
    rows = list(
        (
            await db.execute(
                select(LearningPlanItem).where(
                    LearningPlanItem.goal_id == goal_id,
                    LearningPlanItem.item_type.in_(item_types),
                    LearningPlanItem.status.in_(("pending", "missed")),
                    LearningPlanItem.rejected.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        item.status = "done"
        if item.actual_minutes is None:
            item.actual_minutes = item.planned_minutes
    return len(rows)


async def _mark_topic_practice_done(
    db: AsyncSession, goal_id: uuid.UUID, topic_id: uuid.UUID
) -> int:
    """Close control-assignment rows once their series is actually finished.

    Practice items can be taken ahead of their day. A finished series is proof
    the assignment was done, so every scheduled control task for this topic is
    counted as done — including one still dated in the future. Leaving them
    pending made a control assignment completed ahead look undone.
    """
    rows = list(
        (
            await db.execute(
                select(LearningPlanItem).where(
                    LearningPlanItem.goal_id == goal_id,
                    LearningPlanItem.topic_id == topic_id,
                    LearningPlanItem.item_type == "practice",
                    LearningPlanItem.status.in_(("pending", "missed")),
                    LearningPlanItem.rejected.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        item.status = "done"
        if item.actual_minutes is None:
            item.actual_minutes = item.planned_minutes
    return len(rows)


# ──────────────────────────────────────────────────────────────
# Goals — TZ /goals (U1)
# ──────────────────────────────────────────────────────────────


@router.get("/learning/goals", response_model=list[GoalResponse])
async def list_goals(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(LearningGoal).where(LearningGoal.user_id == user.id)
    if status:
        query = query.where(LearningGoal.status == status)
    result = await db.execute(
        query.order_by(LearningGoal.created_at.desc()).limit(limit)
    )
    goals = result.scalars().all()
    active_id = await _active_goal_id(db, user.id)
    return [
        goal_to_response(
            g,
            topic_count=await _topic_count(db, g.id),
            is_active=g.id == active_id,
        )
        for g in goals
    ]


@router.post("/learning/goals", response_model=GoalResponse, status_code=201)
async def create_goal(
    body: CreateGoalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """U1: the learner states a topic, a deadline and their available time."""
    if body.course_id is not None:
        await _check_course(db, body.course_id, user.id)

    goal = LearningGoal(
        user_id=user.id,
        topic=body.topic,
        deadline=body.resolved_deadline(),
        minutes_per_day=body.minutes_per_day,
        depth=body.depth,
        status="active",
        course_id=body.course_id,
    )
    if body.time_share:
        goal.metadata_json = {"time_share": body.time_share}
    db.add(goal)
    # A just-created goal is the one the learner is about to work on. Selecting
    # it explicitly removes the old "nearest deadline wins" guess after the
    # first goal of a course has been created.
    await db.flush()
    await _choose_active_goal(db, user.id, goal.id)
    await db.commit()
    await db.refresh(goal)
    logger.info("Created goal %s for user %s (active)", goal.id, user.id)
    return goal_to_response(
        goal,
        topic_count=0,
        course_name=await _course_name(db, body.course_id),
        is_active=True,
    )


async def _check_course(
    db: AsyncSession, course_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """A goal may only attach to a course the learner actually owns."""
    from models.course import Course

    row = await db.execute(
        select(Course.id).where(Course.id == course_id, Course.user_id == user_id)
    )
    if row.scalar_one_or_none() is None:
        raise NotFoundError("Course", course_id)


async def _course_name(db: AsyncSession, course_id: uuid.UUID | None) -> str | None:
    if course_id is None:
        return None
    from models.course import Course

    row = await db.execute(select(Course.name).where(Course.id == course_id))
    return row.scalar()


@router.get("/learning/courses/{course_id}/goals", response_model=CourseGoalsResponse)
async def list_course_goals(
    course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Goals that hang off this course workspace, newest first."""
    await _check_course(db, course_id, user.id)

    goals = list(
        (
            await db.execute(
                select(LearningGoal)
                .where(
                    LearningGoal.course_id == course_id,
                    LearningGoal.user_id == user.id,
                )
                .order_by(LearningGoal.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    name = await _course_name(db, course_id)

    # One batched pass for every goal's topics, states and exam, instead of three
    # queries per row. The course page shows progress for all of them at once.
    from services.adaptive_learning import topic_flow

    progress = await topic_flow.course_goal_progress(db, user.id, [g.id for g in goals])
    active_id = await _active_goal_id(db, user.id)

    return CourseGoalsResponse(
        course_id=str(course_id),
        course_name=name,
        goals=[
            goal_to_response(
                g,
                topic_count=progress[g.id].topics_total if g.id in progress else 0,
                course_name=name,
                progress=progress.get(g.id),
                is_active=g.id == active_id,
            )
            for g in goals
        ],
    )


def _revision_to_response(row: PlanRevisionModel) -> PlanRevisionResponse:
    return PlanRevisionResponse(
        id=str(row.id),
        goal_id=str(row.goal_id),
        trigger=row.trigger,
        summary=row.summary,
        status=row.status,
        changes=[PlanChange(**c) for c in (row.changes or [])],
        created_at=row.created_at.isoformat() if row.created_at else None,
        resolved_at=row.resolved_at.isoformat() if row.resolved_at else None,
    )


@router.get("/learning/goals/{goal_id}/plan/revisions", response_model=PlanRevisionList)
async def list_plan_revisions(
    goal_id: uuid.UUID,
    status: str | None = Query(None, pattern="^(pending|accepted|rejected)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-10.4: what the plan replan changed, and whether it was accepted."""
    await _get_goal(db, goal_id, user.id)

    query = select(PlanRevisionModel).where(
        PlanRevisionModel.goal_id == goal_id,
        PlanRevisionModel.user_id == user.id,
    )
    if status:
        query = query.where(PlanRevisionModel.status == status)

    rows = list(
        (
            await db.execute(
                query.order_by(PlanRevisionModel.created_at.desc()).limit(20)
            )
        )
        .scalars()
        .all()
    )
    pending_count = len(await plan_diff.pending(db, goal_id, user.id))
    return PlanRevisionList(
        goal_id=str(goal_id),
        revisions=[_revision_to_response(r) for r in rows],
        pending_count=pending_count,
    )


@router.post(
    "/learning/goals/{goal_id}/plan/revisions/{revision_id}/reject",
    response_model=PlanRevisionResponse,
)
async def reject_plan_revision(
    goal_id: uuid.UUID,
    revision_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-10.4: decline the change.

    The affected rows are kept as they were and flagged rejected, so the next
    replan leaves them alone instead of quietly re-applying what was declined.
    """
    await _get_goal(db, goal_id, user.id)
    row = await db.execute(
        select(PlanRevisionModel).where(
            PlanRevisionModel.id == revision_id,
            PlanRevisionModel.goal_id == goal_id,
            PlanRevisionModel.user_id == user.id,
        )
    )
    revision = row.scalar_one_or_none()
    if not revision:
        raise NotFoundError("Plan revision", revision_id)

    resolved = await plan_diff.resolve(db, revision, accept=False)
    return _revision_to_response(resolved)


@router.post(
    "/learning/goals/{goal_id}/plan/revisions/{revision_id}/accept",
    response_model=PlanRevisionResponse,
)
async def accept_plan_revision(
    goal_id: uuid.UUID,
    revision_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-10.4: keep the new plan and stop asking."""
    await _get_goal(db, goal_id, user.id)
    row = await db.execute(
        select(PlanRevisionModel).where(
            PlanRevisionModel.id == revision_id,
            PlanRevisionModel.goal_id == goal_id,
            PlanRevisionModel.user_id == user.id,
        )
    )
    revision = row.scalar_one_or_none()
    if not revision:
        raise NotFoundError("Plan revision", revision_id)

    # Accepting clears any decline flags so this change stays in force.
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
                        LearningPlanItem.goal_id == goal_id,
                        LearningPlanItem.topic_id.in_(topic_ids),
                        LearningPlanItem.rejected.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        for item in rows:
            item.rejected = False
            if (item.reason or "").startswith("отклонено вами:"):
                item.reason = item.reason.split(": ", 1)[1] or None

    resolved = await plan_diff.resolve(db, revision, accept=True)
    return _revision_to_response(resolved)


@router.get("/learning/goals/{goal_id}/schedule", response_model=GoalScheduleResponse)
async def get_goal_schedule(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The plan and the topics in one list: dates and progress side by side.

    The plan and the topic list used to be separate tabs, which meant seeing
    "what to study" and "when" took two switches. This returns both together,
    plus how much of each topic's task series is done.
    """
    goal = await _get_goal(db, goal_id, user.id)

    topics = list(
        (
            await db.execute(
                select(LearningTopic)
                .where(LearningTopic.goal_id == goal_id)
                .order_by(LearningTopic.order_index)
            )
        )
        .scalars()
        .all()
    )
    states = await load_states(db, goal_id, user.id)

    items = list(
        (
            await db.execute(
                select(LearningPlanItem).where(LearningPlanItem.goal_id == goal_id)
            )
        )
        .scalars()
        .all()
    )
    by_topic: dict[str, list[LearningPlanItem]] = {}
    for item in items:
        if item.topic_id:
            by_topic.setdefault(str(item.topic_id), []).append(item)

    drills = await drill_ops.progress_map(db, goal_id, user.id)

    rows: list[ScheduledTopic] = []
    for topic in topics:
        key = str(topic.id)
        planned = by_topic.get(key, [])
        # One topic can be scheduled more than once (study, then a review).
        study_days = sorted({item.day.isoformat() for item in planned})
        # Control assignments are the practice items tied to this topic —
        # each one is a separate scheduled slot the learner can see in the plan.
        # The row itself stores no title (``learning_plan_items`` has no such
        # column), so the name comes from the topic, exactly like the plan tab.
        practice_items = [
            {
                "id": str(item.id),
                "day": item.day.isoformat(),
                "title": topic.title,
                "planned_minutes": item.planned_minutes,
                "status": item.status,
                "reason": item.reason,
            }
            for item in planned
            if item.item_type == "practice"
        ]
        state = states.get(key)
        rows.append(
            ScheduledTopic(
                topic_id=key,
                title=topic.title,
                description=topic.description,
                order_index=topic.order_index,
                estimated_minutes=topic.estimated_minutes,
                first_day=study_days[0] if study_days else None,
                last_day=study_days[-1] if study_days else None,
                study_days=study_days,
                planned_minutes=sum(item.planned_minutes for item in planned),
                practice_items=practice_items,
                strength=round(state.strength, 3) if state else None,
                status=state.status if state else None,
                next_review_at=(
                    state.next_review_at.isoformat()
                    if state and state.next_review_at
                    else None
                ),
                drill=DrillProgress(**(drills.get(key) or {"prepared": False})),
            )
        )

    gate = await drill_ops.goal_gate(db, goal_id, user.id)
    return GoalScheduleResponse(
        goal_id=str(goal.id),
        topics=rows,
        exam_unlocked=gate["unlocked"],
        topics_total=gate["topics_total"],
        topics_done=gate["topics_done"],
        pending_exam=gate["pending"],
    )


@router.post(
    "/learning/topics/{topic_id}/drill",
    response_model=DrillResponse,
    status_code=201,
)
async def prepare_drill(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    language: str | None = None,
):
    """Prepare this subtopic's task series, easiest first.

    Creates it on first call and returns the existing one afterwards, so opening
    a topic twice never produces six tasks.
    """
    topic = await _get_topic(db, topic_id, user.id)
    goal_row = await db.execute(
        select(LearningGoal).where(LearningGoal.id == topic.goal_id)
    )
    goal = goal_row.scalar_one()

    # Reading an existing series is not a generation request. Without this
    # check, an install whose model provider went away could not even reopen
    # tasks it had already prepared — the exact moment the fallback matters.
    existing = await db.execute(
        select(TopicDrillModel).where(
            TopicDrillModel.goal_id == goal.id,
            TopicDrillModel.topic_id == topic.id,
        )
    )
    if existing.scalar_one_or_none() is None:
        _require_llm()

    # The interface language chosen in settings. The tasks are written in it, so a
    # learner who reads English does not get Russian prompts because the goal was
    # created before the setting existed.
    resolved_language = (language or "").strip().lower()
    if resolved_language not in ("ru", "en"):
        resolved_language = await _profile_language(db, user.id)

    try:
        drill, created = await drill_ops.ensure_drill(
            db, goal, topic, language=resolved_language
        )
    except RuntimeError as exc:
        raise ValidationError(str(exc)) from exc

    questions = await _questions_by_ids(db, drill.question_ids or [])
    info = (await drill_ops.progress_map(db, goal.id, user.id)).get(str(topic.id), {})

    return DrillResponse(
        topic_id=str(topic.id),
        title=topic.title,
        drill=DrillProgress(**info),
        questions=[question_to_response(q, topic.title) for q in questions],
    )


async def _questions_by_ids(db: AsyncSession, ids: list[str]) -> list[LearningQuestion]:
    if not ids:
        return []
    wanted = [uuid.UUID(str(i)) for i in ids]
    rows = list(
        (
            await db.execute(
                select(LearningQuestion).where(LearningQuestion.id.in_(wanted))
            )
        )
        .scalars()
        .all()
    )
    found = {row.id: row for row in rows}
    return [found[i] for i in wanted if i in found]


@router.get("/learning/index", response_model=LearningIndex)
async def get_learning_index(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The whole learning tree in one call: courses, each with its goals.

    A course is the container, so this is what the UI renders as the entry
    point. Goals with no course are returned separately instead of being hidden,
    which keeps older standalone work reachable and attachable.
    """
    from models.course import Course

    courses = list(
        (
            await db.execute(
                select(Course)
                .where(Course.user_id == user.id)
                .order_by(Course.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    goals = list(
        (await db.execute(select(LearningGoal).where(LearningGoal.user_id == user.id)))
        .scalars()
        .all()
    )

    by_course: dict[uuid.UUID, list[LearningGoal]] = {}
    unattached: list[LearningGoal] = []
    for goal in goals:
        if goal.course_id is None:
            unattached.append(goal)
        else:
            by_course.setdefault(goal.course_id, []).append(goal)

    active_id = await _active_goal_id(db, user.id)

    groups: list[CourseGoalsGroup] = []
    for course in courses:
        rows = by_course.get(course.id, [])
        groups.append(
            CourseGoalsGroup(
                course_id=str(course.id),
                course_name=course.name,
                goal_count=len(rows),
                goals=[
                    goal_to_response(
                        g,
                        topic_count=await _topic_count(db, g.id),
                        course_name=course.name,
                        is_active=g.id == active_id,
                    )
                    for g in sorted(rows, key=lambda g: g.created_at, reverse=True)
                ],
            )
        )

    # A goal may reference a course the user no longer has (shared/renamed rows);
    # surface it with its goal group rather than dropping it.
    known = {c.id for c in courses}
    for course_id, rows in by_course.items():
        if course_id in known:
            continue
        groups.append(
            CourseGoalsGroup(
                course_id=str(course_id),
                course_name="Курс недоступен",
                goal_count=len(rows),
                goals=[
                    goal_to_response(
                        g,
                        topic_count=await _topic_count(db, g.id),
                        is_active=g.id == active_id,
                    )
                    for g in rows
                ],
            )
        )

    return LearningIndex(
        courses=groups,
        unattached=[
            goal_to_response(
                g,
                topic_count=await _topic_count(db, g.id),
                is_active=g.id == active_id,
            )
            for g in sorted(unattached, key=lambda g: g.created_at, reverse=True)
        ],
        total_goals=len(goals),
        active_goal_id=str(active_id) if active_id else None,
    )


@router.get(
    "/learning/context",
    response_model=LearnerContextResponse,
)
async def get_learner_context(
    goal_id: uuid.UUID | None = Query(None),
    course_id: uuid.UUID | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """TZ section 9: what we already know about this learner.

    Feeds subtopic generation, diagnostics and task wording, and is shown on the
    goal page so the learner can see the same picture the model gets.
    """
    if goal_id is not None:
        await _get_goal(db, goal_id, user.id)
    if course_id is not None:
        await _check_course(db, course_id, user.id)

    ctx = await learner_context.build(db, user.id, goal_id=goal_id, course_id=course_id)
    return LearnerContextResponse(**learner_context.describe_for_ui(ctx))




# ──────────────────────────────────────────────────────────────
# Overdue goals and cascade replanning — FR-10.4 cross-goal
# ──────────────────────────────────────────────────────────────

@router.get(
    "/learning/goals/overdue",
    response_model=list[OverdueGoal],
)
async def list_overdue_goals(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List goals that have work falling behind their plan.

    A goal is overdue when it has plan items with status=missed, or pending items
    whose day is in the past. Returns the count of overdue items per goal so the
    learner can decide which goals to shift forward.
    """
    today = datetime.now(timezone.utc).date()

    goals = list(
        (
            await db.execute(
                select(LearningGoal).where(
                    LearningGoal.user_id == user.id,
                    LearningGoal.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    if not goals:
        return []

    goal_ids = [g.id for g in goals]
    items = list(
        (
            await db.execute(
                select(LearningPlanItem).where(
                    LearningPlanItem.goal_id.in_(goal_ids),
                    or_(
                        LearningPlanItem.status == "missed",
                        and_(
                            LearningPlanItem.status == "pending",
                            LearningPlanItem.day < today,
                        ),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    # Aggregate per goal
    per_goal: dict[str, dict] = {}
    for g in goals:
        per_goal[str(g.id)] = {
            "goal_id": str(g.id),
            "goal_title": g.topic,
            "course_id": str(g.course_id) if g.course_id else None,
            "overdue_count": 0,
            "missed_count": 0,
            "pending_past_count": 0,
            "earliest_overdue_day": None,
        }

    for item in items:
        gid = str(item.goal_id)
        entry = per_goal.get(gid)
        if not entry:
            continue
        entry["overdue_count"] += 1
        if item.status == "missed":
            entry["missed_count"] += 1
        elif item.status == "pending" and item.day < today:
            entry["pending_past_count"] += 1
        day_str = item.day.isoformat() if item.day else None
        if day_str and (
            entry["earliest_overdue_day"] is None
            or day_str < entry["earliest_overdue_day"]
        ):
            entry["earliest_overdue_day"] = day_str

    return [
        OverdueGoal(**entry)
        for entry in per_goal.values()
        if entry["overdue_count"] > 0
    ]


@router.post(
    "/learning/goals/cascade-replan",
    response_model=CascadeReplanResponse,
)
async def cascade_replan(
    body: CascadeReplanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Shift selected overdue goals forward by N days.

    For each goal, moves all pending/missed plan items forward by
    ``days_to_shift``. Records a PlanRevision per goal so the learner can
    review and accept or decline the changes.

    Only goals belonging to the current user are affected.
    """
    today = datetime.now(timezone.utc).date()
    results: list[CascadeReplanResult] = []

    for gid_str in body.goal_ids:
        goal_id = uuid.UUID(gid_str)
        goal = await _get_goal(db, goal_id, user.id)

        # Items to shift: pending or missed, with day <= today (overdue or due today)
        items = list(
            (
                await db.execute(
                    select(LearningPlanItem).where(
                        LearningPlanItem.goal_id == goal_id,
                        LearningPlanItem.status.in_(("pending", "missed")),
                        LearningPlanItem.day <= today,
                    )
                )
            )
            .scalars()
            .all()
        )

        if not items:
            continue

        shifted_count = 0
        min_new_day = None
        for item in items:
            old_day = item.day
            item.day = item.day + timedelta(days=body.days_to_shift)
            if item.status == "missed":
                item.status = "pending"
                item.reason = (
                    f"перенесено с {old_day.isoformat() if old_day else 'неизвестно'} "
                    f"(каскадный перенос)"
                )
            else:
                item.reason = (
                    f"перенесено на +{body.days_to_shift} дн. (каскадный перенос)"
                )
            db.add(item)
            shifted_count += 1
            new_day_str = item.day.isoformat() if item.day else None
            if min_new_day is None or (new_day_str and new_day_str < min_new_day):
                min_new_day = new_day_str

        await db.commit()

        results.append(
            CascadeReplanResult(
                goal_id=str(goal_id),
                goal_title=goal.topic,
                items_shifted=shifted_count,
                new_first_day=min_new_day,
            )
        )

    return CascadeReplanResponse(
        shifted=results,
        total_shifted=sum(r.items_shifted for r in results),
    )

@router.get("/learning/goals/{goal_id}", response_model=GoalResponse)
async def get_goal(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    goal = await _get_goal(db, goal_id, user.id)
    return goal_to_response(
        goal,
        topic_count=await _topic_count(db, goal.id),
        is_active=goal.id == await _active_goal_id(db, user.id),
    )


@router.post("/learning/goals/{goal_id}/activate", response_model=GoalResponse)
async def activate_goal(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Choose the goal the guided "Сегодня" mode should open.

    Only an active goal can be selected. A goal with no topics is allowed on
    purpose: the learner may want to set the direction before building its plan,
    and the daily entry screen explains that the selected goal has no topics
    yet instead of silently opening a different one.
    """
    goal = await _get_goal(db, goal_id, user.id)
    if goal.status != "active":
        raise ValidationError(
            "Выбрать для ежедневного режима можно только цель в работе"
        )

    await _choose_active_goal(db, user.id, goal.id)
    await db.commit()
    await db.refresh(goal)
    logger.info("User %s selected goal %s as active", user.id, goal.id)
    return goal_to_response(
        goal,
        topic_count=await _topic_count(db, goal.id),
        course_name=await _course_name(db, goal.course_id),
        is_active=True,
    )


@router.patch("/learning/goals/{goal_id}", response_model=GoalResponse)
async def update_goal(
    goal_id: uuid.UUID,
    body: UpdateGoalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-2.4: changing the deadline, time budget or depth triggers a replan."""
    goal = await _get_goal(db, goal_id, user.id)
    payload = body.model_dump(exclude_unset=True)

    if "topic" in payload and payload["topic"]:
        goal.topic = payload["topic"].strip()
    if "deadline" in payload and payload["deadline"]:
        goal.deadline = payload["deadline"]
    if payload.get("minutes_per_day"):
        goal.minutes_per_day = payload["minutes_per_day"]
    if payload.get("depth"):
        goal.depth = payload["depth"]
    if payload.get("status"):
        goal.status = payload["status"]
        goal.completed_at = (
            datetime.now(timezone.utc) if payload["status"] == "completed" else None
        )
        # A paused or finished goal cannot stay selected for "Сегодня".
        if goal.status != "active":
            await _clear_active_goal(db, user.id, goal.id)
    # Moving a goal between courses. Explicit "course_id" key matters: None is a
    # meaningful value here (detach), so membership has to be checked, not truth.
    if "course_id" in payload:
        new_course = payload["course_id"]
        if new_course is not None:
            await _check_course(db, new_course, user.id)
        goal.course_id = new_course
        logger.info("Goal %s moved to course %s", goal.id, new_course)

    await db.commit()
    await db.refresh(goal)
    return goal_to_response(
        goal,
        topic_count=await _topic_count(db, goal.id),
        is_active=goal.id == await _active_goal_id(db, user.id),
    )


@router.delete("/learning/goals/{goal_id}", status_code=204)
async def delete_goal(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    goal = await _get_goal(db, goal_id, user.id)
    await _clear_active_goal(db, user.id, goal.id)
    await db.delete(goal)
    await db.commit()
    return None


# ──────────────────────────────────────────────────────────────
# Topics — TZ /topics/{id} (U4)
# ──────────────────────────────────────────────────────────────


@router.get("/learning/goals/{goal_id}/topics", response_model=list[TopicResponse])
async def list_topics(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_goal(db, goal_id, user.id)
    result = await db.execute(
        select(LearningTopic)
        .where(LearningTopic.goal_id == goal_id)
        .order_by(LearningTopic.order_index, LearningTopic.created_at)
    )
    topics = list(result.scalars().all())

    state_rows = await db.execute(
        select(TopicState).where(
            TopicState.goal_id == goal_id, TopicState.user_id == user.id
        )
    )
    states = {str(s.topic_id): s for s in state_rows.scalars().all()}
    explained = await _topics_with_explanations(db, user.id, topics)
    return [
        # Membership on the UUID itself: the helper returns UUIDs, and comparing
        # a string against a set of UUIDs is always False.
        topic_to_response(t, states.get(str(t.id)), has_explanation=t.id in explained)
        for t in topics
    ]


@router.post(
    "/learning/goals/{goal_id}/topics",
    response_model=list[TopicResponse],
    status_code=201,
)
async def create_topics_manually(
    goal_id: uuid.UUID,
    body: CreateTopicsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add subtopics by hand. Also the offline path when no LLM is configured."""
    await _get_goal(db, goal_id, user.id)
    created: list[LearningTopic] = []
    for index, title in enumerate(body.topics):
        clean = title.strip()
        if not clean:
            continue
        topic = LearningTopic(
            goal_id=goal_id,
            title=clean[:300],
            estimated_minutes=30,
            order_index=index,
        )
        db.add(topic)
        created.append(topic)
    if not created:
        raise ValidationError("No usable topics in the request")
    await db.commit()
    for topic in created:
        await db.refresh(topic)
    return [topic_to_response(t) for t in created]


@router.post(
    "/learning/goals/{goal_id}/topics/generate",
    response_model=list[TopicResponse],
    status_code=201,
)
async def generate_topics(
    goal_id: uuid.UUID,
    body: GenerateTopicsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """TZ 6.1 step 1: split the goal into subtopics with the model.

    This is the missing first step of the flow. A goal created without it has no
    subtopics, and both the diagnostic (FR-4) and the planner (FR-6) refuse to
    start until they exist.
    """
    _require_llm()
    goal = await _get_goal(db, goal_id, user.id)

    try:
        created = await generate_topics_for_goal(
            db,
            goal_id,
            user.id,
            depth=body.depth or goal.depth,
            language=body.language or "ru",
        )
    except content.ContentGenerationError as exc:
        raise ValidationError(f"Не удалось разбить тему на подтемы: {exc}") from exc
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    return [topic_to_response(t) for t in created]


@router.get("/learning/topics/{topic_id}", response_model=TopicResponse)
async def get_topic(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    topic = await _get_topic(db, topic_id, user.id)
    state = await db.execute(
        select(TopicState).where(
            TopicState.topic_id == topic_id, TopicState.user_id == user.id
        )
    )
    return topic_to_response(topic, state.scalar_one_or_none(), has_explanation=None)


@router.get(
    "/learning/topics/{topic_id}/material",
    response_model=MaterialCandidatesResponse,
    summary="What this topic can be explained from",
    description="Passages of the course's uploaded literature that match the topic, plus the one it is pinned to.",
)
async def topic_material_candidates(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-3.2, read side: show the learner what the explanation will be based on.

    Returns the automatic matches *and* whether the course has any material at
    all. Without that second flag an empty list is unreadable — "nothing matched
    this topic" and "you have uploaded nothing" look identical without it, and
    they call for opposite actions.
    """
    from models.content import CourseContentTree

    from services.adaptive_learning import topic_material as material_service

    topic = await _get_topic(db, topic_id, user.id)
    goal_row = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == topic.goal_id, LearningGoal.user_id == user.id
        )
    )
    goal = goal_row.scalar_one_or_none()
    if goal is None or goal.course_id is None:
        return MaterialCandidatesResponse(topic_id=str(topic_id))

    has_material = bool(
        await db.scalar(
            select(func.count(CourseContentTree.id)).where(
                CourseContentTree.course_id == goal.course_id,
                CourseContentTree.content.is_not(None),
            )
        )
    )

    automatic = await material_service.find_material_for_topic(
        db, goal.course_id, topic.title, topic.description, limit=6
    )
    labels = [p.get("source_file") for p in automatic if p.get("source_file")]
    return MaterialCandidatesResponse(
        topic_id=str(topic_id),
        course_id=str(goal.course_id),
        source_node_id=str(topic.source_node_id) if topic.source_node_id else None,
        course_has_material=has_material,
        automatic=automatic,
        source_label=", ".join(list(dict.fromkeys(labels))[:3]),
    )


@router.get(
    "/learning/topics/{topic_id}/sources",
    response_model=TopicSourcesResponse,
    summary="Uploaded files this topic can be explained from",
    description="The course's uploaded literature, offered before generating an explanation. Choosing one replaces the automatic keyword match.",
)
async def list_topic_sources(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """File-level choice for the explanation.

    The automatic match guesses which passage belongs to a topic. A learner who
    uploaded several books knows which one their goal is about, so the
    generation screen offers the files themselves and lets them say it.
    """
    from services.adaptive_learning import topic_material as material_service

    topic = await _get_topic(db, topic_id, user.id)
    goal_row = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == topic.goal_id, LearningGoal.user_id == user.id
        )
    )
    goal = goal_row.scalar_one_or_none()
    if goal is None or goal.course_id is None:
        return TopicSourcesResponse(topic_id=str(topic_id))

    files = await material_service.list_course_sources(db, goal.course_id)
    return TopicSourcesResponse(
        topic_id=str(topic_id),
        course_id=str(goal.course_id),
        files=files,
    )


@router.get(
    "/learning/topics/{topic_id}/resources",
    response_model=list[TopicResourceResponse],
    summary="Links the learner put beside this topic",
)
async def list_topic_resources(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_topic(db, topic_id, user.id)
    rows = await db.execute(
        select(TopicResource)
        .where(TopicResource.topic_id == topic_id)
        .order_by(TopicResource.order_index, TopicResource.created_at)
    )
    return [_resource_response(r) for r in rows.scalars().all()]


@router.post(
    "/learning/topics/{topic_id}/resources",
    response_model=TopicResourceResponse,
    status_code=201,
    summary="Add a link to a video for this topic",
)
async def add_topic_resource(
    topic_id: uuid.UUID,
    body: TopicResourceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store a video link the learner chose themselves.

    Deliberately unsearched and ungenerated. A model asked for a video URL
    produces a plausible address for a video that does not exist, and the learner
    only discovers that by clicking something that looked trustworthy. These rows
    are links someone verified by using them.
    """
    await _get_topic(db, topic_id, user.id)

    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValidationError("Ссылка должна начинаться с http:// или https://")

    existing = await db.execute(
        select(TopicResource).where(
            TopicResource.topic_id == topic_id, TopicResource.url == url
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValidationError("Эта ссылка уже добавлена")

    next_order = (
        int(
            await db.scalar(
                select(func.max(TopicResource.order_index)).where(
                    TopicResource.topic_id == topic_id
                )
            )
            or 0
        )
        + 1
    )

    row = TopicResource(
        topic_id=topic_id,
        kind=(body.kind or "video").strip()[:20] or "video",
        url=url[:1000],
        title=(body.title or "").strip()[:300] or None,
        note=(body.note or "").strip() or None,
        order_index=next_order,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _resource_response(row)


@router.delete("/learning/topics/{topic_id}/resources/{resource_id}", status_code=204)
async def delete_topic_resource(
    topic_id: uuid.UUID,
    resource_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_topic(db, topic_id, user.id)
    rows = await db.execute(
        select(TopicResource).where(
            TopicResource.id == resource_id, TopicResource.topic_id == topic_id
        )
    )
    row = rows.scalar_one_or_none()
    if row is None:
        raise NotFoundError("Resource", resource_id)
    await db.delete(row)
    await db.commit()
    return None


def _resource_response(row: TopicResource) -> TopicResourceResponse:
    return TopicResourceResponse(
        id=str(row.id),
        topic_id=str(row.topic_id),
        kind=row.kind,
        url=row.url,
        title=row.title,
        note=row.note,
    )


@router.post("/learning/topics/{topic_id}/explain", response_model=ExplainResponse)
async def explain_topic(
    topic_id: uuid.UUID,
    body: ExplainRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-7.1 explanation, FR-7.2 'explain it differently'.

    The regenerated variant explicitly excludes the previous text so the button
    actually produces a different angle.
    """
    _require_llm()
    topic = await _get_topic(db, topic_id, user.id)

    previous = ""
    if body.regenerate:
        rows = await db.execute(
            select(GeneratedAsset)
            .where(
                GeneratedAsset.user_id == user.id,
                GeneratedAsset.asset_type == "explanation",
                # ``metadata_`` is the attribute; the column is "metadata", which
                # SQLAlchemy reserves for declarative classes.
                GeneratedAsset.metadata_.contains(str(topic_id)),
            )
            .order_by(GeneratedAsset.created_at.desc())
            .limit(1)
        )
        found = rows.scalar_one_or_none()
        if found:
            # ``content`` is a JSON column, so an explanation is stored as
            # {"text": ...}. Older rows may hold a bare string. Named ``stored``
            # because ``content`` is also the imported service module used below.
            stored = found.content
            if isinstance(stored, dict):
                previous = stored.get("text", "")
            elif isinstance(stored, str):
                previous = stored

    # FR-3.2 / FR-3.4: ground the explanation in the learner's own literature
    # rather than in the model's recall. A topic the learner pinned to a node
    # wins; otherwise the course material is searched, and the one-line
    # description is only the last resort — it was the only source before, which
    # is why explanations talked about material the learner never supplied.
    passages, source_label = await _grounding_for_topic(
        db,
        user.id,
        topic,
        source_file=body.source_file,
        ground=body.ground,
    )
    source_text, label = (
        topic_material.as_grounding_text(passages)
        if passages
        else ((topic.description or ""), "")
    )

    text = await content.explain_topic(
        topic_title=topic.title,
        style=body.style,
        source_text=source_text,
        source_label=label,
        learner_language=(
            (body.language or "").strip().lower()
            if (body.language or "").strip().lower() in ("ru", "en")
            else await _profile_language(db, user.id)
        ),
        previous_explanation=previous,
    )

    db.add(
        GeneratedAsset(
            user_id=user.id,
            asset_type="explanation",
            title=f"Explanation: {topic.title}",
            content={"text": text},
            # Attribute is ``metadata_`` because SQLAlchemy reserves the name
            # ``metadata``; passing ``metadata_json`` raised a TypeError and made
            # every explanation request fail with a 500.
            metadata_={
                "topic_id": str(topic_id),
                "style": body.style,
                # Kept so the topic screen can say which passages this
                # explanation was written from, and offer to redo it against
                # different ones.
                "source_node_ids": [p["node_id"] for p in passages],
                "source_label": label,
            },
        )
    )
    await db.commit()
    return ExplainResponse(
        topic_id=str(topic_id),
        style=body.style,
        text=text,
        # "ИИ сам" produces no label; a chosen file keeps its name even when
        # the description supplied the fallback text.
        source_label=source_label or None,
    )


# ── Comprehension check ──
#
# The check is a short dialogue, not a quiz. It reads the explanation the learner
# actually saw, decomposes it into key units, diagnoses a free-recall answer, and
# re-teaches only the unit that failed. The state lives in its own table so a
# reload resumes the same conversation.


@router.post(
    "/learning/topics/{topic_id}/comprehension/start",
    response_model=ComprehensionStartResponse,
)
async def start_comprehension(
    topic_id: uuid.UUID,
    body: ComprehensionStartRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start, or resume, a check of whether the topic was understood."""
    _require_llm()
    topic = await _get_topic(db, topic_id, user.id)
    language = _resolve_language(
        body.language if body else None,
        await _profile_language(db, user.id),
    )
    explanation = await topic_flow.saved_explanation(db, topic.id, user.id)
    if not explanation.strip():
        raise ValidationError(
            "Сначала прочитайте объяснение темы — проверять пока нечего."
            if language != "en"
            else "Read the topic explanation first — there is nothing to check yet."
        )

    # FR-3.4: the check may quote the learner's own material, not only the
    # model's paraphrase. The explanation is still the primary text.
    passages, source_label = await _grounding_for_topic(db, user.id, topic)
    source_text = topic_material.as_grounding_text(passages) if passages else ""
    material = comprehension.combine_material(explanation, source_text)
    session, resumed = await comprehension.start_session(
        db,
        topic=topic,
        user_id=user.id,
        language=language,
        material=material,
        source_label=source_label
        or ("объяснение темы" if language == "ru" else "topic explanation"),
    )
    await db.commit()
    return ComprehensionStartResponse(
        **comprehension.start_payload(session, topic, resumed=resumed)
    )


@router.post(
    "/learning/topics/{topic_id}/comprehension/{session_id}/answer",
    response_model=ComprehensionTurnResponse,
)
async def answer_comprehension(
    topic_id: uuid.UUID,
    session_id: uuid.UUID,
    body: ComprehensionAnswerRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Diagnose one answer. Code decides whether to probe, re-teach or stop."""
    _require_llm()
    topic = await _get_topic(db, topic_id, user.id)
    session = await comprehension.load_session(db, session_id, topic.id, user.id)
    if session is None:
        raise NotFoundError("Comprehension session", session_id)
    language = _resolve_language(body.language, session.language)
    if session.status != "in_progress":
        raise ValidationError(
            "Эта проверка уже завершена. Начните новую."
            if language != "en"
            else "This check is already finished. Start a new one."
        )
    try:
        await comprehension.submit_answer(
            db,
            session=session,
            topic=topic,
            answer=body.text,
            language=language,
        )
    except content.ContentGenerationError as exc:
        raise ValidationError(
            "Не удалось разобрать ответ. Попробуйте ещё раз."
            if language != "en"
            else "Could not analyse the answer. Please try again."
        ) from exc
    await db.commit()
    return ComprehensionTurnResponse(**comprehension.turn_payload(session))


@router.get(
    "/learning/topics/{topic_id}/comprehension/latest",
    response_model=ComprehensionLatestResponse,
)
async def latest_comprehension(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The latest check for this topic, so a reload can show its result."""
    topic = await _get_topic(db, topic_id, user.id)
    explanation = await topic_flow.saved_explanation(db, topic.id, user.id)
    session = await comprehension.latest_session(db, topic_id=topic.id, user_id=user.id)
    return ComprehensionLatestResponse(
        **comprehension.latest_payload(
            session, has_explanation=bool(explanation.strip())
        )
    )


# ── Topic notes ──
#
# A note is a summary of the explanation the learner already read, per goal topic.
# The course's own note block writes from chapter text instead, which put notes
# about the wrong subject whenever the course's material was not what the learner
# was actually studying.


@router.post("/learning/goals/{goal_id}/topics/explain-missing")
async def explain_missing_topics(
    goal_id: uuid.UUID,
    background: BackgroundTasks,
    body: ExplainRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-7.1 for every topic of the goal that has none yet.

    A note and a quiz are both refused without an explanation, so a goal whose
    topics have none is a goal the learner cannot work on at all. Explaining them
    one at a time is thirty round trips; this queues them instead and returns
    immediately, because one explanation takes seconds and a goal can have a
    dozen topics.

    Topics that already have one are left alone unless ``regenerate`` is set — the
    learner has read those, and silently replacing an explanation they read would
    make every note and card built from it describe something they never saw.
    """
    _require_llm()
    await _get_goal(db, goal_id, user.id)

    style = (body.style if body else None) or "detailed"
    regenerate = bool(body.regenerate) if body else False
    language = (body.language if body else None) or ""
    language = language.strip().lower()
    if language not in ("ru", "en"):
        language = await _profile_language(db, user.id)

    result = await db.execute(
        select(LearningTopic)
        .where(LearningTopic.goal_id == goal_id)
        .order_by(LearningTopic.order_index, LearningTopic.created_at)
    )
    topics = list(result.scalars().all())
    explained = await _topics_with_explanations(db, user.id, topics)
    pending = topics if regenerate else [t for t in topics if t.id not in explained]

    if not pending:
        return {
            "queued": 0,
            "already_explained": len(topics),
            "message": "У всех тем уже есть объяснение",
        }

    payload = [
        {
            "topic_id": str(t.id),
            "title": t.title,
            "description": t.description or "",
            # The row itself travels so the worker can resolve the topic's own
            # pinned material without re-querying and re-authorising.
            "source_node_id": str(t.source_node_id) if t.source_node_id else None,
            # Carried per entry so the background worker writes every explanation
            # in the language the learner picked when queueing the run.
            "language": language,
        }
        for t in pending
    ]
    background.add_task(_explain_missing_worker, goal_id, user.id, style, payload)
    logger.info(
        "Goal %s: queued %d explanation(s), style=%s", goal_id, len(payload), style
    )
    return {
        "queued": len(payload),
        "already_explained": len(topics) - len(pending),
        "message": f"Объяснения готовятся для {len(payload)} тем",
    }


async def _explain_missing_worker(
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    style: str,
    payload: list[dict],
) -> None:
    """Write an explanation for each queued topic, one session for all of them.

    Sequential on purpose: one topic's failure must not take the rest down, and a
    goal rarely has so many topics that serial calls cost more than the wait.
    """
    from database import async_session

    from services.adaptive_learning import topic_material as topic_material_service

    written = failed = 0
    async with async_session() as db:
        for entry in payload:
            topic_id = uuid.UUID(entry["topic_id"])
            try:
                # Same grounding as the single-topic endpoint: the learner's own
                # literature first, the one-line description only as a fallback.
                topic_row = await db.execute(
                    select(LearningTopic).where(
                        LearningTopic.id == topic_id,
                        LearningTopic.goal_id == goal_id,
                    )
                )
                topic = topic_row.scalar_one_or_none()
                if topic is None:
                    raise ValueError("topic vanished before its explanation ran")
                passages, _label = await _grounding_for_topic(db, user_id, topic)
                source_text, label = (
                    topic_material_service.as_grounding_text(passages)
                    if passages
                    else ((entry["description"] or ""), "")
                )
                text = await content.explain_topic(
                    topic_title=entry["title"],
                    style=style,
                    source_text=source_text,
                    source_label=label,
                    learner_language=entry.get("language") or "ru",
                )
                if not (text or "").strip():
                    raise ValueError("empty explanation")
                db.add(
                    GeneratedAsset(
                        user_id=user_id,
                        asset_type="explanation",
                        title=f"Explanation: {entry['title']}",
                        content={"text": text},
                        metadata_={
                            "topic_id": entry["topic_id"],
                            "style": style,
                            "source_node_ids": [p["node_id"] for p in passages],
                            "source_label": label,
                        },
                    )
                )
                await db.commit()
                written += 1
            except (ConnectionError, TimeoutError, ValueError, RuntimeError) as exc:
                await db.rollback()
                failed += 1
                logger.warning(
                    "Explanation failed for topic %s (%s): %s",
                    entry["topic_id"],
                    goal_id,
                    exc,
                )
            except SQLAlchemyError as exc:
                await db.rollback()
                failed += 1
                logger.warning(
                    "Explanation storage failed for topic %s (%s): %s",
                    entry["topic_id"],
                    goal_id,
                    exc,
                )
    logger.info(
        "Goal %s: explanations finished — %d written, %d failed",
        goal_id,
        written,
        failed,
    )


@router.get(
    "/learning/notes",
    response_model=TopicNoteIndexResponse,
    summary="List topics that have a note",
    description="Return the learner's topics that already have an explanation, with their note when one was written. Pass course_id or goal_id to narrow it.",
)
async def list_topic_notes(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    course_id: uuid.UUID | None = None,
    goal_id: uuid.UUID | None = None,
):
    """Topics with something to summarise.

    A topic with no explanation is absent rather than listed empty: it has no note
    yet, which is a different thing from a note that failed.
    """
    if goal_id is not None:
        await _get_goal(db, goal_id, user.id)
    entries = await topic_notes.topic_notes_index(
        db, user.id, course_id=course_id, goal_id=goal_id
    )
    return TopicNoteIndexResponse(topics=entries)


@router.post(
    "/learning/topics/{topic_id}/notes",
    response_model=TopicNoteResponse,
    summary="Write or fetch a topic note",
    description="Summarise a topic's saved explanation into a note. Returns the saved note without calling the model when one exists.",
)
async def write_topic_note(
    topic_id: uuid.UUID,
    body: TopicNoteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Summarise the explanation into a note.

    Refuses a topic with no explanation: the alternative is a summary of its
    title, which is the complaint this route exists to end.
    """
    _require_llm()
    topic = await _get_topic(db, topic_id, user.id)
    # course_id is left to the service: it resolves the goal with an explicit
    # await. Touching topic.goal here would lazy-load outside greenlet context.
    try:
        markdown, cached, source = await topic_notes.build_topic_note(
            db,
            topic,
            user.id,
            note_format=body.note_format,
        )
    except topic_notes.NoteUnavailable as exc:
        # The note holds the explanation and nothing else, so with no explanation
        # there is nothing to write. Substituting the title would put material in
        # the note that the learner never read.
        raise ValidationError(
            "У темы пока нет объяснения — конспект составляется только из него"
        ) from exc
    return TopicNoteResponse(
        topic_id=str(topic_id),
        topic_title=topic.title,
        goal_id=str(topic.goal_id),
        markdown=markdown,
        cached=cached,
        written_from=source,
    )


# ──────────────────────────────────────────────────────────────
# Diagnostic — TZ /goals/{id}/diagnostic (U2, FR-4)
# ──────────────────────────────────────────────────────────────


def _build_diagnostic_session(
    topics: list[LearningTopic],
    max_replies: int,
) -> DiagnosticSession:
    return DiagnosticSession(
        [{"id": str(t.id), "title": t.title} for t in topics],
        max_replies=max_replies,
    )


@router.post(
    "/learning/goals/{goal_id}/diagnostic/start", response_model=DiagnosticTurnResponse
)
async def start_diagnostic(
    goal_id: uuid.UUID,
    body: DiagnosticStartRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """U2: open the hidden diagnostic and return the first probing turn.

    FR-4.1: the reply is checked against the banned level questions before it is
    returned, so the guarantee does not rely on the prompt alone.
    """
    _require_llm()
    goal = await _get_goal(db, goal_id, user.id)

    query = select(LearningTopic).where(LearningTopic.goal_id == goal_id)
    if body.subtopic_ids:
        query = query.where(LearningTopic.id.in_(body.subtopic_ids))
    result = await db.execute(query.order_by(LearningTopic.order_index))
    topics = list(result.scalars().all())
    if not topics:
        raise ValidationError("Add subtopics before running the diagnostic")

    session = _build_diagnostic_session(topics, body.max_replies)
    turn = session.next_turn()
    if turn is None:
        raise ValidationError("The diagnostic finished immediately; nothing to ask")

    payload = await content.generate_diagnostic_question(
        goal_topic=goal.topic,
        subtopic_title=turn.subtopic_title,
        subtopic_description="",
        target_difficulty=turn.target_difficulty,
    )
    content.assert_no_level_question(payload["question"])

    record = DiagnosticSessionRecord(
        goal_id=goal_id,
        user_id=user.id,
        turns=[
            {
                "turn_index": 0,
                "subtopic_id": turn.subtopic_id,
                "subtopic_title": turn.subtopic_title,
                "question": payload["question"],
                "difficulty": payload.get("difficulty", turn.target_difficulty),
                "technique": payload.get("technique"),
                "criteria": payload.get("criteria", {}),
            }
        ],
        reply_count=1,
        status="in_progress",
        # Persist the estimator state so the dialogue resumes exactly instead of
        # being replayed (see DiagnosticSession.snapshot).
        metadata_json={"estimator": session.snapshot()},
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return DiagnosticTurnResponse(
        session_id=str(record.id),
        turn_index=0,
        subtopic_id=turn.subtopic_id,
        subtopic_title=turn.subtopic_title,
        question=payload["question"],
        technique=payload.get("technique"),
        difficulty=payload.get("difficulty", turn.target_difficulty),
        done=False,
    )


@router.post(
    "/learning/goals/{goal_id}/diagnostic/{session_id}/reply",
    response_model=DiagnosticTurnResponse,
)
async def reply_to_diagnostic(
    goal_id: uuid.UUID,
    session_id: uuid.UUID,
    body: DiagnosticReplyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Grade the reply, update the estimate, return the next turn or finish."""
    _require_llm()
    await _get_goal(db, goal_id, user.id)

    record = await db.execute(
        select(DiagnosticSessionRecord).where(
            DiagnosticSessionRecord.id == session_id,
            DiagnosticSessionRecord.goal_id == goal_id,
            DiagnosticSessionRecord.user_id == user.id,
        )
    )
    session_row = record.scalar_one_or_none()
    if not session_row:
        raise NotFoundError("Diagnostic session", session_id)

    turns = list(session_row.turns or [])
    last_turn = turns[-1]
    subtopic_id = last_turn.get("subtopic_id")

    graded = await content.grade_answer(
        question=last_turn.get("question", ""),
        criteria=last_turn.get("criteria") or {},
        answer=body.text,
    )
    # Section 6.2: hint penalty is deterministic, so it is applied here.
    score = answer_scoring.score_answer(
        graded["breakdown"],
        hints_used=0,
        feedback=graded.get("feedback", ""),
        reported_errors=graded.get("error_types"),
    )

    topics = list(
        (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id == goal_id)
            )
        )
        .scalars()
        .all()
    )
    # Restore the estimator from the stored snapshot. Replaying the transcript
    # would drift, because the RNG jitter is not part of the transcript.
    snapshot = (session_row.metadata_json or {}).get("estimator")
    session = DiagnosticSession.restore(
        [{"id": str(t.id), "title": t.title} for t in topics], snapshot
    )

    session.record_answer(subtopic_id, score.final_score)
    turns.append(
        {
            "turn_index": len(turns),
            "subtopic_id": subtopic_id,
            "answer": body.text,
            "score": score.final_score,
            "feedback": score.feedback,
        }
    )

    next_turn = session.next_turn()
    done = next_turn is None

    if next_turn is not None:
        payload = await content.generate_diagnostic_question(
            goal_topic=(await _get_goal(db, goal_id, user.id)).topic,
            subtopic_title=next_turn.subtopic_title,
            subtopic_description="",
            target_difficulty=next_turn.target_difficulty,
            previous_turns=[t.get("question", "") for t in turns if t.get("question")],
        )
        content.assert_no_level_question(payload["question"])
        turns.append(
            {
                "turn_index": next_turn.turn_index,
                "subtopic_id": next_turn.subtopic_id,
                "subtopic_title": next_turn.subtopic_title,
                "question": payload["question"],
                "difficulty": payload.get("difficulty", next_turn.target_difficulty),
                "technique": payload.get("technique"),
                "criteria": payload.get("criteria", {}),
            }
        )

    summary = session.finish("completed") if done else session.summary()
    session_row.turns = turns
    session_row.reply_count = len([t for t in turns if t.get("question")])
    session_row.metadata_json = {"estimator": session.snapshot()}
    if done:
        session_row.status = "completed"
        session_row.finished_reason = summary.finished_reason
        session_row.final_estimates = {e["id"]: e for e in summary.estimates}
        session_row.completed_at = datetime.now(timezone.utc)
        # FR-4.4: the estimates have to leave the session row, or the planner
        # keeps reading a clean slate for every topic no matter what the learner
        # just demonstrated.
        await _apply_estimates_to_topics(db, goal_id, user.id, summary.estimates)
    await db.commit()

    if done:
        return DiagnosticTurnResponse(
            session_id=str(session_row.id),
            turn_index=len(turns),
            subtopic_id=subtopic_id,
            subtopic_title="",
            question="",
            technique=None,
            difficulty=0.0,
            done=True,
        )

    return DiagnosticTurnResponse(
        session_id=str(session_row.id),
        turn_index=next_turn.turn_index,
        subtopic_id=next_turn.subtopic_id,
        subtopic_title=next_turn.subtopic_title,
        question=payload["question"],
        technique=payload.get("technique"),
        difficulty=payload.get("difficulty", next_turn.target_difficulty),
        done=False,
    )


@router.get(
    "/learning/goals/{goal_id}/diagnostic/{session_id}",
    response_model=DiagnosticStateResponse,
)
async def get_diagnostic_state(
    goal_id: uuid.UUID,
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-4.5: show the learner what was inferred about their level."""
    row = await db.execute(
        select(DiagnosticSessionRecord).where(
            DiagnosticSessionRecord.id == session_id,
            DiagnosticSessionRecord.goal_id == goal_id,
            DiagnosticSessionRecord.user_id == user.id,
        )
    )
    record = row.scalar_one_or_none()
    if not record:
        raise NotFoundError("Diagnostic session", session_id)

    estimates = list((record.final_estimates or {}).values())
    return DiagnosticStateResponse(
        session_id=str(record.id),
        status=record.status,
        reply_count=record.reply_count,
        finished_reason=record.finished_reason,
        estimates=estimates,
        aggregate_uncertainty=round(
            sum(float(e.get("uncertainty", 1.0)) for e in estimates) / len(estimates), 3
        )
        if estimates
        else 1.0,
    )


@router.patch(
    "/learning/goals/{goal_id}/diagnostic/{session_id}",
    response_model=DiagnosticStateResponse,
)
async def correct_diagnostic(
    goal_id: uuid.UUID,
    session_id: uuid.UUID,
    body: CorrectionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-4.5: the learner corrects the inferred level themselves."""
    row = await db.execute(
        select(DiagnosticSessionRecord).where(
            DiagnosticSessionRecord.id == session_id,
            DiagnosticSessionRecord.goal_id == goal_id,
            DiagnosticSessionRecord.user_id == user.id,
        )
    )
    record = row.scalar_one_or_none()
    if not record:
        raise NotFoundError("Diagnostic session", session_id)

    from services.adaptive_learning.diagnostic import describe_band

    estimates = dict(record.final_estimates or {})
    for topic_id, value in body.estimates.items():
        current = dict(estimates.get(topic_id) or {})
        current.update(
            {
                "estimate": max(0.0, min(1.0, float(value))),
                "band": describe_band(float(value)),
            }
        )
        estimates[topic_id] = current

    record.final_estimates = estimates

    # FR-5.6 / FR-6.2: a manual correction feeds straight into planning, and it
    # always wins — the learner is stating the level, not the model guessing it.
    # Written through the same helper the dialogue uses so the two paths cannot
    # disagree about what a strength means.
    written = await _apply_estimates_to_topics(
        db,
        goal_id,
        user.id,
        [
            {"id": topic_id, "estimate": (entry or {}).get("estimate")}
            for topic_id, entry in estimates.items()
        ],
        force=True,
    )
    await db.commit()
    logger.info("Goal %s: learner corrected %d topic estimate(s)", goal_id, written)

    values = list(estimates.values())
    return DiagnosticStateResponse(
        session_id=str(record.id),
        status=record.status,
        reply_count=record.reply_count,
        finished_reason=record.finished_reason,
        estimates=values,
        aggregate_uncertainty=round(
            sum(float(v.get("uncertainty", 0.0)) for v in values) / len(values), 3
        )
        if values
        else 0.0,
    )


# ──────────────────────────────────────────────────────────────
# Guided daily session — "open it and start"
# ──────────────────────────────────────────────────────────────


@router.post(
    "/learning/goals/{goal_id}/daily/prepare",
    response_model=DailySessionResponse,
)
async def prepare_daily_session(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build today's review -> theory -> practice session.

    The endpoint is a POST because preparing a session may move overdue plan
    items to today. It is idempotent for a given day: once the gap is closed,
    calling it again returns the same session. No model call is made here.
    """
    goal = await _get_goal(db, goal_id, user.id)
    payload = await daily.prepare_session(db, goal, user.id)
    return DailySessionResponse(**payload)


@router.post(
    "/learning/daily/prepare",
    response_model=DailyAgendaResponse,
)
async def prepare_daily_agenda(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build today's session for every goal that has work in the calendar.

    This is the multi-goal entry point for "Сегодня". The explicitly selected
    goal is put first so the daily mode keeps its familiar starting point, but
    the response still contains every other in-progress goal with a block today.
    """
    payload = await daily.prepare_agenda(db, user.id)
    active_id = await _active_goal_id(db, user.id)
    if active_id is not None:
        active_key = str(active_id)
        payload["sessions"].sort(key=lambda session: session["goal_id"] != active_key)
    payload["active_goal_id"] = str(active_id) if active_id else None
    return DailyAgendaResponse(**payload)


@router.post(
    "/learning/goals/{goal_id}/daily/complete",
    response_model=DailyCompleteResponse,
)
async def complete_daily_session(
    goal_id: uuid.UUID,
    body: DailyCompleteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the finished topics of today's guided session as done.

    Topics the learner did not pass are deliberately left pending. The next
    preparation will carry them forward, which is how "tomorrow we do this
    again, and the plan moves" happens.
    """
    goal = await _get_goal(db, goal_id, user.id)
    day = body.day or datetime.now(timezone.utc).date()

    topic_ids = set(body.topic_ids)
    item_ids = set(body.item_ids)
    if not topic_ids and not item_ids:
        return DailyCompleteResponse(
            goal_id=str(goal.id), date=day.isoformat(), completed_items=0
        )

    owned: set[uuid.UUID] = set()
    if topic_ids:
        owned = {
            row[0]
            for row in (
                await db.execute(
                    select(LearningTopic.id).where(
                        LearningTopic.goal_id == goal.id,
                        LearningTopic.id.in_(topic_ids),
                    )
                )
            ).all()
        }

    # A practice answer can replan the goal between prepare and complete. Only
    # items still scheduled for this day are marked: if the replan moved a weak
    # topic later, leaving it pending is the repeat-first behaviour we want.
    #
    # Task-only days have no topic id, so they are named by plan-item id. Both
    # sets are filtered by the same day and ownership checks.
    conditions = []
    if owned:
        conditions.append(LearningPlanItem.topic_id.in_(owned))
    if item_ids:
        conditions.append(LearningPlanItem.id.in_(item_ids))
    if not conditions:
        return DailyCompleteResponse(
            goal_id=str(goal.id), date=day.isoformat(), completed_items=0
        )

    rows = list(
        (
            await db.execute(
                select(LearningPlanItem).where(
                    LearningPlanItem.goal_id == goal.id,
                    LearningPlanItem.day == day,
                    LearningPlanItem.status.in_(("pending", "missed")),
                    LearningPlanItem.rejected.is_(False),
                    or_(*conditions),
                )
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        item.status = "done"
        if item.actual_minutes is None:
            item.actual_minutes = item.planned_minutes

    await db.commit()
    return DailyCompleteResponse(
        goal_id=str(goal.id), date=day.isoformat(), completed_items=len(rows)
    )


@router.post(
    "/learning/goals/{goal_id}/daily/pull",
    response_model=DailyPullResponse,
)
async def pull_daily_day(
    goal_id: uuid.UUID,
    body: DailyPullRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take a future day's lesson now.

    The original day keeps a shortened review at 40% of its planned time, so
    working ahead does not leave an empty tomorrow. Today's own pending work
    must be finished first; that is what prevents an accidental backlog from
    being buried under a new lesson.
    """
    goal = await _get_goal(db, goal_id, user.id)
    try:
        payload = await daily.pull_day(
            db,
            goal,
            user.id,
            target_day=body.day if body else None,
        )
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    return DailyPullResponse(**payload)


# ──────────────────────────────────────────────────────────────
# Plan — TZ /goals/{id}/plan (U3, U7)
# ──────────────────────────────────────────────────────────────


@router.get("/learning/goals/{goal_id}/plan", response_model=PlanResponse)
async def get_plan(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """U3: the day-by-day plan plus the graph of topics."""
    goal = await _get_goal(db, goal_id, user.id)

    rows = await db.execute(
        select(LearningPlanItem)
        .where(LearningPlanItem.goal_id == goal_id)
        .order_by(LearningPlanItem.day)
    )
    items = list(rows.scalars().all())

    titles = {
        str(t.id): t.title
        for t in (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id == goal_id)
            )
        )
        .scalars()
        .all()
    }

    days: dict[str, list[PlanItemResponse]] = {}
    total = 0
    for item in items:
        total += item.planned_minutes
        days.setdefault(item.day.isoformat(), []).append(
            PlanItemResponse(
                id=str(item.id),
                topic_id=str(item.topic_id) if item.topic_id else None,
                title=_plan_item_title(item.item_type, item.topic_id, titles),
                item_type=item.item_type,
                day=item.day.isoformat(),
                planned_minutes=item.planned_minutes,
                actual_minutes=item.actual_minutes,
                status=item.status,
                reason=item.reason,
                rejected=item.rejected,
            )
        )

    days_left = (goal.deadline - date.today()).days if goal.deadline else 30
    return PlanResponse(
        goal_id=str(goal_id),
        days=days,
        total_minutes=total,
        available_minutes=max(0, days_left) * goal.minutes_per_day,
        feasible=True,
        speed=1.0,
    )


@router.post("/learning/goals/{goal_id}/plan", response_model=PlanResponse)
async def build_plan_endpoint(
    goal_id: uuid.UUID,
    body: ReplanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """U7: replan. Runs entirely on the deterministic scheduler, no LLM needed."""
    goal = await _get_goal(db, goal_id, user.id)

    topics = await load_topic_inputs(db, goal_id, user.id)
    if not topics:
        raise ValidationError("Add subtopics before building a plan")

    days = body.days
    if not days:
        days = (goal.deadline - date.today()).days if goal.deadline else 30
    days = max(1, days)

    minutes = body.minutes_per_day or goal.minutes_per_day
    speed = body.speed if body.speed is not None else await compute_speed(db, goal_id)
    mode = body.mode

    # FR-10.2/10.4: name the trigger so the learner is told *why* the plan moved.
    # A budget or speed change is "settings"; otherwise this is a plain replan.
    trigger = "replan"
    context = ""
    if body.minutes_per_day and body.minutes_per_day != goal.minutes_per_day:
        trigger = "settings"
        context = f"бюджет {goal.minutes_per_day} → {body.minutes_per_day} мин/день"
    elif body.speed is not None and abs(speed - 1.0) > 0.05:
        trigger = "settings"
        context = f"коэффициент скорости {speed:.2f}"
    elif mode and mode not in ("course_following", "self_paced"):
        trigger = "settings"
        context = f"режим {mode}"

    result = await compute_and_store_plan(
        db,
        goal_id,
        user.id,
        days=days,
        minutes_per_day=minutes,
        speed=speed,
        trigger=trigger,
        context=context,
        mode=mode,
    )

    stored = list(
        (
            await db.execute(
                select(LearningPlanItem)
                .where(LearningPlanItem.goal_id == goal_id)
                .order_by(LearningPlanItem.day)
            )
        )
        .scalars()
        .all()
    )
    titles = {
        str(t.id): t.title
        for t in (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id == goal_id)
            )
        )
        .scalars()
        .all()
    }

    by_id = {str(item.id): item for item in stored}
    days_map: dict[str, list[PlanItemResponse]] = {}
    for planned in result.items:
        row = next(
            (
                r
                for r in stored
                if r.day == planned.day
                and _same_plan_topic(r.topic_id, planned.topic_id)
                and r.item_type == planned.item_type
            ),
            None,
        )
        days_map.setdefault(planned.day.isoformat(), []).append(
            PlanItemResponse(
                id=str(row.id) if row else "",
                topic_id=planned.topic_id,
                title=_plan_item_title(
                    planned.item_type, planned.topic_id, titles
                ),
                item_type=planned.item_type,
                day=planned.day.isoformat(),
                planned_minutes=planned.planned_minutes,
                actual_minutes=row.actual_minutes if row else None,
                status=row.status if row else "pending",
                reason=planned.reason,
                rejected=False,
            )
        )

    return PlanResponse(
        goal_id=str(goal_id),
        days=days_map,
        total_minutes=result.total_minutes,
        available_minutes=result.available_minutes,
        feasible=result.feasible,
        speed=result.speed,
        warnings=result.warnings,
        suggestions=result.suggestions,
    )


@router.patch(
    "/learning/goals/{goal_id}/plan/{item_id}", response_model=PlanItemResponse
)
async def update_plan_item(
    goal_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PlanItemPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-6.6 / FR-10.4: manual plan edits, including rejecting a replan change.

    Rejecting an item marks it, so the next replan leaves it alone instead of
    silently re-applying the change the learner declined.
    """
    row = await db.execute(
        select(LearningPlanItem)
        .join(LearningGoal, LearningGoal.id == LearningPlanItem.goal_id)
        .where(LearningPlanItem.id == item_id, LearningGoal.user_id == user.id)
    )
    item = row.scalar_one_or_none()
    if not item:
        raise NotFoundError("Plan item", item_id)

    payload = body.model_dump(exclude_unset=True)
    for key in ("status", "rejected", "day", "planned_minutes"):
        if key in payload and payload[key] is not None:
            setattr(item, key, payload[key])

    # FR-7.4: recording actual time feeds the speed coefficient.
    if item.status == "done" and item.actual_minutes is None:
        item.actual_minutes = item.planned_minutes

    await db.commit()
    await db.refresh(item)

    titles: dict[str, str] = {}
    if item.topic_id is not None:
        topic = (
            await db.execute(
                select(LearningTopic).where(LearningTopic.id == item.topic_id)
            )
        ).scalar_one_or_none()
        if topic is not None:
            titles[str(topic.id)] = topic.title
    title = _plan_item_title(item.item_type, item.topic_id, titles)
    return PlanItemResponse(
        id=str(item.id),
        topic_id=str(item.topic_id) if item.topic_id else None,
        title=title,
        item_type=item.item_type,
        day=item.day.isoformat(),
        planned_minutes=item.planned_minutes,
        actual_minutes=item.actual_minutes,
        status=item.status,
        reason=item.reason,
        rejected=item.rejected,
    )


@router.post(
    "/learning/goals/{goal_id}/plan/{item_id}/pull-ahead",
    response_model=PullAheadResponse,
)
async def pull_ahead_plan_item_endpoint(
    goal_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PullAheadRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Взять одно задание с будущего дня на сегодня (или на указанный день).

    The original stays on its day at half the time (never below 5 minutes), so
    tomorrow is a short consolidation of what was already done, not a repeat of
    the full lesson. The copy on the target day keeps the full time. Practice
    (control) assignments can be taken this way; the final exam cannot.
    """
    await _get_goal(db, goal_id, user.id)
    try:
        pulled, original = await plan_ops.pull_ahead_plan_item(
            db,
            goal_id,
            user.id,
            item_id,
            target_day=body.target_day if body else None,
        )
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc

    topic_ids = {item.topic_id for item in (pulled, original) if item.topic_id}
    titles: dict[str, str] = {}
    if topic_ids:
        rows = await db.execute(
            select(LearningTopic.id, LearningTopic.title).where(
                LearningTopic.id.in_(topic_ids)
            )
        )
        titles = {str(topic_id): title for topic_id, title in rows.all()}

    def _response(item: LearningPlanItem) -> PlanItemResponse:
        return PlanItemResponse(
            id=str(item.id),
            topic_id=str(item.topic_id) if item.topic_id else None,
            title=_plan_item_title(item.item_type, item.topic_id, titles),
            item_type=item.item_type,
            day=item.day.isoformat(),
            planned_minutes=item.planned_minutes,
            actual_minutes=item.actual_minutes,
            status=item.status,
            reason=item.reason,
            rejected=item.rejected,
        )

    return PullAheadResponse(pulled=_response(pulled), original=_response(original))


# ──────────────────────────────────────────────────────────────
# Questions and answers — TZ /questions/{id}/answer (U5)
# ──────────────────────────────────────────────────────────────


@router.post(
    "/learning/topics/{topic_id}/question",
    response_model=QuestionResponse,
    status_code=201,
)
async def generate_question(
    topic_id: uuid.UUID,
    body: GenerateTaskRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-8.1-8.3: a task with criteria fixed now and three hints."""
    _require_llm()
    topic = await _get_topic(db, topic_id, user.id)

    state = await db.execute(
        select(TopicState).where(
            TopicState.topic_id == topic_id, TopicState.user_id == user.id
        )
    )
    stored = state.scalar_one_or_none()
    difficulty = (
        body.difficulty
        if body.difficulty is not None
        else (stored.strength if stored else 0.5)
    )

    recent = await db.execute(
        select(LearningQuestion.question_type)
        .where(LearningQuestion.topic_id == topic_id)
        .order_by(LearningQuestion.created_at.desc())
        .limit(3)
    )
    avoid = [t for (t,) in recent.all()]

    # TZ section 9: steer the task towards this learner's actual weak spots.
    # The course hangs off the goal, not the topic.
    parent_course = await db.scalar(
        select(LearningGoal.course_id).where(LearningGoal.id == topic.goal_id)
    )
    ctx = await learner_context.build(db, user.id, course_id=parent_course)

    # The learner's interface language, so a task generated for an English reader
    # is written in English rather than in the goal's original language.
    task_language = (body.language or "").strip().lower()
    if task_language not in ("ru", "en"):
        task_language = await _profile_language(db, user.id)

    try:
        payload = await content.generate_task(
            topic_title=topic.title,
            difficulty=difficulty,
            avoid_types=avoid,
            language=task_language,
            prior_context=ctx.as_prompt_text(),
            format_hint=body.format,
        )
    except content.ContentGenerationError as exc:
        raise ValidationError(
            "Не удалось подготовить задание. Попробуйте ещё раз."
        ) from exc

    # An explicitly requested code task that came back without checks cannot be
    # shown as "working or not", which is the point of asking for code. Refuse it
    # rather than silently handing the learner a text task.
    if body.format == task_formats.FORMAT_CODE:
        generated_tests = (payload.get("payload") or {}).get("tests") or []
        if payload.get("format") != task_formats.FORMAT_CODE or not generated_tests:
            raise ValidationError(
                "Не удалось подготовить задачу с проверками кода. Попробуйте ещё раз."
            )

    question = LearningQuestion(
        topic_id=topic_id,
        question_type=payload["question_type"],
        text=payload["text"],
        criteria=payload["criteria"],
        difficulty=payload["difficulty"],
        hints=payload["hints"],
        # .get rather than [..]: a generator that predates formats, or a substitute
        # one, must still yield a task the learner can answer.
        format=payload.get("format") or task_formats.DEFAULT_FORMAT,
        payload=payload.get("payload") or {},
    )
    db.add(question)
    await db.commit()
    await db.refresh(question)

    return QuestionResponse(
        id=str(question.id),
        topic_id=str(topic_id),
        topic_title=topic.title,
        question_type=question.question_type,
        text=question.text,
        criteria=question.criteria,
        hints=question.hints or [],
        difficulty=question.difficulty,
        format=getattr(question, "format", "text") or "text",
        payload=_public_payload(question),
    )


def _public_payload(question: LearningQuestion) -> dict:
    """What the client is given to render the task, with the answer removed.

    A task that can be answered by looking at the network response is not a task.
    So the correct option, the expected number, the reference order, the pairing
    and the reference solution are all dropped here — the learner gets the
    question and the material to answer it with, never the key.
    """
    fmt = getattr(question, "format", "text") or "text"
    payload = getattr(question, "payload", None)
    if not isinstance(payload, dict):
        return {}

    if fmt == task_formats.FORMAT_NUMBER:
        return {
            "unit": payload.get("unit", ""),
            "hint_text": payload.get("hint_text", ""),
            "tolerance": payload.get("tolerance"),
        }
    if fmt == task_formats.FORMAT_SINGLE_CHOICE:
        return {"options": payload.get("options", [])}
    if fmt == task_formats.FORMAT_MULTI_CHOICE:
        return {"options": payload.get("options", [])}
    if fmt == task_formats.FORMAT_TRUE_FALSE:
        return {"statement": payload.get("statement", "")}
    if fmt == task_formats.FORMAT_ORDERING:
        # `items` is the scrambled order as shown. `solution` is the key.
        return {"items": payload.get("items", [])}
    if fmt == task_formats.FORMAT_MATCHING:
        # The right column is shuffled in the stored payload too, so the client can
        # show it in an order that does not give the answer away.
        return {
            "left": payload.get("left", []),
            "right": payload.get("right", []),
        }
    if fmt == task_formats.FORMAT_CODE:
        # `expected` is included deliberately. It is the specification of what the
        # function must return — the same thing the task text spells out — and not
        # the answer to the task, which is the code. Without it the learner cannot
        # see what a failing check means, and the browser cannot compare anything.
        # The reference solution is still withheld: that is the answer.
        return {
            "language": payload.get("language", "python"),
            "starter_code": payload.get("starter_code", ""),
            "tests": [
                {
                    "name": t.get("name", ""),
                    "call": t.get("call", ""),
                    "expected": t.get("expected", ""),
                }
                for t in (payload.get("tests") or [])
                if isinstance(t, dict)
            ],
        }
    return {}


def question_to_response(
    question: LearningQuestion, topic_title: str | None = None
) -> QuestionResponse:
    """Serialise a question. Shared so the drill endpoint matches the single one."""
    return QuestionResponse(
        id=str(question.id),
        topic_id=str(question.topic_id),
        topic_title=topic_title,
        question_type=question.question_type,
        text=question.text,
        criteria=question.criteria,
        hints=question.hints or [],
        difficulty=question.difficulty,
        format=getattr(question, "format", "text") or "text",
        payload=_public_payload(question),
    )


@router.get("/learning/questions/{question_id}", response_model=QuestionResponse)
async def get_question(
    question_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        select(LearningQuestion, LearningTopic)
        .join(LearningTopic, LearningTopic.id == LearningQuestion.topic_id)
        .join(LearningGoal, LearningGoal.id == LearningTopic.goal_id)
        .where(LearningQuestion.id == question_id, LearningGoal.user_id == user.id)
    )
    found = row.first()
    if not found:
        raise NotFoundError("Question", question_id)
    question, topic = found
    return QuestionResponse(
        id=str(question.id),
        topic_id=str(question.topic_id),
        topic_title=topic.title,
        question_type=question.question_type,
        text=question.text,
        criteria=question.criteria,
        hints=question.hints or [],
        difficulty=question.difficulty,
        source_label=question.source_label,
        format=getattr(question, "format", "text") or "text",
        payload=_public_payload(question),
    )


@router.get(
    "/learning/questions/{question_id}/hints/{level}", response_model=HintResponse
)
async def get_hint(
    question_id: uuid.UUID,
    level: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-8.6: three escalating levels. Requesting one costs grade points."""
    if level not in (1, 2, 3):
        raise ValidationError("Hint level must be 1, 2 or 3")

    row = await db.execute(
        select(LearningQuestion, LearningTopic, LearningGoal)
        .join(LearningTopic, LearningTopic.id == LearningQuestion.topic_id)
        .join(LearningGoal, LearningGoal.id == LearningTopic.goal_id)
        .where(LearningQuestion.id == question_id, LearningGoal.user_id == user.id)
    )
    found = row.first()
    if not found:
        raise NotFoundError("Question", question_id)
    question = found[0]
    hints = question.hints or []
    if level > len(hints):
        raise ValidationError("This question has no hint at that level")
    return HintResponse(level=level, hint=str(hints[level - 1]))


@router.post(
    "/learning/questions/{question_id}/answer",
    response_model=AnswerResponse,
    status_code=201,
)
async def submit_answer(
    question_id: uuid.UUID,
    body: SubmitAnswerRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """U5: grade, then apply the repetition rules deterministically.

    Section 6.2: the model produces the criterion breakdown, then
    ``final = score * (1 - hint_penalty)`` is computed in code. Section 6.3 then
    derives the next review date. The model never touches either formula.

    Which model call happens at all depends on the task's format. A chosen option,
    a number, an ordering and a set of pairs are decided by comparing values, so
    those tasks are graded without a model — they work with no provider configured,
    cost nothing, and give the same verdict every time they are re-run. Only prose
    and code need judgement, and code only for what running it cannot show.
    """
    row = await db.execute(
        select(LearningQuestion, LearningTopic, LearningGoal)
        .join(LearningTopic, LearningTopic.id == LearningQuestion.topic_id)
        .join(LearningGoal, LearningGoal.id == LearningTopic.goal_id)
        .where(LearningQuestion.id == question_id, LearningGoal.user_id == user.id)
    )
    found = row.first()
    if not found:
        raise NotFoundError("Question", question_id)
    question, topic, goal = found

    question_format = getattr(question, "format", "text") or "text"
    stored_payload = getattr(question, "payload", None) or {}

    breakdown: dict = {key: 0.0 for key in answer_scoring.CRITERIA}
    feedback: str = ""
    error_types: list[str] | None = None
    expected_answer: str | None = None
    checked_without_model = False

    if question_format == task_formats.FORMAT_CODE:
        answer_language = (body.language or "").strip().lower()
        if answer_language not in ("ru", "en"):
            answer_language = await _profile_language(db, user.id)
        verdict = await _grade_code_answer(
            question, body, answer_language=answer_language
        )
        breakdown = verdict["breakdown"]
        feedback = verdict["feedback"]
        error_types = verdict["error_types"]
        expected_answer = verdict["expected_answer"]
        checked_without_model = verdict["checked_without_model"]
    else:
        deterministic = task_formats.grade(question_format, stored_payload, body.value)
        if deterministic is None:
            # Prose, or a photo of a handwritten solution: the only paths where the
            # model reads the answer. A photo is graded by looking at it, so the
            # learner does not have to retype what they worked out on paper.
            _require_llm()
            if body.image:
                graded = await content.grade_answer_with_image(
                    question=question.text,
                    criteria=question.criteria or {},
                    image=body.image,
                )
            else:
                graded = await content.grade_answer(
                    question=question.text,
                    criteria=question.criteria or {},
                    answer=body.text,
                )
            breakdown = graded["breakdown"]
            feedback = graded.get("feedback", "")
            error_types = graded.get("error_types")
        else:
            checked_without_model = True
            breakdown = {
                key: (1.0 if deterministic.correct else 0.0)
                for key in answer_scoring.CRITERIA
            }
            feedback = deterministic.detail
            error_types = deterministic.error_types
            expected_answer = deterministic.expected

    scored = answer_scoring.score_answer(
        breakdown,
        hints_used=body.hints_used,
        feedback=feedback,
        reported_errors=error_types,
    )

    answer = LearningAnswer(
        question_id=question_id,
        user_id=user.id,
        text=body.text,
        score=scored.final_score,
        raw_score=scored.raw_score,
        breakdown=scored.breakdown,
        feedback=scored.feedback,
        error_types=scored.error_types,
        hints_used=body.hints_used,
        answer_time_ms=body.answer_time_ms,
    )
    db.add(answer)

    state = await apply_scored_answer(
        db,
        goal_id=goal.id,
        user_id=user.id,
        topic_id=topic.id,
        final_score=scored.mastery_score,
        confidence=None,
    )

    # Keep this topic's task series in step, so the exam gate sees the answer.
    drill_row = await db.execute(
        select(TopicDrillModel).where(
            TopicDrillModel.goal_id == goal.id,
            TopicDrillModel.topic_id == topic.id,
        )
    )
    drill = drill_row.scalar_one_or_none()
    if drill is not None:
        # Flush so the new answer is visible to the progress query.
        await db.flush()
        await drill_ops.refresh_status(db, drill)
        # A control assignment counts as done on its scheduled day as soon as
        # its series is complete, including when it was taken ahead of time.
        if drill.status == "completed":
            await _mark_topic_practice_done(db, goal.id, topic.id)

    await db.commit()

    # Section 6.6: summarise every N graded answers. Runs after the response is
    # committed, because TZ section 12 budgets 5-10s for an answer and a summary
    # pass would eat into that.
    background.add_task(_maybe_summarise_errors, db, goal.id, user.id)

    # FR-10.1: the plan is recalculated after every check, and FR-10.4 records what
    # that changed. Runs in the background so the answer stays inside the TZ's
    # 5-10 second budget.
    background.add_task(
        _maybe_replan_after_answer,
        db,
        goal.id,
        user.id,
        topic.title,
        round(scored.mastery_score, 2),
    )

    return AnswerResponse(
        id=str(answer.id),
        question_id=str(question_id),
        raw_score=scored.raw_score,
        final_score=scored.final_score,
        hint_penalty=scored.hint_penalty,
        breakdown=scored.breakdown,
        feedback=scored.feedback,
        error_types=scored.error_types,
        next_review_at=state.next_review_at.isoformat()
        if state.next_review_at
        else None,
        interval_days=state.interval_days,
        topic_status=state.status,
        mastery_score=scored.mastery_score,
        expected_answer=expected_answer,
        checked_without_model=checked_without_model,
    )


#: What a passing run is worth at the very least. Below this a model can say the
#: approach is poor; at or below it the code stops counting as a solution at all,
#: which only a failing test may decide.
_PASSED_BUT_WEAK = 0.5


async def _grade_code_answer(
    question: LearningQuestion,
    body: SubmitAnswerRequest,
    answer_language: str = "ru",
) -> dict:
    """Grade code: the learner's own run first, the model's reading of it second."""
    payload = getattr(question, "payload", None) or {}
    tests = [t for t in (payload.get("tests") or []) if isinstance(t, dict)]

    run = code_review.verdict_from_tests(payload, body.run_results)
    breakdown: dict[str, float] = {}
    reviewed = await code_review.review_code(
        task_text=question.text,
        code=body.text,
        solution=str(payload.get("solution") or ""),
        tests=tests,
        run=run,
        language=str(payload.get("language") or "python"),
        answer_language=answer_language,
    )

    if run.ran:
        # The run is a fact. When it failed, the breakdown comes from the tests so
        # the score cannot depend on the model reading the code generously; the
        # model's part is confined to explaining it. When it passed, the model may
        # only lower the score, never raise it and never fail it — code that
        # satisfies every check has demonstrated something, and a model that
        # misreads it must not be able to take that away.
        if run.all_passed:
            for key in answer_scoring.CRITERIA:
                breakdown[key] = max(_PASSED_BUT_WEAK, min(1.0, reviewed.correctness))
        else:
            breakdown = code_review.breakdown_for_run(run)
        return {
            "breakdown": breakdown,
            "feedback": reviewed.feedback,
            "error_types": reviewed.error_types,
            "expected_answer": str(payload.get("solution") or "")[:2000] or None,
            # Said so only when it is true. If the review fell back to the run's
            # verdict, nothing read the code and the learner must not be told it
            # was read.
            "checked_without_model": not reviewed.from_model,
        }

    # Nothing ran. Either no model is available, in which case the answer cannot be
    # judged at all, or the review fell back to the same place.
    if not _llm_available() or not reviewed.from_model:
        return {
            "breakdown": {key: 0.0 for key in answer_scoring.CRITERIA},
            "feedback": (
                "Код сохранён. Проверить его нечем: в браузере запустить не удалось, "
                "а разбор недоступен. Верьте только тому, что написано в задании."
            ),
            "error_types": ["incomplete"],
            "expected_answer": str(payload.get("solution") or "")[:2000] or None,
            "checked_without_model": True,
        }

    return {
        "breakdown": {key: reviewed.correctness for key in answer_scoring.CRITERIA},
        "feedback": reviewed.feedback,
        "error_types": reviewed.error_types,
        "expected_answer": str(payload.get("solution") or "")[:2000] or None,
        "checked_without_model": False,
    }


def _llm_available() -> bool:
    try:
        from services.llm.router import get_registry

        get_registry().get()
    except Exception:  # noqa: BLE001 - a probe, not a decision
        return False
    return True


async def _maybe_summarise_errors(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Background hook: write an error summary once N answers have piled up.

    Reuses the request session on purpose. FastAPI keeps a ``yield`` dependency
    open until background tasks have run, so this session is still valid; opening
    a second one on the shared engine instead breaks under a test runner that
    closes the loop between tests.
    """
    try:
        if await error_analysis.is_due(db, goal_id, user_id):
            await error_analysis.generate_summary(db, goal_id, user_id)
    except Exception as exc:  # noqa: BLE001 - never fail a graded answer
        logger.warning("Background error summary failed for goal %s: %s", goal_id, exc)


async def _maybe_replan_after_answer(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    topic_title: str,
    score: float,
) -> None:
    """FR-10.1 + FR-10.4: replan after a check, and record why it moved.

    Skipped when the goal has no plan yet: without one there is nothing to diff,
    and a fresh goal should not be told its initial plan changed.
    """
    try:
        existing = await db.scalar(
            select(func.count(LearningPlanItem.id)).where(
                LearningPlanItem.goal_id == goal_id
            )
        )
        if not existing:
            return

        goal = await db.scalar(select(LearningGoal).where(LearningGoal.id == goal_id))
        if goal is None or goal.status == "completed":
            return

        days = (goal.deadline - date.today()).days if goal.deadline else 30
        await compute_and_store_plan(
            db,
            goal_id,
            user_id,
            days=max(1, days),
            minutes_per_day=goal.minutes_per_day,
            trigger="answer",
            context=f"{topic_title}: оценка {score:.0%}",
        )
    except Exception as exc:  # noqa: BLE001 - a replan must never fail an answer
        logger.warning("Replan after answer failed for goal %s: %s", goal_id, exc)


@router.post("/learning/answers/{answer_id}/dispute", response_model=AnswerResponse)
async def dispute_grade(
    answer_id: uuid.UUID,
    body: DisputeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-8.7: 'challenge this grade' with extra context, kept for tuning."""
    row = await db.execute(
        select(LearningAnswer).where(
            LearningAnswer.id == answer_id, LearningAnswer.user_id == user.id
        )
    )
    answer = row.scalar_one_or_none()
    if not answer:
        raise NotFoundError("Answer", answer_id)

    answer.disputed = True
    answer.dispute_note = body.note
    await db.commit()
    await db.refresh(answer)

    return AnswerResponse(
        id=str(answer.id),
        question_id=str(answer.question_id),
        raw_score=answer.raw_score,
        final_score=answer.score,
        hint_penalty=answer_scoring.hint_penalty(answer.hints_used),
        breakdown=answer.breakdown or {},
        feedback=answer.feedback,
        error_types=answer.error_types or [],
    )


@router.post("/learning/questions/{question_id}/regrade", response_model=AnswerResponse)
async def regrade_answer(
    question_id: uuid.UUID,
    answer_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-grade the same answer to measure drift (TZ section 12: within 0.15)."""
    _require_llm()
    row = await db.execute(
        select(LearningAnswer, LearningQuestion)
        .join(LearningQuestion, LearningQuestion.id == LearningAnswer.question_id)
        .join(LearningTopic, LearningTopic.id == LearningQuestion.topic_id)
        .join(LearningGoal, LearningGoal.id == LearningTopic.goal_id)
        .where(
            LearningAnswer.id == answer_id,
            LearningAnswer.question_id == question_id,
            LearningGoal.user_id == user.id,
        )
    )
    found = row.first()
    if not found:
        raise NotFoundError("Answer", answer_id)
    answer, question = found

    graded = await content.grade_answer(
        question=question.text,
        criteria=question.criteria or {},
        answer=answer.text,
    )
    rescored = answer_scoring.score_answer(
        graded["breakdown"],
        hints_used=answer.hints_used,
        feedback=graded.get("feedback", ""),
        reported_errors=graded.get("error_types"),
    )

    stable = answer_scoring.is_stable(answer.raw_score, rescored.raw_score)
    logger.info(
        "Regrade of answer %s: %.2f -> %.2f (stable=%s)",
        answer_id,
        answer.raw_score,
        rescored.raw_score,
        stable,
    )
    return AnswerResponse(
        id=str(answer.id),
        question_id=str(question_id),
        raw_score=rescored.raw_score,
        final_score=rescored.final_score,
        hint_penalty=rescored.hint_penalty,
        breakdown=rescored.breakdown,
        feedback=rescored.feedback,
        error_types=rescored.error_types,
    )


# ──────────────────────────────────────────────────────────────
# Review — TZ /goals/{id}/review (U6, FR-9.3)
# ──────────────────────────────────────────────────────────────


@router.post("/learning/goals/{goal_id}/review", response_model=QuestionResponse)
async def next_review_question(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """U6: due reviews first, then the weakest topics (FR-9.3).

    Reuses an existing question for the topic when possible so the review is
    cheap and comparable with the original attempt.
    """
    _require_llm()
    goal = await _get_goal(db, goal_id, user.id)

    states = await load_states(db, goal_id, user.id)
    now = datetime.now(timezone.utc)

    due = sorted(
        (
            (tid, st)
            for tid, st in states.items()
            if st.next_review_at and st.next_review_at <= now
        ),
        key=lambda pair: pair[1].next_review_at,
    )
    if not due:
        # FR-9.3: fall back to the weakest topics even when nothing is due yet.
        due = sorted(states.items(), key=lambda pair: pair[1].strength)

    if not due:
        raise ValidationError("Nothing to review yet: answer something first")

    topic_id, _state = due[0]

    existing = await db.execute(
        select(LearningQuestion)
        .where(LearningQuestion.topic_id == uuid.UUID(topic_id))
        .order_by(LearningQuestion.created_at.desc())
        .limit(1)
    )
    question = existing.scalar_one_or_none()

    if question is None:
        topic_row = await db.execute(
            select(LearningTopic).where(LearningTopic.id == uuid.UUID(topic_id))
        )
        topic = topic_row.scalar_one_or_none()
        if not topic:
            raise NotFoundError("Topic", topic_id)
        raise ValidationError(
            "This topic has no question yet. Generate one before starting a review."
        )

    topic_row = await db.execute(
        select(LearningTopic).where(LearningTopic.id == uuid.UUID(topic_id))
    )
    topic = topic_row.scalar_one_or_none()

    return QuestionResponse(
        id=str(question.id),
        topic_id=topic_id,
        topic_title=topic.title if topic else None,
        question_type=question.question_type,
        text=question.text,
        criteria=question.criteria,
        # FR-9.3 quick review: hints are not shown by default.
        hints=[],
        difficulty=question.difficulty,
        format=getattr(question, "format", "text") or "text",
        payload=_public_payload(question),
    )


@router.get(
    "/learning/review/due",
    response_model=DueReviewResponse,
)
async def due_review_global(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate all items needing attention across every goal of the learner.

    Combines:
    - due topic reviews (TopicState.next_review_at <= now)
    - unmastered wrong answers
    - pending/missed plan items scheduled for today

    Returns a flat list the frontend can render as a single "review inbox".
    """
    today = datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)

    # 1. Load all goals for the user (to resolve titles)
    goals_rows = list(
        (await db.execute(select(LearningGoal).where(LearningGoal.user_id == user.id)))
        .scalars()
        .all()
    )
    goal_titles = {str(g.id): g.topic for g in goals_rows}
    goal_ids = {str(g.id) for g in goals_rows}
    if not goals_rows:
        return DueReviewResponse(date=today.isoformat(), total=0, items=[])

    # Load all topics for these goals (to resolve topic titles)
    topics_rows = list(
        (
            await db.execute(
                select(LearningTopic).where(
                    LearningTopic.goal_id.in_([g.id for g in goals_rows])
                )
            )
        )
        .scalars()
        .all()
    )
    topic_titles = {str(t.id): t.title for t in topics_rows}

    items: list[DueReviewItem] = []

    # 2. Due reviews from TopicState across all goals
    states = list(
        (
            await db.execute(
                select(TopicState).where(
                    TopicState.user_id == user.id,
                    TopicState.next_review_at <= now,
                ).order_by(TopicState.next_review_at)
            )
        )
        .scalars()
        .all()
    )
    for st in states:
        gid = str(st.goal_id)
        if gid not in goal_ids:
            continue
        items.append(
            DueReviewItem(
                type="review",
                goal_id=gid,
                goal_title=goal_titles.get(gid, "Цель"),
                topic_id=str(st.topic_id),
                topic_title=topic_titles.get(str(st.topic_id)),
                title=topic_titles.get(str(st.topic_id)) or "Повторение темы",
                due_at=st.next_review_at.isoformat() if st.next_review_at else None,
                minutes=None,
                reason=(
                    f"Сила {round((st.strength or 0) * 100)}% — нужно повторить"
                ),
                status=st.status,
                action="review",
                action_href=f"/learning/goals/{gid}/review",
            )
        )

    # 3. Unmastered wrong answers
    wrong_answers = list(
        (
            await db.execute(
                select(WrongAnswer).where(
                    WrongAnswer.user_id == user.id,
                    WrongAnswer.mastered.is_(False),
                ).order_by(WrongAnswer.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for wa in wrong_answers:
        # Link to a goal of the same course, or fall back to the first goal
        goal_for_wa = None
        for g in goals_rows:
            if g.course_id and str(g.course_id) == str(wa.course_id):
                goal_for_wa = g
                break
        if not goal_for_wa:
            goal_for_wa = goals_rows[0]
        gid = str(goal_for_wa.id)
        items.append(
            DueReviewItem(
                type="wrong_answer",
                goal_id=gid,
                goal_title=goal_titles.get(gid, "Цель"),
                topic_id=None,
                topic_title=None,
                title=f"Повторить ошибку: {(wa.error_category or 'задание')}",
                day=None,
                minutes=None,
                reason=wa.diagnosis or "Нужно ещё раз разобрать ошибку",
                status="mastered" if wa.mastered else "pending",
                action="retry",
                action_href=f"/learning/goals/{gid}/wrong-answers/{wa.id}",
            )
        )

    # 4. Pending/missed plan items due today or overdue (past days the learner
    #    skipped). The frontend splits them into "Сегодня" (day == today) and
    #    "Отстающие" (day < today).
    plan_items = list(
        (
            await db.execute(
                select(LearningPlanItem).where(
                    LearningPlanItem.day <= today,
                    LearningPlanItem.goal_id.in_([g.id for g in goals_rows]),
                    LearningPlanItem.status.in_(("pending", "missed")),
                    LearningPlanItem.rejected.is_(False),
                ).order_by(LearningPlanItem.day.asc(), LearningPlanItem.created_at)
            )
        )
        .scalars()
        .all()
    )
    for pi in plan_items:
        gid = str(pi.goal_id)
        topic_title = topic_titles.get(str(pi.topic_id)) if pi.topic_id else None
        items.append(
            DueReviewItem(
                type="plan_item",
                goal_id=gid,
                goal_title=goal_titles.get(gid, "Цель"),
                topic_id=str(pi.topic_id) if pi.topic_id else None,
                topic_title=topic_title,
                title=topic_title or "Задание",
                day=pi.day.isoformat() if pi.day else None,
                minutes=pi.planned_minutes,
                reason=pi.reason,
                status=pi.status,
                action="continue",
                action_href=f"/learning/goals/{gid}/today",
            )
        )

    # 5. Video resources added beside topics — watch them
    video_resources = list(
        (
            await db.execute(
                select(TopicResource, LearningTopic)
                .join(LearningTopic, LearningTopic.id == TopicResource.topic_id)
                .where(
                    TopicResource.kind == "video",
                    LearningTopic.goal_id.in_([g.id for g in goals_rows]),
                )
            )
        )
        .all()
    )
    for resource, topic in video_resources:
        gid = str(topic.goal_id)
        items.append(
            DueReviewItem(
                type="video",
                goal_id=gid,
                goal_title=goal_titles.get(gid, "Цель"),
                topic_id=str(topic.id),
                topic_title=topic.title,
                title=resource.title or "Видео по теме",
                day=None,
                minutes=None,
                reason=resource.note or "Посмотрите видео по этой теме",
                status=None,
                action="watch",
                action_href=resource.url,
            )
        )

    # Sort: reviews first (by due_at), then plan items (by day), then wrong answers, then videos
    def sort_key(item: DueReviewItem):
        type_order = {"review": 0, "plan_item": 1, "wrong_answer": 2, "video": 3}
        return (type_order.get(item.type, 99), item.due_at or item.day or "")

    items.sort(key=sort_key)

    return DueReviewResponse(
        date=today.isoformat(),
        total=len(items),
        items=items,
    )


# ──────────────────────────────────────────────────────────────
# Exam — TZ /goals/{id}/exam (U8, FR-11)
# ──────────────────────────────────────────────────────────────


@router.post("/learning/goals/{goal_id}/exam", response_model=dict)
async def plan_exam(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-11.1/11.4: which topics an exam should cover, weighted to weak ones.

    Purely deterministic: it returns a composition, not questions. Building the
    actual exam needs the LLM and is a separate step.
    """
    await _get_goal(db, goal_id, user.id)
    states = await load_states(db, goal_id, user.id)
    if not states:
        raise ValidationError("Answer some questions before planning an exam")

    weak = [(tid, st.strength) for tid, st in states.items() if st.strength < 0.7]
    strong = [(tid, st.strength) for tid, st in states.items() if st.strength >= 0.7]

    titles = {
        str(t.id): t.title
        for t in (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id == goal_id)
            )
        )
        .scalars()
        .all()
    }

    ordered = sorted(weak, key=lambda pair: pair[1]) + sorted(
        strong, key=lambda pair: pair[1]
    )
    # FR-11.1: weak topics get the larger share.
    weak_share = 0.7 if weak else 0.0
    total = len(ordered) or 1

    return {
        "goal_id": str(goal_id),
        "weak_share": weak_share,
        "topics": [
            {
                "topic_id": tid,
                "title": titles.get(tid, ""),
                "strength": strength,
                "focus": "weak" if strength < 0.7 else "reinforce",
            }
            for tid, strength in ordered
        ],
        "suggested_questions": max(1, round(total * weak_share)) if weak else total,
    }


@router.post(
    "/learning/goals/{goal_id}/exam/start", response_model=ExamQuestionResponse
)
async def start_exam(
    goal_id: uuid.UUID,
    body: StartExamRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """U8 / FR-11: begin the final exam and serve the first question.

    The exam is the last step, not the first: a goal whose subtopics still have
    unanswered practice tasks cannot start one. The refusal names what is
    missing rather than just saying no.
    """
    await _get_goal(db, goal_id, user.id)

    # ``force`` is an explicit, visible opt-out rather than a hidden bypass. It
    # exists because a failed drill generation would otherwise lock a learner out
    # of the exam with no way forward.
    if not body.force:
        gate = await drill_ops.goal_gate(db, goal_id, user.id)
        if not gate["unlocked"]:
            raise ValidationError(
                "Экзамен откроется после того, как вы выполните задания по всем темам. "
                f"Готово тем: {gate['topics_done']} из {gate['topics_total']}. "
                "Не начато: "
                + "; ".join(
                    f"{item['title']} ({item['reason']})"
                    for item in gate["pending"][:5]
                )
            )
        # The practice day exists to unlock the exam. Reaching this point means
        # it did its job, even if the learner never pressed "finish" there.
        await _mark_special_plan_items_done(db, goal_id, ("practice",))

    # Abandon any run left in progress so the timer is not silently inherited.
    for stale in (
        (
            await db.execute(
                select(ExamModel).where(
                    ExamModel.goal_id == goal_id,
                    ExamModel.user_id == user.id,
                    ExamModel.status == "in_progress",
                )
            )
        )
        .scalars()
        .all()
    ):
        stale.status = "abandoned"
        stale.completed_at = datetime.now(timezone.utc)
    await db.flush()

    try:
        exam = await exam_service.build_exam(
            db,
            goal_id,
            user.id,
            mode=body.mode,
            time_limit_seconds=body.time_limit_seconds,
        )
    except exam_service.ExamUnavailable as exc:
        raise ValidationError(str(exc)) from exc

    return await _serve_exam_question(db, exam, index=0)


async def _serve_exam_question(
    db: AsyncSession,
    exam: ExamModel,
    *,
    index: int,
) -> ExamQuestionResponse:
    questions = await exam_service._load_questions(db, exam)
    titles = await exam_service.topic_title_map(db, exam.goal_id)

    if index >= len(questions):
        return ExamQuestionResponse(
            exam_id=str(exam.id),
            index=index,
            total=len(questions),
            question_id="",
            topic_id="",
            topic_title=None,
            text="",
            question_type="",
            hints_allowed=False,
            seconds_left=exam_service.remaining_seconds(exam),
            done=True,
        )

    question = questions[index]
    return ExamQuestionResponse(
        exam_id=str(exam.id),
        index=index,
        total=len(questions),
        question_id=str(question.id),
        topic_id=str(question.topic_id),
        topic_title=titles.get(str(question.topic_id)),
        text=question.text,
        question_type=question.question_type,
        # FR-11.2: hints are a training-mode affordance.
        hints_allowed=exam.mode == exam_service.MODE_TRAINING,
        seconds_left=exam_service.remaining_seconds(exam),
        done=False,
    )


@router.get(
    "/learning/goals/{goal_id}/exam/{exam_id}", response_model=ExamQuestionResponse
)
async def get_exam_question(
    goal_id: uuid.UUID,
    exam_id: uuid.UUID,
    index: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resume an exam; also reports the remaining time on the clock."""
    exam = await _get_exam(db, goal_id, exam_id, user.id)

    if exam_service.is_expired(exam):
        # FR-11.2: a strict exam closes when the timer runs out.
        exam.status = "completed"
        exam.completed_at = datetime.now(timezone.utc)
        exam.results = await exam_service.summarise(db, exam)
        if drill_ops.completion_for_score(
            float(exam.results.get("total_score") or 0.0)
        ):
            await _mark_special_plan_items_done(db, goal_id, ("exam",))
        await db.commit()

    return await _serve_exam_question(db, exam, index=index)


@router.post(
    "/learning/goals/{goal_id}/exam/{exam_id}/answer",
    response_model=ExamAnswerResponse,
)
async def answer_exam_question(
    goal_id: uuid.UUID,
    exam_id: uuid.UUID,
    body: ExamAnswerRequest,
    index: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Grade one exam answer, then serve the next question or finish."""
    _require_llm()
    exam = await _get_exam(db, goal_id, exam_id, user.id)

    if exam_service.is_expired(exam):
        raise ValidationError("Время экзамена истекло.")

    questions = await exam_service._load_questions(db, exam)
    if index >= len(questions):
        raise ValidationError("No question at that index")
    question = questions[index]

    hints_used = body.hints_used if exam.mode == exam_service.MODE_TRAINING else 0
    graded = await content.grade_answer(
        question=question.text,
        criteria=question.criteria or {},
        answer=body.text,
    )
    scored = answer_scoring.score_answer(
        graded["breakdown"],
        hints_used=hints_used,
        feedback=graded.get("feedback", ""),
        reported_errors=graded.get("error_types"),
    )

    # FR-11.3: an exam answer is an ordinary graded answer, so it lands in the
    # same history as practice and moves topic strength the same way. Without
    # this the exam would leave the planner blind to what it measured.
    db.add(
        LearningAnswer(
            question_id=question.id,
            user_id=user.id,
            text=body.text,
            score=scored.final_score,
            raw_score=scored.raw_score,
            breakdown=scored.breakdown,
            feedback=scored.feedback,
            error_types=scored.error_types,
            hints_used=hints_used,
        )
    )
    await apply_scored_answer(
        db,
        goal_id=goal_id,
        user_id=user.id,
        topic_id=question.topic_id,
        final_score=scored.final_score,
        confidence=None,
    )

    answers = list(exam.answers or [])
    answers.append(
        {
            "question_id": str(question.id),
            "topic_id": str(question.topic_id),
            "text": body.text[:2000],
            "score": scored.final_score,
            "raw_score": scored.raw_score,
            "hints_used": hints_used,
        }
    )
    exam.answers = answers
    exam.answered_count = len(answers)

    next_index = index + 1
    finished = next_index >= len(questions)

    if finished:
        exam.status = "completed"
        exam.completed_at = datetime.now(timezone.utc)
        exam.results = await exam_service.summarise(db, exam)
        exam.total_score = float(exam.results.get("total_score") or 0.0)

        # Auto-completion: a passed exam finishes the goal. Previously the goal
        # stayed "active" forever and only a manual PATCH could close it, which
        # made finishing feel like bookkeeping instead of an achievement.
        passed = drill_ops.completion_for_score(exam.total_score)
        if passed:
            goal_row = await db.execute(
                select(LearningGoal).where(
                    LearningGoal.id == goal_id, LearningGoal.user_id == user.id
                )
            )
            goal = goal_row.scalar_one_or_none()
            if goal is not None and goal.status != "completed":
                goal.status = "completed"
                goal.completed_at = datetime.now(timezone.utc)
                await _clear_active_goal(db, user.id, goal.id)
                logger.info(
                    "Goal %s auto-completed: exam score %.2f", goal_id, exam.total_score
                )

        # A failed exam is not a finished exam day: leaving the row pending lets
        # the daily session carry it forward as the next attempt.
        if passed:
            await _mark_special_plan_items_done(db, goal_id, ("exam",))

    await db.commit()

    # FR-11.2: in strict mode the learner only sees the grade at the end.
    reveal = exam.mode == exam_service.MODE_TRAINING or finished

    return ExamAnswerResponse(
        exam_id=str(exam.id),
        question_id=str(question.id),
        raw_score=scored.raw_score if reveal else 0.0,
        final_score=scored.final_score if reveal else 0.0,
        hint_penalty=scored.hint_penalty if reveal else 0.0,
        feedback=scored.feedback if reveal else None,
        error_types=scored.error_types if reveal else [],
        revealed=reveal,
        done=finished,
        next_question=None
        if finished
        else await _serve_exam_question(db, exam, index=next_index),
    )


@router.get(
    "/learning/goals/{goal_id}/exam/{exam_id}/result",
    response_model=ExamResultResponse,
)
async def get_exam_result(
    goal_id: uuid.UUID,
    exam_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-11.3: the mark, the per-topic breakdown and what to do next."""
    goal = await _get_goal(db, goal_id, user.id)
    exam = await _get_exam(db, goal_id, exam_id, user.id)

    if exam.status == "in_progress":
        exam.status = "completed"
        exam.completed_at = datetime.now(timezone.utc)
    summary = await exam_service.summarise(db, exam)
    exam.results = summary
    exam.total_score = summary["total_score"]

    # Same auto-completion rule as the answering endpoint, applied here too so
    # fetching the result after the fact also closes a passed goal.
    passed = drill_ops.completion_for_score(summary["total_score"])
    if passed and goal.status != "completed":
        goal.status = "completed"
        goal.completed_at = goal.completed_at or datetime.now(timezone.utc)
        await _clear_active_goal(db, user.id, goal.id)
        logger.info("Goal %s auto-completed from exam result", goal_id)

    if passed:
        await _mark_special_plan_items_done(db, goal_id, ("exam",))
    await db.commit()

    return ExamResultResponse(
        **summary,
        suggestion=exam_service.deadline_suggestion(
            goal.deadline, summary["answered"], summary["total_questions"]
        ),
        goal_completed=goal.status == "completed",
        pass_threshold=drill_ops.EXAM_PASS_SCORE,
    )


async def _profile_language(db: AsyncSession, user_id: uuid.UUID) -> str:
    """The learner's interface language, defaulting to Russian.

    Language lives on UserProfile, not User (see the model docstring), and a user
    who never opened the settings has no profile row yet.
    """
    row = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = row.scalar_one_or_none()
    return (profile.language if profile else None) or "ru"


def _resolve_language(value: str | None, fallback: str) -> str:
    """A language the prompts can answer in, falling back to the profile."""
    code = (value or "").strip().lower()
    return code if code in ("ru", "en") else fallback


async def _get_exam(
    db: AsyncSession,
    goal_id: uuid.UUID,
    exam_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ExamModel:
    row = await db.execute(
        select(ExamModel).where(
            ExamModel.id == exam_id,
            ExamModel.goal_id == goal_id,
            ExamModel.user_id == user_id,
        )
    )
    exam = row.scalar_one_or_none()
    if not exam:
        raise NotFoundError("Exam", exam_id)
    return exam


# ──────────────────────────────────────────────────────────────
# Progress — TZ /goals/{id}/progress (U9, FR-12)
# ──────────────────────────────────────────────────────────────


@router.get(
    "/learning/goals/{goal_id}/progress/strength",
    response_model=StrengthHistoryResponse,
)
async def get_strength_history(
    goal_id: uuid.UUID,
    topic_id: uuid.UUID | None = Query(None),
    days: int | None = Query(None, ge=1, le=3650),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-12.1: the strength-over-time chart.

    Purely a read of the points recorded as answers were graded, so no model is
    involved and the curve always matches the topic map.
    """
    await _get_goal(db, goal_id, user.id)
    return await error_analysis.strength_series(
        db, goal_id, user.id, topic_id=topic_id, days=days
    )


@router.get(
    "/learning/goals/{goal_id}/progress/errors",
    response_model=ErrorAnalysisResponse,
)
async def get_error_analysis(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-12.3: current error counts plus every stored summary."""
    await _get_goal(db, goal_id, user.id)
    stats, _ = await error_analysis.collect_stats(db, goal_id, user.id)
    rows = await error_analysis.list_summaries(db, goal_id, user.id)
    return ErrorAnalysisResponse(
        goal_id=str(goal_id),
        stats=stats,
        summaries=[ErrorSummaryResponse(**error_analysis.to_response(r)) for r in rows],
        due=await error_analysis.is_due(db, goal_id, user.id),
    )


@router.post(
    "/learning/goals/{goal_id}/progress/errors",
    response_model=ErrorSummaryResponse,
    status_code=201,
)
async def regenerate_error_summary(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-12.3: write a summary now instead of waiting for the next N answers."""
    _require_llm()
    await _get_goal(db, goal_id, user.id)

    row = await error_analysis.generate_summary(
        db, goal_id, user.id, force=True, language=await _profile_language(db, user.id)
    )
    if not row:
        raise ValidationError("Пока нет ответов для анализа.")
    return ErrorSummaryResponse(**error_analysis.to_response(row))


@router.get("/learning/goals/{goal_id}/progress", response_model=ProgressResponse)
async def get_progress(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """U9: topic map, error analysis and the readiness forecast. No LLM needed."""
    goal = await _get_goal(db, goal_id, user.id)

    topics = list(
        (
            await db.execute(
                select(LearningTopic)
                .where(LearningTopic.goal_id == goal_id)
                .order_by(LearningTopic.order_index)
            )
        )
        .scalars()
        .all()
    )
    states = await load_states(db, goal_id, user.id)

    items: list[TopicProgressItem] = []
    by_status: dict[str, int] = {}
    remaining_minutes = 0

    for topic in topics:
        state = states.get(str(topic.id))
        if state:
            items.append(
                TopicProgressItem(
                    topic_id=str(topic.id),
                    title=topic.title,
                    strength=round(state.strength, 3),
                    status=state.status,
                    interval_days=state.interval_days,
                    next_review_at=state.next_review_at.isoformat()
                    if state.next_review_at
                    else None,
                    repetitions=state.repetitions,
                    lapses=state.lapses,
                )
            )
            by_status[state.status] = by_status.get(state.status, 0) + 1
            if state.strength < 0.8:
                remaining_minutes += topic.estimated_minutes
        else:
            items.append(
                TopicProgressItem(
                    topic_id=str(topic.id),
                    title=topic.title,
                    strength=0.0,
                    status="not_started",
                    interval_days=0,
                    next_review_at=None,
                    repetitions=0,
                    lapses=0,
                )
            )
            by_status["not_started"] = by_status.get("not_started", 0) + 1
            remaining_minutes += topic.estimated_minutes

    days_left = (goal.deadline - date.today()).days if goal.deadline else 14
    speed = await compute_speed(db, goal_id)

    result = forecast_mod.forecast_readiness(
        forecast_mod.ForecastInput(
            remaining_minutes=remaining_minutes,
            minutes_per_day=goal.minutes_per_day,
            days_left=max(0, days_left),
            speed=speed,
            states=states,
            topic_ids=[str(t.id) for t in topics],
        )
    )

    # FR-12.3: error analysis from recent answers.
    recent = list(
        (
            await db.execute(
                select(LearningAnswer)
                .where(LearningAnswer.user_id == user.id)
                .order_by(LearningAnswer.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    error_summary = (
        answer_scoring.summarise_errors(
            [{"score": a.score, "error_types": a.error_types or []} for a in recent],
            window=20,
        )
        if recent
        else None
    )

    return ProgressResponse(
        goal_id=str(goal_id),
        topic_count=len(topics),
        topics=items,
        by_status=by_status,
        forecast=result.to_dict(),
        error_summary=error_summary,
    )


# ──────────────────────────────────────────────────────────────
# Profile — TZ /auth (FR-1.2, FR-1.3)
# ──────────────────────────────────────────────────────────────


@router.get("/learning/profile", response_model=ProfileResponse)
async def get_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = row.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return ProfileResponse(**profile.__dict__)


@router.patch("/learning/profile", response_model=ProfileResponse)
async def update_profile(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = row.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(profile, key, value)

    await db.commit()
    await db.refresh(profile)
    return ProfileResponse(**profile.__dict__)


# ──────────────────────────────────────────────────────────────
# Goal and topic refinement — FR-2.2, FR-2.3, FR-5.6, FR-6.6
# ──────────────────────────────────────────────────────────────


@router.post("/learning/goals/{goal_id}/clarify", response_model=dict)
async def clarify_goal(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-2.2: if the topic is too broad, ask the learner to narrow it down.

    Deterministic heuristics only, so this works with no LLM configured.
    """
    goal = await _get_goal(db, goal_id, user.id)

    result = await db.execute(
        select(LearningTopic).where(LearningTopic.goal_id == goal_id)
    )
    topic_count = len(result.scalars().all())

    topic = goal.topic.strip()
    vague_markers = (
        "всё",
        "все",
        "основы",
        "базу",
        "начать",
        "с нуля",
        "математика",
        "физика",
        "история",
        "программирование",
        "английский",
        "немецкий",
        "языки",
    )
    too_vague = len(topic) < 12 or (
        len(topic.split()) <= 2
        and any(marker in topic.lower() for marker in vague_markers)
    )

    if topic_count and not too_vague:
        return {
            "goal_id": str(goal_id),
            "clarification_needed": False,
            "prompt": None,
            "topic_count": topic_count,
        }

    if too_vague:
        goal.clarification_needed = True
        goal.clarification_prompt = (
            f"«{topic}» — слишком широко, чтобы построить точный план. "
            "Уточните, пожалуйста: какой именно раздел и для какой задачи? "
            "Например: «производные и интегралы для экономического анализа»."
        )
        await db.commit()
        logger.info("Goal %s flagged as needing clarification", goal_id)
        return {
            "goal_id": str(goal_id),
            "clarification_needed": True,
            "prompt": goal.clarification_prompt,
            "topic_count": topic_count,
        }

    return {
        "goal_id": str(goal_id),
        "clarification_needed": False,
        "prompt": None,
        "topic_count": topic_count,
    }


@router.patch("/learning/topics/{topic_id}", response_model=TopicResponse)
async def update_topic(
    topic_id: uuid.UUID,
    body: UpdateTopicRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-5.6: the learner marks a topic as already known or to be skipped."""
    topic = await _get_topic(db, topic_id, user.id)
    payload = body.model_dump(exclude_unset=True)

    if "title" in payload and payload["title"]:
        topic.title = payload["title"].strip()[:300]
    if "description" in payload:
        topic.description = payload["description"]
    if "estimated_minutes" in payload and payload["estimated_minutes"]:
        topic.estimated_minutes = payload["estimated_minutes"]
    if "manual_status" in payload:
        topic.manual_status = payload["manual_status"]
        if topic.manual_status == "known":
            # FR-6.2: marking as known must also update the stored state, so the
            # planner skips it rather than waiting for an answer.
            state = await ensure_topic_state(db, topic.goal_id, user.id, topic.id)
            state.strength = 1.0
            state.status = "known"
    if "source_node_id" in payload:
        await _set_topic_source(db, user.id, topic, payload["source_node_id"])

    await db.commit()
    await db.refresh(topic)

    state_row = await db.execute(
        select(TopicState).where(
            TopicState.topic_id == topic_id, TopicState.user_id == user.id
        )
    )
    return topic_to_response(topic, state_row.scalar_one_or_none())


@router.post(
    "/learning/goals/{goal_id}/plan/{item_id}/add-topic",
    response_model=PlanItemResponse,
    status_code=201,
)
async def append_topic_to_plan(
    goal_id: uuid.UUID,
    item_id: uuid.UUID,
    body: AddToPlanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-6.6: add a topic to the plan manually."""
    await _get_goal(db, goal_id, user.id)

    row = await db.execute(
        select(LearningPlanItem).where(
            LearningPlanItem.id == item_id, LearningPlanItem.goal_id == goal_id
        )
    )
    if not row.scalar_one_or_none():
        raise NotFoundError("Plan item", item_id)

    topic = await _get_topic(db, body.topic_id, user.id)
    if topic.goal_id != goal_id:
        raise ValidationError("That topic belongs to a different goal")

    created = await plan_ops.add_topic_to_plan(
        db, goal_id, topic, day=body.day, minutes=body.minutes
    )
    return PlanItemResponse(
        id=str(created.id),
        topic_id=str(created.topic_id),
        title=topic.title,
        item_type=created.item_type,
        day=created.day.isoformat(),
        planned_minutes=created.planned_minutes,
        actual_minutes=created.actual_minutes,
        status=created.status,
        reason=created.reason,
        rejected=False,
    )


# ──────────────────────────────────────────────────────────────
# Catch-up and multi-goal budgeting — FR-10.5, FR-2.3
# ──────────────────────────────────────────────────────────────


@router.post("/learning/goals/{goal_id}/plan/catch-up", response_model=PlanResponse)
async def catch_up_plan(
    goal_id: uuid.UUID,
    body: ReplanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-10.5: after missing days, offer a gentler plan than a backlog."""
    goal = await _get_goal(db, goal_id, user.id)

    topics = list(
        (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id == goal_id)
            )
        )
        .scalars()
        .all()
    )
    if not topics:
        raise ValidationError("Add subtopics before replanning")

    days = body.days or ((goal.deadline - date.today()).days if goal.deadline else 30)
    minutes = body.minutes_per_day or goal.minutes_per_day
    mode = body.mode

    result = await plan_ops.rebuild_plan_with_catchup(
        db, goal_id, user.id, days=max(1, days), minutes_per_day=minutes, mode=mode
    )

    stored = list(
        (
            await db.execute(
                select(LearningPlanItem, LearningTopic)
                .outerjoin(LearningTopic, LearningTopic.id == LearningPlanItem.topic_id)
                .where(LearningPlanItem.goal_id == goal_id)
                .order_by(LearningPlanItem.day)
            )
        ).all()
    )

    days_map: dict[str, list[PlanItemResponse]] = {}
    for row, topic in stored:
        days_map.setdefault(row.day.isoformat(), []).append(
            PlanItemResponse(
                id=str(row.id),
                topic_id=str(row.topic_id) if row.topic_id else None,
                title=topic.title if topic else _plan_item_title(
                    row.item_type, row.topic_id, {}
                ),
                item_type=row.item_type,
                day=row.day.isoformat(),
                planned_minutes=row.planned_minutes,
                actual_minutes=row.actual_minutes,
                status=row.status,
                reason=row.reason,
                rejected=row.rejected,
            )
        )

    return PlanResponse(
        goal_id=str(goal_id),
        days=days_map,
        total_minutes=sum(
            i.planned_minutes for items in days_map.values() for i in items
        ),
        available_minutes=result.available_minutes,
        feasible=result.feasible,
        speed=result.speed,
        warnings=result.warnings,
        suggestions=result.suggestions,
    )


@router.get("/learning/allocation", response_model=dict)
async def allocate_time(
    goal_id: list[str] = Query(..., description="Goal ids sharing the daily budget"),
    minutes_per_day: int = Query(120, ge=5, le=960),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-2.3: split the daily budget across several goals.

    Repeat ``goal_id`` to pass several: ``?goal_id=A&goal_id=B``. FastAPI does
    not apply an alias to list query parameters, so the name is used directly.

    The path deliberately does not sit under ``/learning/goals/{goal_id}``: that
    route is registered first, so a static segment there would be parsed as a
    UUID and fail validation with a 422.
    """
    active = list(
        (
            await db.execute(
                select(LearningGoal).where(
                    LearningGoal.user_id == user.id, LearningGoal.status == "active"
                )
            )
        )
        .scalars()
        .all()
    )
    titles = {str(g.id): g.topic for g in active}

    stored_shares = {}
    for goal in active:
        meta = goal.metadata_json or {}
        if meta.get("time_share") is not None:
            stored_shares[str(goal.id)] = float(meta["time_share"])

    requested = [{"id": gid, "time_share": stored_shares.get(gid)} for gid in goal_id]
    allocation = plan_ops.split_daily_minutes(requested, minutes_per_day)

    return {
        "minutes_per_day": minutes_per_day,
        "total_allocated": sum(allocation.values()),
        "allocation": [
            {"goal_id": gid, "topic": titles.get(gid, ""), "minutes_per_day": minutes}
            for gid, minutes in allocation.items()
        ],
    }


# ──────────────────────────────────────────────────────────────
# History and export — FR-12.2, FR-15.1
# ──────────────────────────────────────────────────────────────


@router.get("/learning/goals/{goal_id}/answers", response_model=list[AnswerResponse])
async def list_answers(
    goal_id: uuid.UUID,
    topic_id: uuid.UUID | None = Query(None),
    min_score: float | None = Query(None, ge=0.0, le=1.0),
    max_score: float | None = Query(None, ge=0.0, le=1.0),
    error_type: str | None = Query(None),
    disputed: bool | None = Query(None),
    since: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-12.2: answer history filtered by topic, score, date and error type."""
    await _get_goal(db, goal_id, user.id)

    query = (
        select(LearningAnswer, LearningQuestion, LearningTopic)
        .join(LearningQuestion, LearningQuestion.id == LearningAnswer.question_id)
        .join(LearningTopic, LearningTopic.id == LearningQuestion.topic_id)
        .where(LearningTopic.goal_id == goal_id, LearningAnswer.user_id == user.id)
    )
    if topic_id:
        query = query.where(LearningTopic.id == topic_id)
    if min_score is not None:
        query = query.where(LearningAnswer.score >= min_score)
    if max_score is not None:
        query = query.where(LearningAnswer.score <= max_score)
    if disputed is not None:
        query = query.where(LearningAnswer.disputed.is_(disputed))
    if since:
        query = query.where(LearningAnswer.created_at >= since)

    rows = list(
        (
            await db.execute(
                query.order_by(LearningAnswer.created_at.desc()).limit(limit)
            )
        ).all()
    )
    # SQLite JSON filtering is unreliable, so error_type is applied in Python.
    if error_type:
        rows = [r for r in rows if error_type in (r[0].error_types or [])]

    return [
        AnswerResponse(
            id=str(answer.id),
            question_id=str(answer.question_id),
            raw_score=answer.raw_score,
            final_score=answer.score,
            hint_penalty=answer_scoring.hint_penalty(answer.hints_used),
            breakdown=answer.breakdown or {},
            feedback=answer.feedback,
            error_types=answer.error_types or [],
        )
        for answer, _question, _topic in rows
    ]


@router.get("/learning/goals/{goal_id}/export.ics")
async def export_ics(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-15.1: download the plan as an importable calendar file."""
    try:
        payload = await plan_ops.export_plan_to_ics(db, goal_id, user.id)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    return Response(
        content=payload,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="opentutor-plan-{goal_id}.ics"'
        },
    )


# ──────────────────────────────────────────────────────────────
# Dependency graph — FR-5.5
# ──────────────────────────────────────────────────────────────


@router.get("/learning/goals/{goal_id}/graph", response_model=dict)
async def get_topic_graph(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-5.5: the topic graph for the progress screen, with cycle detection."""
    await _get_goal(db, goal_id, user.id)

    topics = list(
        (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id == goal_id)
            )
        )
        .scalars()
        .all()
    )
    ids = [str(t.id) for t in topics]
    edges = list(
        (
            await db.execute(
                select(TopicDependency).where(
                    TopicDependency.topic_id.in_([uuid.UUID(i) for i in ids])
                )
            )
        )
        .scalars()
        .all()
        if ids
        else []
    )
    states = await load_states(db, goal_id, user.id)

    from services.adaptive_learning.scheduling import (
        find_cycle,
        topological_order,
        TopicInput,
    )

    topic_map = {
        str(t.id): TopicInput(
            id=str(t.id),
            title=t.title,
            estimated_minutes=t.estimated_minutes,
            prerequisites=[
                str(e.prerequisite_id) for e in edges if str(e.topic_id) == str(t.id)
            ],
        )
        for t in topics
    }
    cycle = find_cycle(topic_map) if topic_map else None

    return {
        "goal_id": str(goal_id),
        "nodes": [
            {
                "id": str(t.id),
                "title": t.title,
                "status": states.get(str(t.id)).status
                if str(t.id) in states
                else "not_started",
                "strength": round(states[str(t.id)].strength, 3)
                if str(t.id) in states
                else 0.0,
            }
            for t in topics
        ],
        "edges": [
            {
                "prerequisite": str(e.prerequisite_id),
                "topic": str(e.topic_id),
                "weight": e.weight,
            }
            for e in edges
        ],
        "order": topological_order(topic_map) if topic_map else [],
        # FR-5.2: the graph must stay acyclic, so surface violations loudly.
        "cycle": cycle,
    }


# ──────────────────────────────────────────────────────────────
# History and export — FR-12.2, FR-15.1
# ──────────────────────────────────────────────────────────────


@router.get("/learning/goals/{goal_id}/answers", response_model=list[AnswerResponse])
async def list_answers(
    goal_id: uuid.UUID,
    topic_id: uuid.UUID | None = Query(None),
    min_score: float | None = Query(None, ge=0.0, le=1.0),
    max_score: float | None = Query(None, ge=0.0, le=1.0),
    error_type: str | None = Query(None),
    disputed: bool | None = Query(None),
    since: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-12.2: answer history filtered by topic, score, date and error type."""
    await _get_goal(db, goal_id, user.id)

    query = (
        select(LearningAnswer, LearningQuestion, LearningTopic)
        .join(LearningQuestion, LearningQuestion.id == LearningAnswer.question_id)
        .join(LearningTopic, LearningTopic.id == LearningQuestion.topic_id)
        .where(LearningTopic.goal_id == goal_id, LearningAnswer.user_id == user.id)
    )
    if topic_id:
        query = query.where(LearningTopic.id == topic_id)
    if min_score is not None:
        query = query.where(LearningAnswer.score >= min_score)
    if max_score is not None:
        query = query.where(LearningAnswer.score <= max_score)
    if disputed is not None:
        query = query.where(LearningAnswer.disputed.is_(disputed))
    if since:
        query = query.where(LearningAnswer.created_at >= since)

    rows = list(
        (
            await db.execute(
                query.order_by(LearningAnswer.created_at.desc()).limit(limit)
            )
        ).all()
    )
    # SQLite JSON filtering is unreliable, so error_type is applied in Python.
    if error_type:
        rows = [r for r in rows if error_type in (r[0].error_types or [])]

    return [
        AnswerResponse(
            id=str(answer.id),
            question_id=str(answer.question_id),
            raw_score=answer.raw_score,
            final_score=answer.score,
            hint_penalty=answer_scoring.hint_penalty(answer.hints_used),
            breakdown=answer.breakdown or {},
            feedback=answer.feedback,
            error_types=answer.error_types or [],
        )
        for answer, _question, _topic in rows
    ]


@router.get("/learning/goals/{goal_id}/export.ics")
async def export_ics(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-15.1: download the plan as an importable calendar file."""
    try:
        payload = await plan_ops.export_plan_to_ics(db, goal_id, user.id)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    return Response(
        content=payload,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="opentutor-plan-{goal_id}.ics"'
        },
    )


# ──────────────────────────────────────────────────────────────
# Dependency graph — FR-5.5
# ──────────────────────────────────────────────────────────────


@router.get("/learning/goals/{goal_id}/graph", response_model=dict)
async def get_topic_graph(
    goal_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FR-5.5: the topic graph for the progress screen, with cycle detection."""
    await _get_goal(db, goal_id, user.id)

    topics = list(
        (
            await db.execute(
                select(LearningTopic).where(LearningTopic.goal_id == goal_id)
            )
        )
        .scalars()
        .all()
    )
    ids = [str(t.id) for t in topics]
    edges = list(
        (
            await db.execute(
                select(TopicDependency).where(
                    TopicDependency.topic_id.in_([uuid.UUID(i) for i in ids])
                )
            )
        )
        .scalars()
        .all()
        if ids
        else []
    )
    states = await load_states(db, goal_id, user.id)

    from services.adaptive_learning.scheduling import (
        find_cycle,
        topological_order,
        TopicInput,
    )

    topic_map = {
        str(t.id): TopicInput(
            id=str(t.id),
            title=t.title,
            estimated_minutes=t.estimated_minutes,
            prerequisites=[
                str(e.prerequisite_id) for e in edges if str(e.topic_id) == str(t.id)
            ],
        )
        for t in topics
    }
    cycle = find_cycle(topic_map) if topic_map else None

    return {
        "goal_id": str(goal_id),
        "nodes": [
            {
                "id": str(t.id),
                "title": t.title,
                "status": states.get(str(t.id)).status
                if str(t.id) in states
                else "not_started",
                "strength": round(states[str(t.id)].strength, 3)
                if str(t.id) in states
                else 0.0,
            }
            for t in topics
        ],
        "edges": [
            {
                "prerequisite": str(e.prerequisite_id),
                "topic": str(e.topic_id),
                "weight": e.weight,
            }
            for e in edges
        ],
        "order": topological_order(topic_map) if topic_map else [],
        # FR-5.2: the graph must stay acyclic, so surface violations loudly.
        "cycle": cycle,
    }


