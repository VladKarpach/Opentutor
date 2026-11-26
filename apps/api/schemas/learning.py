"""Pydantic schemas for the adaptive learning API (TZ section 10).

TZ FR-2.1 defines the goal input: topic (free text), deadline, available time
per day, and desired depth (overview / confident / deep).
"""

from __future__ import annotations

import uuid
from typing import Any
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# FR-2.1 depths.
DEPTHS = ("overview", "confident", "deep")
GOAL_STATUSES = ("active", "paused", "completed", "archived")
# FR-8.6 three hint levels.
MAX_HINTS = 3


class CreateGoalRequest(BaseModel):
    """FR-2.1: set the goal."""

    topic: str = Field(min_length=1, max_length=2000)
    deadline: date | None = None
    days: int | None = Field(default=None, ge=1, le=3650)
    minutes_per_day: int = Field(default=60, ge=5, le=960)
    depth: str = Field(default="confident")

    # FR-2.3: an optional share of the daily budget, 0-1, for several goals.
    time_share: float | None = Field(default=None, ge=0.05, le=1.0)

    # Link the goal to a course workspace so it can use that course's materials.
    course_id: uuid.UUID | None = None

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("topic must not be empty")
        return cleaned

    @field_validator("depth")
    @classmethod
    def _check_depth(cls, value: str) -> str:
        if value not in DEPTHS:
            raise ValueError(f"depth must be one of {', '.join(DEPTHS)}")
        return value

    def resolved_deadline(self) -> date | None:
        """FR-2.1 accepts either an absolute date or a number of days."""
        if self.deadline:
            return self.deadline
        if self.days:
            from datetime import timedelta

            return date.today() + timedelta(days=self.days)
        return None


class UpdateGoalRequest(BaseModel):
    """FR-2.4: editing the goal triggers a replan."""

    topic: str | None = Field(default=None, max_length=2000)
    # Attach an existing standalone goal to a course, or null to detach it.
    course_id: uuid.UUID | None = None
    deadline: date | None = None
    minutes_per_day: int | None = Field(default=None, ge=5, le=960)
    depth: str | None = None
    status: str | None = None

    @field_validator("depth")
    @classmethod
    def _check_depth(cls, value: str | None) -> str | None:
        if value is not None and value not in DEPTHS:
            raise ValueError(f"depth must be one of {', '.join(DEPTHS)}")
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is not None and value not in GOAL_STATUSES:
            raise ValueError(f"status must be one of {', '.join(GOAL_STATUSES)}")
        return value


class GoalResponse(BaseModel):
    id: str
    topic: str
    deadline: str | None
    minutes_per_day: int
    depth: str
    status: str
    clarification_needed: bool
    # True for the one goal the guided daily mode currently opens.
    is_active: bool = False
    # Set when the goal was closed — manually, or automatically by a passed exam.
    completed_at: str | None = None
    # The course workspace this goal belongs to, when it has one.
    course_id: str | None = None
    course_name: str | None = None
    topic_count: int = 0
    days_left: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    # How far the goal has got. The course page lists every goal and previously
    # showed only a topic count, so there was no way to tell from the course which
    # goal was finished, which was under way, or what to open next.
    topics_done: int = 0
    exam_status: str = "none"
    exam_score: float | None = None
    exam_passed: bool = False
    current_topic_title: str | None = None
    progress_percent: int = 0


class LearnerContextResponse(BaseModel):
    """What the system already knows about this learner across all goals."""

    goals_seen: int
    topics_touched: int
    answers_total: int
    answered_recently: int
    known: list[str]
    weak: list[str]
    in_progress: list[str]
    prior_goal_topics: list[str]
    error_profile: dict[str, int]
    dominant_error: str
    mean_score: float
    course_title: str = ""
    course_goals: int = 0
    course_documents: int = 0
    summary: str = ""


class CourseGoalsResponse(BaseModel):
    """Goals attached to one course workspace."""

    course_id: str
    course_name: str | None = None
    goals: list[GoalResponse]


class CourseGoalsGroup(BaseModel):
    """One course and the goals that live inside it."""

    course_id: str
    course_name: str
    goal_count: int
    goals: list[GoalResponse]


class LearningIndex(BaseModel):
    """The whole learning tree in one call: courses with their goals.

    Goals are grouped by course because a course is the container. Anything not
    attached to a course is surfaced separately rather than dropped, so existing
    work is never silently hidden.
    """

    courses: list[CourseGoalsGroup]
    unattached: list[GoalResponse]
    total_goals: int
    # The goal the learner selected for "Сегодня". None on an older install that
    # has never made a choice; the UI keeps its old nearest-deadline fallback.
    active_goal_id: str | None = None


class TopicResponse(BaseModel):
    id: str
    goal_id: str
    title: str
    description: str | None
    estimated_minutes: int
    difficulty: str = "medium"
    order_index: int
    manual_status: str | None
    # FR-9.1 current state, when known.
    strength: float | None = None
    status: str | None = None
    next_review_at: str | None = None
    # Whether an explanation exists for this topic. Notes and quizzes refuse
    # without one, so the learner needs to see which topics can be worked on.
    has_explanation: bool | None = None


class GenerateTopicsRequest(BaseModel):
    """TZ 6.1 step 1: ask the model to split the goal into subtopics.

    Omitted fields fall back to the goal's own depth and the default language.
    """

    depth: str | None = None
    language: str | None = None

    @field_validator("depth")
    @classmethod
    def _check_depth(cls, value: str | None) -> str | None:
        if value is not None and value not in DEPTHS:
            raise ValueError(f"depth must be one of {', '.join(DEPTHS)}")
        return value


class StartExamRequest(BaseModel):
    """FR-11.2: training (hints, no timer) or strict (timer, no hints)."""

    mode: str = "training"
    time_limit_seconds: int | None = Field(default=None, ge=30, le=14400)
    # Skip the practice gate. A deliberate opt-out rather than a hidden bypass:
    # without it, a failed drill generation would lock a learner out of the exam
    # with no way forward.
    force: bool = False

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        if value not in ("training", "strict"):
            raise ValueError("mode must be 'training' or 'strict'")
        return value


class ExamQuestionResponse(BaseModel):
    """One question inside an exam, with the timer state attached."""

    exam_id: str
    index: int
    total: int
    question_id: str
    topic_id: str
    topic_title: str | None
    text: str
    question_type: str
    hints_allowed: bool
    seconds_left: int | None
    done: bool


class ExamAnswerRequest(BaseModel):
    """FR-11.2: hints are only accepted in training mode."""

    text: str = Field(min_length=1, max_length=20000)
    hints_used: int = Field(default=0, ge=0, le=MAX_HINTS)


class ExamAnswerResponse(BaseModel):
    exam_id: str
    question_id: str
    raw_score: float
    final_score: float
    hint_penalty: float
    feedback: str | None
    error_types: list[str] = []
    # FR-11.2: strict mode shows nothing until the exam is finished.
    revealed: bool
    done: bool
    next_question: ExamQuestionResponse | None = None


class ExamResultResponse(BaseModel):
    """FR-11.3: overall mark, per-topic breakdown and recommendations."""

    exam_id: str
    mode: str
    status: str
    total_score: float
    answered: int
    total_questions: int
    by_topic: list[dict]
    weak_topics: list[str]
    recommendations: list[str]
    suggestion: str | None = None
    # True when this result closed the goal (FR-11.3 auto-completion).
    goal_completed: bool = False
    pass_threshold: float = 0.7


class StrengthPoint(BaseModel):
    at: str | None
    strength: float
    interval_days: int = 0
    was_lapse: bool = False


class AverageStrengthPoint(BaseModel):
    at: str
    strength: float
    samples: int
    lapses: int = 0


class TopicStrengthSeries(BaseModel):
    topic_id: str
    title: str
    points: list[StrengthPoint]
    current: float


class MaterialPassage(BaseModel):
    """One passage of the learner's own material."""

    node_id: str
    title: str | None = None
    source_file: str | None = None
    level: int | None = None
    matched_terms: list[str] = Field(default_factory=list)
    excerpt: str = ""


class MaterialCandidatesResponse(BaseModel):
    """What the topic can be explained from, and what it is pinned to."""

    topic_id: str
    course_id: str | None = None
    source_node_id: str | None = None
    #: True when the course has material at all, so an empty list can be read
    #: as "nothing matched" rather than "nothing uploaded".
    course_has_material: bool = False
    automatic: list[MaterialPassage] = Field(default_factory=list)
    source_label: str = ""


class TopicSourceFile(BaseModel):
    """One uploaded file the learner can pick for an explanation."""

    name: str
    nodes: int = 0


class TopicSourcesResponse(BaseModel):
    """The course's uploaded files, offered when generating the explanation.

    Choosing a file replaces automatic keyword matching: the learner already
    knows which book the topic is from, so the guess is not needed.
    """

    topic_id: str
    course_id: str | None = None
    files: list[TopicSourceFile] = Field(default_factory=list)


class TopicResourceCreate(BaseModel):
    url: str = Field(..., min_length=4, max_length=1000)
    title: str | None = Field(default=None, max_length=300)
    note: str | None = None
    kind: str = "video"


class TopicResourceResponse(BaseModel):
    id: str
    topic_id: str
    kind: str
    url: str
    title: str | None
    note: str | None


class StrengthHistoryResponse(BaseModel):
    """FR-12.1: the strength-over-time chart."""

    goal_id: str
    overall: list[AverageStrengthPoint]
    by_topic: list[TopicStrengthSeries]
    total_points: int
    topic_count: int


class WeakTopicStat(BaseModel):
    topic_id: str
    title: str
    mean_score: float
    answers: int
    dominant_error: str = ""
    weak: bool


class ErrorSummaryResponse(BaseModel):
    """FR-12.3: the stored error analysis for one period."""

    id: str
    goal_id: str
    period_start: str | None = None
    period_end: str | None = None
    summary_text: str | None = None
    recommendations: list[str] = []
    answer_count: int = 0
    stats: dict = {}
    generated_at: str | None = None


class ErrorAnalysisResponse(BaseModel):
    """FR-12.3: current counts plus the stored summaries."""

    goal_id: str
    stats: dict
    summaries: list[ErrorSummaryResponse]
    due: bool


class PlanChange(BaseModel):
    """One difference between the old and the new plan (FR-10.4)."""

    kind: str
    topic_id: str = ""
    topic_title: str = ""
    item_type: str = ""
    before: str | int | None = None
    after: str | int | None = None
    delta_minutes: int = 0
    reason: str = ""
    # Pre-rendered line, e.g. "добавлено 30 мин: повторение темы «X»".
    text: str = ""


class PlanRevisionResponse(BaseModel):
    """A recorded replan the learner can accept or decline."""

    id: str
    goal_id: str
    trigger: str
    summary: str
    status: str
    changes: list[PlanChange] = []
    created_at: str | None = None
    resolved_at: str | None = None


class PlanRevisionList(BaseModel):
    goal_id: str
    revisions: list[PlanRevisionResponse]
    pending_count: int


class DrillProgress(BaseModel):
    """How far the learner has got through one subtopic's task series."""

    drill_id: str | None = None
    required: int = 0
    answered: int = 0
    done: bool = False
    prepared: bool = False
    status: str = "pending"
    answered_question_ids: list[str] = []
    remaining_question_ids: list[str] = []


class ScheduledTopic(BaseModel):
    """A subtopic together with its plan dates and its practice progress.

    This is the merged list the goal page shows, so the learner does not have to
    switch tabs to see what is scheduled for when and how much of it is done.
    """

    topic_id: str
    title: str
    description: str | None = None
    order_index: int = 0
    estimated_minutes: int = 0

    # From the plan: when this topic is scheduled.
    first_day: str | None = None
    last_day: str | None = None
    study_days: list[str] = []
    planned_minutes: int = 0
    # Control assignments for this topic, as separate scheduled items
    # (one per required practice task, e.g. "Контрольное задание 1 по теме X").
    practice_items: list[dict] = []

    # From the repetition engine.
    strength: float | None = None
    status: str | None = None
    next_review_at: str | None = None

    # From the practice series.
    drill: DrillProgress = Field(default_factory=DrillProgress)


class GoalScheduleResponse(BaseModel):
    goal_id: str
    topics: list[ScheduledTopic]
    exam_unlocked: bool
    topics_total: int
    topics_done: int
    pending_exam: list[dict] = []


class DrillResponse(BaseModel):
    topic_id: str
    title: str
    drill: DrillProgress
    questions: list[QuestionResponse] = []


class UpdateTopicRequest(BaseModel):
    """FR-5.6: manual topic edits, including 'I already know this'."""

    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=5, le=960)
    manual_status: str | None = None
    # FR-3.2: pin the topic to one passage of the uploaded literature. Empty
    # string clears the pin and goes back to automatic matching.
    source_node_id: str | None = None

    @field_validator("manual_status")
    @classmethod
    def _check_manual_status(cls, value: str | None) -> str | None:
        allowed = ("known", "skip")
        if value is not None and value not in allowed:
            raise ValueError(f"manual_status must be one of {', '.join(allowed)}, or omitted")
        return value


class AddToPlanRequest(BaseModel):
    """FR-6.6: append a topic to an existing plan by hand."""

    topic_id: uuid.UUID
    day: date | None = None
    minutes: int | None = Field(default=None, ge=5, le=960)


class CreateTopicsRequest(BaseModel):
    """Manual subtopic list, used when the model is unavailable (FR-5.1)."""

    topics: list[str] = Field(min_length=1, max_length=50)


class ReplanRequest(BaseModel):
    """FR-10.1: replan, optionally with a speed override.

    ``mode`` selects which learning regime the plan is optimised for
    (course_following | self_paced). When omitted the scheduler keeps its
    defaults.
    """

    days: int | None = Field(default=None, ge=1, le=365)
    minutes_per_day: int | None = Field(default=None, ge=5, le=960)
    speed: float | None = Field(default=None, ge=0.1, le=5.0)
    mode: str | None = Field(default=None)


class PlanItemResponse(BaseModel):
    id: str
    topic_id: str | None
    title: str
    item_type: str
    day: str
    planned_minutes: int
    actual_minutes: int | None
    status: str
    reason: str | None
    rejected: bool


class PlanResponse(BaseModel):
    """FR-6.5 day-by-day plan plus FR-6.4 feasibility feedback."""

    goal_id: str
    days: dict[str, list[PlanItemResponse]]
    total_minutes: int
    available_minutes: int
    feasible: bool
    speed: float
    warnings: list[str] = []
    suggestions: list[str] = []


class DailyStepResponse(BaseModel):
    """One step of the guided daily session."""

    kind: str
    # review | theory | practice | exam_prep | exam
    # The two dedicated end-of-plan days are not tied to one topic, so this is
    # optional. The first topic is still sent as a useful practice entry point.
    topic_id: str | None
    title: str
    description: str | None = None
    minutes: int
    reason: str | None = None
    item_id: str | None = None
    has_explanation: bool = False
    theory: str | None = None
    strength: float | None = None
    status: str | None = None
    practice_ready: bool = False


class DailySessionResponse(BaseModel):
    """What to do today, in order, without choosing a topic first.

    The plan still owns the dates. This response is the short, runnable slice
    of one day: a warm-up, the theory for the day's topic, and one practice
    block. ``scheduled_for`` can be later than ``date`` when the plan has a rest
    day and the learner ran the next lesson early.
    """

    goal_id: str
    goal_title: str
    date: str
    scheduled_for: str
    minutes_per_day: int
    total_minutes: int
    steps: list[DailyStepResponse] = []
    shifted_days: int = 0
    carried_topics: list[str] = []
    completed: bool = False
    empty_reason: str | None = None
    message: str | None = None
    next_session_at: str | None = None
    remaining_topics: int = 0


class DailyAgendaResponse(BaseModel):
    """Every guided session that has work scheduled for today.

    A learner can have several goals in progress, and the global calendar can
    place more than one goal on the same day. "Сегодня" must show all of those
    blocks, not only the explicitly selected goal.
    """

    date: str
    active_goal_id: str | None = None
    sessions: list[DailySessionResponse] = []
    total_minutes: int = 0


class DailyCompleteRequest(BaseModel):
    """Close the guided session for the topics that were actually finished.

    The client names topics rather than plan-item ids because a graded answer
    may replan the goal, replacing the items the session was built from. Topics
    survive that; stale ids do not.
    """

    day: date | None = None
    topic_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    # Plan rows for the task-only and exam days are not tied to a topic, so they
    # are named directly. Without this the practice day would stay pending even
    # after the learner finished all its tasks.
    item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class DailyCompleteResponse(BaseModel):
    goal_id: str
    date: str
    completed_items: int


class DailyPullRequest(BaseModel):
    """Take one future plan day now, leaving a shortened review in its place."""

    day: date | None = None


class DailyPullResponse(BaseModel):
    goal_id: str
    from_day: str
    moved_items: int
    review_minutes: int
    review_topic_id: str | None = None
    remaining_items: int = 0
    message: str | None = None


class PlanItemPatch(BaseModel):
    """FR-6.6: manual edits to the plan."""

    status: str | None = None
    rejected: bool | None = None
    day: date | None = None
    planned_minutes: int | None = Field(default=None, ge=5, le=960)

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        allowed = ("pending", "done", "skipped", "missed")
        if value is not None and value not in allowed:
            raise ValueError(f"status must be one of {', '.join(allowed)}")
        return value


class PullAheadRequest(BaseModel):
    """Взять конкретное задание из будущего дня на сегодня (или на указанный день)."""

    target_day: date | None = None


class PullAheadResponse(BaseModel):
    pulled: PlanItemResponse
    original: PlanItemResponse


class DiagnosticStartRequest(BaseModel):
    """U2: begin the hidden diagnostic."""

    subtopic_ids: list[uuid.UUID] | None = None
    max_replies: int = Field(default=8, ge=4, le=12)


class DiagnosticTurnResponse(BaseModel):
    """One probing turn. FR-4.5: no level question is ever returned."""

    session_id: str
    turn_index: int
    subtopic_id: str
    subtopic_title: str
    question: str
    technique: str | None
    difficulty: float
    done: bool


class DiagnosticReplyRequest(BaseModel):
    """U2: the learner's reply."""

    text: str = Field(min_length=1, max_length=8000)


class DiagnosticStateResponse(BaseModel):
    """FR-4.5: 'here is what I understood about your level', editable."""

    session_id: str
    status: str
    reply_count: int
    finished_reason: str | None
    estimates: list[dict]
    aggregate_uncertainty: float


class CorrectionRequest(BaseModel):
    """FR-4.5: the learner corrects the estimate."""

    estimates: dict[str, float]


class GenerateTaskRequest(BaseModel):
    """FR-8.1: produce a task for a topic."""

    topic_id: uuid.UUID | None = None
    difficulty: float | None = Field(default=None, ge=0.0, le=1.0)
    language: str | None = None
    #: Ask for one reply format instead of letting the topic decide. Ignored when
    #: it is not a format this build knows.
    format: str | None = None


class QuestionResponse(BaseModel):
    id: str
    topic_id: str
    topic_title: str | None = None
    question_type: str
    text: str
    criteria: dict | None
    hints: list[str] = []
    difficulty: float
    source_label: str | None = None
    #: How the learner replies. The client renders a different control per value;
    #: see services/adaptive_learning/task_formats.py for what each one carries.
    format: str = "text"
    #: What that format needs: options, a number and its tolerance, starter code
    #: and test cases, steps, pairs. Never the correct answer — that stays on the
    #: server, except for the practice tabs that reveal it deliberately.
    payload: dict | None = None


class HintResponse(BaseModel):
    """FR-8.6: nudges escalate; each level lowers the grade."""

    level: int
    hint: str


class SubmitAnswerRequest(BaseModel):
    """U5: submit an answer for grading.

    ``text`` still carries a prose answer, and carries code too. It is kept
    non-empty for every format because the answer is stored as text and reviewed
    as text; the formats that are not prose put their answer in ``value`` and
    repeat it in ``text`` in whatever form reads best for the review screen.
    """

    text: str = Field(min_length=1, max_length=20000)
    # FR-8.6
    hints_used: int = Field(default=0, ge=0, le=MAX_HINTS)
    answer_time_ms: int | None = Field(default=None, ge=0)
    #: The structured answer for the non-prose formats: option indices, a number,
    #: the order the steps were put in, the pairs that were joined. ``None`` means
    #: the learner replied in prose, or used a control that did not produce one.
    value: Any = None
    #: What the learner saw when they ran their own code in the browser. The
    #: server trusts only the pass/fail of each named test and never the reported
    #: output text — that is decided by the model's review instead, so a client
    #: cannot buy a passing grade by inventing a clean run.
    run_results: list[dict] | None = None
    #: A photo of a handwritten solution, as a base64 data URL. When present the
    #: answer is graded by looking at the image rather than reading the text, so a
    #: learner who worked on paper can send a photo of it instead of retyping.
    #: The image is sent to the model as a separate content part.
    image: str | None = None
    #: Interface language selected in settings. Code feedback is written in it;
    #: when absent the user's profile language is used.
    language: str | None = Field(default=None, max_length=10)

    @field_validator("image")
    @classmethod
    def _check_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # A data URL is the only shape a browser can produce without a file
        # endpoint of its own, and it is what the model expects.
        if not value.startswith("data:image/"):
            raise ValueError("image must be a base64 data URL starting with data:image/")
        # A page of handwriting is a few hundred kilobytes at most. Anything far
        # larger is not a photo of a solution.
        if len(value) > 8_000_000:
            raise ValueError("image is too large to be a photo of a solution")
        return value

    @field_validator("run_results")
    @classmethod
    def _cap_run_results(cls, value: list[dict] | None) -> list[dict] | None:
        if value is None:
            return None
        # One task carries at most twelve tests; more than that is a client that
        # is not talking about this task.
        return value[:12]


class AnswerResponse(BaseModel):
    """FR-8.4: score with a breakdown and a short debrief."""

    id: str
    question_id: str
    raw_score: float
    final_score: float
    hint_penalty: float
    breakdown: dict
    feedback: str | None
    error_types: list[str] = []
    # FR-9.2: the new repetition interval.
    next_review_at: str | None = None
    interval_days: int | None = None
    topic_status: str | None = None
    #: What the memory system learned from this attempt — derived purely from the
    #: objective result (correctness, hints, difficulty), not from a self-declared
    #: confidence.
    mastery_score: float | None = None
    #: The right answer for a task that was answered wrongly, so the review screen
    #: can show it without a second request.
    expected_answer: str | None = None
    #: True when the verdict came from comparing values rather than from the model.
    #: The UI says so, because "the system checked this itself" and "a model read
    #: this" are different claims.
    checked_without_model: bool = False


class DisputeRequest(BaseModel):
    """FR-8.7: 'challenge this grade'."""

    note: str = Field(min_length=1, max_length=4000)


class ExplainRequest(BaseModel):
    """FR-7.1 explanation; FR-7.2 'explain differently' via ``style``."""

    style: str = "short"
    regenerate: bool = False
    # Interface language selected in settings ("ru" / "en"). The explanation is
    # written in it; when absent the user's profile language is used.
    language: str | None = Field(default=None, max_length=10)
    # The learner can name the uploaded file to write from, the same way they
    # name a source at all. ``None`` keeps the automatic keyword match so old
    # clients and the "explain all missing" batch behave as before.
    source_file: str | None = Field(default=None, max_length=500)
    # False means "write it from your own knowledge, no uploaded literature".
    # The topic description is still passed as the subject, but no passage is.
    ground: bool = True


class ExplainResponse(BaseModel):
    topic_id: str
    style: str
    text: str
    # The file the text was written from; null when no literature was used.
    source_label: str | None = None


class ComprehensionStartRequest(BaseModel):
    """Start (or resume) a comprehension check in the learner's interface language."""

    language: str | None = Field(default=None, max_length=10)


class ComprehensionAnswerRequest(BaseModel):
    """One free-recall, probe or check answer.

    The cap is generous enough for a paragraph of reasoning but small enough that
    a pasted document cannot become the whole context of the diagnosis.
    """

    text: str = Field(min_length=1, max_length=8000)
    language: str | None = Field(default=None, max_length=10)


class ComprehensionUnit(BaseModel):
    id: str
    label: str
    # understood | partial | missing | misconception | surface_memorization |
    # missing_prerequisite
    status: str | None = None
    # An exact excerpt of the learner's answer, when one supports the status.
    evidence: str | None = None
    # Why this status was assigned.
    why: str | None = None
    # One concrete consequence of applying this mental model to a new case.
    consequence: str | None = None
    # An exact excerpt of the material, never a paraphrase.
    source_quote: str | None = None


class ComprehensionGap(BaseModel):
    unit_id: str
    label: str
    kind: str
    why: str = ""
    #: The learner's exact words that show the discrepancy.
    evidence: str = ""
    #: An exact excerpt of the explanation/source that settles it.
    source_quote: str = ""
    consequence: str = ""


class ComprehensionReteach(BaseModel):
    unit_id: str
    title: str
    why_wrong: str = ""
    explanation: str
    example: str | None = None
    source_quote: str | None = None


class ComprehensionNext(BaseModel):
    # question: a probe or a re-teach check; reteach: the block to show before
    # the question; done: no further answer is expected.
    kind: str
    question: str | None = None
    reteach: ComprehensionReteach | None = None


class ComprehensionVerdict(BaseModel):
    summary: str
    # Computed from the fixed status weights in the service, never from a number
    # the model chose.
    score: float
    units: list[ComprehensionUnit] = []
    gaps: list[ComprehensionGap] = []
    # The unit this turn checked, so the UI can say "Теперь верно" after a
    # successful re-teach check without re-reading the whole diagnosis.
    target_unit_id: str | None = None
    target_status: str | None = None
    next_review_at: str | None = None
    interval_days: int | None = None


class ComprehensionStartResponse(BaseModel):
    session_id: str
    topic_id: str
    status: str
    step: int
    max_steps: int
    question: str
    resumed: bool = False
    #: A resumed session keeps the diagnosis and re-teach block from its last
    #: turn, so a reload does not drop the context of the question being asked.
    score: float = 0.0
    verdict: ComprehensionVerdict | None = None
    next: ComprehensionNext | None = None


class ComprehensionTurnResponse(BaseModel):
    session_id: str
    topic_id: str
    status: str
    step: int
    max_steps: int
    score: float
    verdict: ComprehensionVerdict | None = None
    next: ComprehensionNext | None = None


class ComprehensionLatestResponse(BaseModel):
    checked: bool = False
    has_explanation: bool = False
    session_id: str | None = None
    status: str | None = None
    score: float | None = None
    summary: str | None = None
    gaps: list[ComprehensionGap] = []
    next_review_at: str | None = None
    interval_days: int | None = None
    completed_at: str | None = None


class TopicNoteRequest(BaseModel):
    # "qa" by default: a note is for study, so it reads as questions with their
    # answers rather than a list of statements.
    note_format: str = "qa"


class TopicNoteResponse(BaseModel):
    """One topic note. Markdown, written from whatever the topic has."""

    topic_id: str
    topic_title: str
    goal_id: str
    markdown: str
    # True when the note was already saved and no model call was made.
    cached: bool
    # explanation | description | title — what the note was written from.
    written_from: str = "explanation"


class TopicNoteIndexItem(BaseModel):
    topic_id: str
    topic_title: str
    goal_id: str
    goal_topic: str
    course_id: str | None = None
    order_index: int = 0
    explanation_chars: int = 0
    # What a note here would be written from: explanation, description or title.
    note_source: str = "explanation"
    has_note: bool = False
    note_markdown: str | None = None


class TopicNoteIndexResponse(BaseModel):
    topics: list[TopicNoteIndexItem]


class TopicProgressItem(BaseModel):
    topic_id: str
    title: str
    strength: float
    status: str
    interval_days: int
    next_review_at: str | None
    repetitions: int
    lapses: int


class ProgressResponse(BaseModel):
    """FR-12.1/12.4: topic map, error analysis and readiness forecast."""

    goal_id: str
    topic_count: int
    topics: list[TopicProgressItem]
    by_status: dict[str, int]
    # FR-12.4
    forecast: dict
    # FR-12.3
    error_summary: dict | None = None


class ProfileResponse(BaseModel):
    """FR-1.2/FR-1.3."""

    timezone: str
    language: str
    preferred_study_time: str | None
    notifications_enabled: bool
    push_enabled: bool
    email_enabled: bool
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    reminder_interval_days: int
    missed_reminders: int
    gamification_enabled: bool


class UpdateProfileRequest(BaseModel):
    timezone: str | None = None
    language: str | None = None
    preferred_study_time: str | None = None
    notifications_enabled: bool | None = None
    push_enabled: bool | None = None
    email_enabled: bool | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    reminder_interval_days: int | None = Field(default=None, ge=1, le=30)
    gamification_enabled: bool | None = None


def goal_to_response(
    goal,
    *,
    topic_count: int = 0,
    course_name: str | None = None,
    progress=None,
    is_active: bool = False,
) -> GoalResponse:
    """Serialise a LearningGoal row.

    ``progress`` is an optional ``topic_flow.GoalProgress``. It stays optional so
    every other caller keeps working without one; without it the progress fields
    read as "nothing done yet", which is what a goal nobody has touched is.

    ``is_active`` marks the learner's chosen goal for the guided daily mode. It
    is passed in rather than read from the row because the choice lives on the
    user profile, not on the goal.
    """
    days_left = None
    if goal.deadline:
        from datetime import date as _date

        days_left = (goal.deadline - _date.today()).days
    return GoalResponse(
        id=str(goal.id),
        topic=goal.topic,
        deadline=goal.deadline.isoformat() if goal.deadline else None,
        minutes_per_day=goal.minutes_per_day,
        depth=goal.depth,
        status=goal.status,
        is_active=is_active,
        clarification_needed=goal.clarification_needed,
        completed_at=goal.completed_at.isoformat() if goal.completed_at else None,
        course_id=str(goal.course_id) if getattr(goal, "course_id", None) else None,
        course_name=course_name,
        topic_count=topic_count,
        days_left=days_left,
        created_at=goal.created_at.isoformat() if goal.created_at else None,
        updated_at=goal.updated_at.isoformat() if goal.updated_at else None,
        topics_done=progress.topics_done if progress else 0,
        exam_status=progress.exam_status if progress else "none",
        exam_score=progress.exam_score if progress else None,
        exam_passed=progress.exam_passed if progress else False,
        current_topic_title=progress.current_topic_title if progress else None,
        progress_percent=progress.progress_percent if progress else 0,
    )


def topic_to_response(topic, state=None, *, has_explanation: bool | None = None) -> TopicResponse:
    """Serialise a LearningTopic row, optionally with its state attached.

    ``has_explanation`` says whether the topic has a saved explanation, which is
    the only source a note or a quiz may be built from. It is passed in rather
    than looked up here so the caller can answer for the whole goal in one query.
    """
    return TopicResponse(
        id=str(topic.id),
        goal_id=str(topic.goal_id),
        title=topic.title,
        description=topic.description,
        estimated_minutes=topic.estimated_minutes,
        difficulty=topic.difficulty or "medium",
        order_index=topic.order_index,
        manual_status=topic.manual_status,
        strength=getattr(state, "strength", None),
        status=getattr(state, "status", None),
        next_review_at=state.next_review_at.isoformat() if getattr(state, "next_review_at", None) else None,
        has_explanation=has_explanation,
    )


# ── Unified review / inbox (FR-9.3 global) ──────────────────────────────────

class DueReviewItem(BaseModel):
    """One item that needs the learner's attention right now."""

    type: str
    # "review" | "wrong_answer" | "plan_item" | "video"

    goal_id: str
    goal_title: str

    topic_id: str | None = None
    topic_title: str | None = None

    title: str  # human-readable line
    due_at: str | None = None  # ISO datetime (reviews)
    day: str | None = None  # ISO date (plan items / deadlines)
    minutes: int | None = None
    reason: str | None = None
    status: str | None = None

    # What the frontend can do with this item.
    action: str | None = None  # "review" | "retry" | "continue" | "watch"
    action_href: str | None = None


class DueReviewResponse(BaseModel):
    """All due items across every goal of the learner, aggregated."""

    date: str  # today's date
    total: int
    items: list[DueReviewItem] = []


# ── Cascade replanning (FR-10.4 multi-goal) ──────────────────────────────────

class OverdueGoal(BaseModel):
    """A goal that has work falling behind its plan."""

    goal_id: str
    goal_title: str
    course_id: str | None = None
    overdue_count: int
    missed_count: int
    pending_past_count: int
    earliest_overdue_day: str | None = None  # ISO date


class CascadeReplanRequest(BaseModel):
    """Shift selected overdue goals forward by N days."""

    goal_ids: list[str]
    days_to_shift: int = Field(default=1, ge=1, le=30)


class CascadeReplanResult(BaseModel):
    """Result of shifting one goal's plan."""

    goal_id: str
    goal_title: str
    items_shifted: int
    new_first_day: str | None = None


class CascadeReplanResponse(BaseModel):
    """Shifted multiple goals and recorded the replan."""

    shifted: list[CascadeReplanResult]
    total_shifted: int