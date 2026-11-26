"""Adaptive learning models — TZ section 8.

SQLite-only, following the ``Compat*`` conventions in ``models/compat.py``.

Deliberate design choices:

- Profile settings live in a separate ``user_profiles`` table rather than as
  new columns on ``users``. Local SQLite installs bootstrap via
  ``Base.metadata.create_all()``, which does not add columns to a table that
  already exists, so extending ``users`` would leave existing installs without
  the new fields. A new table is created correctly in both cases.
- No ``group_id`` FK on Goal yet: group learning is P3 in the TZ (section 17)
  and requires a multi-user rework this project has not made. The column is
  kept as plain JSON metadata so the shape is already there when groups land.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.compat import CompatJSONB, CompatUUID


class UserProfile(Base):
    """FR-1.2 / FR-1.3: profile and notification preferences."""

    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # FR-1.2
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    language: Mapped[str] = mapped_column(String(10), default="ru")
    preferred_study_time: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # morning | afternoon | evening | night | anytime

    # FR-1.3
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quiet_hours_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Hour of day, 0-23. NULL means "no quiet period".
    reminder_interval_days: Mapped[int] = mapped_column(Integer, default=1)

    # FR-13.3: the reminder frequency backs off when ignored.
    missed_reminders: Mapped[int] = mapped_column(Integer, default=0)

    # FR-14.5: gamification can be switched off entirely.
    gamification_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # The goal the learner chose for the guided daily mode. Several goals can be
    # "active" in the status sense at once; this is the one whose plan "Сегодня"
    # opens. It lives here because exactly one goal can be selected per learner.
    # No FK on purpose: SQLite local installs add this column with ALTER TABLE,
    # and a stale id is harmless because every read re-checks the goal exists and
    # is still active.
    active_goal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LearningGoal(Base):
    """TZ Goal: topic + deadline + available time.

    FR-2.1: topic (free text), deadline, hours per day, desired depth.
    FR-2.3: group_id stays in metadata_json until group support (P3) exists.

    ``course_id`` links a goal to a course workspace. The TZ never mentions
    courses, but this app is course-based, so a goal can hang off a course to
    pick up its materials and ingestion tree (TZ FR-3). It is nullable on
    purpose: a goal created straight from "Цели обучения" has no course, and
    those rows must keep working.
    """

    __tablename__ = "learning_goals"
    __table_args__ = (
        Index("ix_learning_goal_user_status", "user_id", "status"),
        Index("ix_learning_goal_course", "course_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        # SET NULL, not CASCADE: deleting a course must not destroy a study plan
        # that may hold months of spaced-repetition history. The goal survives as
        # a standalone one and keeps working.
        CompatUUID,
        ForeignKey("courses.id", ondelete="SET NULL"),
        nullable=True,
    )

    topic: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    minutes_per_day: Mapped[int] = mapped_column(Integer, default=60)

    # FR-2.1 depth: overview | confident | deep
    depth: Mapped[str] = mapped_column(String(20), default="confident")

    status: Mapped[str] = mapped_column(String(20), default="active")
    # active | paused | completed | archived

    # FR-2.2: the AI can propose a clarification of a too-broad topic.
    clarification_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    clarification_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", CompatJSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    topics = relationship("LearningTopic", back_populates="goal", cascade="all, delete-orphan")


class LearningTopic(Base):
    """TZ Topic: the unit of both planning and checking (FR glossary)."""

    __tablename__ = "learning_topics"
    __table_args__ = (
        Index("ix_learning_topic_goal_order", "goal_id", "order_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=30)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    # Difficulty weight: easy | medium | hard. Affects planned time per topic.
    # Existing rows default to medium so current plans keep their behaviour.
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")

    # FR-5.6: the learner may mark a topic as known or skipped by hand.
    manual_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # known | skip | NULL

    # FR-4.7: a diagnostic question may be grounded in uploaded material.
    source_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("course_content_tree.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    goal = relationship("LearningGoal", back_populates="topics")


class TopicDependency(Base):
    """TZ TopicDependency: the prerequisite edge of the DAG (FR-5.1)."""

    __tablename__ = "topic_dependencies"
    __table_args__ = (
        UniqueConstraint("topic_id", "prerequisite_id", name="uq_topic_dependency"),
        Index("ix_topic_dependency_prerequisite", "prerequisite_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False
    )
    prerequisite_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False
    )
    # Confidence in this edge, so a weak guess can be reviewed (FR-5.1).
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TopicState(Base):
    """TZ TopicState: strength, intervals and review bookkeeping (FR-9.1)."""

    __tablename__ = "topic_states"
    __table_args__ = (
        UniqueConstraint("goal_id", "user_id", "topic_id", name="uq_topic_state"),
        Index("ix_topic_state_next_review", "user_id", "next_review_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False
    )

    # Section 6.1 / glossary: strength in [0, 1] plus the estimate uncertainty.
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    uncertainty: Mapped[float] = mapped_column(Float, default=1.0)

    last_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    stability_days: Mapped[float] = mapped_column(Float, default=0.0)

    # Dynamic difficulty relative to the initial estimate: 0.5 = the learner is
    # doing as expected, >0.5 = harder than expected, <0.5 = easier. Starts
    # neutral and is updated from graded answers (and card FSRS data at plan
    # time). NULL = no evidence yet, no adaptive adjustment.
    fsrs_difficulty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="not_started")
    # not_started | weak | learning | known

    # FR-8.5: last self-reported confidence.
    confidence_label: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LearningPlanItem(Base):
    """TZ PlanItem: a scheduled unit of work (FR-6.5)."""

    __tablename__ = "learning_plan_items"
    __table_args__ = (
        Index("ix_learning_plan_goal_day", "goal_id", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=True
    )

    item_type: Mapped[str] = mapped_column(String(20), default="study")
    # study | review | practice | exam

    day: Mapped[date] = mapped_column(Date, nullable=False)
    planned_minutes: Mapped[int] = mapped_column(Integer, default=0)
    # FR-7.4: actual time spent, feeds the speed coefficient (FR-10.3).
    actual_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | done | skipped | missed

    # FR-10.4: the learner is told what changed and why, and may reject it.
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LearningQuestion(Base):
    """TZ Question: task text plus the criteria fixed at creation time.

    Section 6.2: "criteria are formed when the question is created, so that the
    grading stays stable." They are therefore stored here, not invented at
    grading time.
    """

    __tablename__ = "learning_questions"
    __table_args__ = (
        Index("ix_learning_question_topic", "topic_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False
    )

    # FR-8.3 task types. FR-8.10 also asks for contextual and follow-up tasks,
    # which is why a parent question is modelled.
    question_type: Mapped[str] = mapped_column(String(30), default="explain")
    # explain_own_words | find_error | fill_gap | mini_project | compare |
    # explain_to_learner | apply_situation | choose_justify

    text: Mapped[str] = mapped_column(Text, nullable=False)
    criteria: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # {"correctness": "...", "completeness": "...", "understanding": "..."}

    # How the learner replies, which is a different question from question_type
    # above: a "find the error" task can be prose or code, and a "write the
    # function" task is code however it is phrased. Only the format decides how the
    # answer is graded. See services/adaptive_learning/task_formats.py.
    format: Mapped[str] = mapped_column(String(24), default="text", index=True)
    # text | code | number | single_choice | multi_choice | true_false |
    # ordering | matching
    payload: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # Format-specific data: options and the correct one, a number and its
    # tolerance, starter code and test cases, steps to order, pairs to match.
    # Everything the deterministic grader needs lives here, so grading a
    # single-choice answer costs no model call.

    difficulty: Mapped[float] = mapped_column(Float, default=0.5)
    # FR-3.4 / criterion 17: reference to the material fragment used.
    source_chunk_id: Mapped[Optional[uuid.UUID]] = mapped_column(CompatUUID, nullable=True)
    source_label: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # FR-8.6: three hint levels, hint -> direction -> almost the solution.
    hints: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)

    parent_question_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("learning_questions.id", ondelete="SET NULL"), nullable=True
    )
    is_diagnostic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearningAnswer(Base):
    """TZ Answer: the graded attempt (FR-8.4, FR-8.5, FR-8.7)."""

    __tablename__ = "learning_answers"
    __table_args__ = (
        Index("ix_learning_answer_question_user", "question_id", "user_id"),
        Index("ix_learning_answer_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_questions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    raw_score: Mapped[float] = mapped_column(Float, default=0.0)
    # raw_score is before the hint penalty; score is section 6.2's
    # final = score * (1 - hint_penalty).

    breakdown: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # {"correctness": 0.8, "completeness": 0.6, "understanding": 0.5}

    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Section 6.6: error taxonomy used by the error analysis.
    error_types: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # concept_confusion | missing_step | wrong_application | incomplete

    # FR-8.5 / FR-8.6 / FR-8.7 / FR-8.10
    confidence_label: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    answer_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    dispute_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Kept so answer quality can be improved later (FR-8.7, TZ section 16).

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DiagnosticSessionRecord(Base):
    """TZ DiagnosticSession: the dialogue transcript and its outcome (FR-4)."""

    __tablename__ = "diagnostic_sessions"
    __table_args__ = (
        Index("ix_diagnostic_session_goal", "goal_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    turns: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # [{"turn_index": 0, "subtopic_id": "...", "question": "...", "answer": "..."}]

    final_estimates: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # {topic_id: {"estimate": 0.62, "band": "working_knowledge"}}

    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    # in_progress | completed | skipped

    # FR-4.6: the learner may redo the diagnostic later, so runs are not
    # overwritten; the latest one wins when reading.
    finished_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # Working state of the deterministic estimator, so a dialogue resumes exactly
    # instead of being replayed (see DiagnosticSession.snapshot).
    metadata_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Exam(Base):
    """TZ Exam: a run over the whole goal (section 8, FR-11).

    FR-11.1: the exam is composed from all topics, weighted towards the weak
    ones. FR-11.2: two modes — "training" (hints allowed) and "strict" (with a
    timer and no hints).
    """

    __tablename__ = "exams"
    __table_args__ = (
        Index("ix_exam_goal_status", "goal_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    mode: Mapped[str] = mapped_column(String(20), default="training")
    # training | strict

    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    # in_progress | completed | abandoned

    # FR-11.2: a wall-clock budget for the whole exam, in seconds.
    # Training mode has no limit; strict mode does.
    time_limit_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    question_ids: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # Ordered ids of LearningQuestion, as served to the learner.

    answers: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # [{"question_id": ..., "text": ..., "score": ..., "hints_used": ...}]

    results: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # Per-topic scores plus the overall mark, filled in on completion.

    total_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    answered_count: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TopicStrengthPoint(Base):
    """FR-12.1: one dot on the strength-over-time chart.

    The TZ asks for a graph of knowledge strength over time, but section 8 only
    stores the *current* strength on TopicState. Keeping a point per graded
    answer makes the graph exact rather than approximated from the answer log.

    Appended by the same deterministic path that updates TopicState, so the
    chart can never disagree with the topic map on the same screen.
    """

    __tablename__ = "topic_strength_points"
    __table_args__ = (
        Index("ix_strength_point_goal_time", "goal_id", "recorded_at"),
        Index("ix_strength_point_topic", "topic_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    strength: Mapped[float] = mapped_column(Float, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    # FR-9.5: a lapse ("подзабыл тему") is what the graph should show as a drop.
    was_lapse: Mapped[bool] = mapped_column(Boolean, default=False)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TopicDrill(Base):
    """The required set of practice tasks for one subtopic.

    The practice flow is: the plan says what to study, each subtopic carries a
    short series of tasks with a rising difficulty, the learner works through
    them, and only a completed set across the goal opens the exam.

    ``required_count`` is 3 for confident/deep goals and 2 for an overview, so
    "2-3 tasks per topic" is data rather than a constant buried in code.
    ``question_ids`` keeps the generated order, which is also the difficulty
    ramp, so the UI can present the tasks in escalating order.
    """

    __tablename__ = "topic_drills"
    __table_args__ = (
        UniqueConstraint("goal_id", "topic_id", name="uq_topic_drill_goal_topic"),
        Index("ix_topic_drill_goal_status", "goal_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False
    )

    required_count: Mapped[int] = mapped_column(Integer, default=3)
    question_ids: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # Ordered, easiest first.

    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | in_progress | completed

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PlanRevision(Base):
    """One recorded replan and what it actually changed — FR-10.4.

    The TZ asks the learner to see *what* changed and *why* ("добавлено 30 минут
    повторения темы X"), and to be able to decline it. A per-item ``rejected``
    flag is not enough on its own: by the time the plan is rebuilt the previous
    rows are gone, so there is nothing left to show or to restore. This keeps the
    diff, the reason for each change, and the decision.
    """

    __tablename__ = "plan_revisions"
    __table_args__ = (
        Index("ix_plan_revision_goal_status", "goal_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    trigger: Mapped[str] = mapped_column(String(30), default="replan")
    # replan | answer | settings | goal_change | catch_up | manual

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # One human sentence describing the headline change.

    changes: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # [{"kind","topic_id","topic_title","item_type","before","after",
    #   "delta_minutes","reason"}]

    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | accepted | rejected

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ErrorSummary(Base):
    """TZ section 8, FR-12.3: the periodic error analysis.

    Section 6.6: every N graded answers the model receives aggregated statistics
    and writes a short summary with recommendations. The statistics themselves
    are counted in code; only the wording comes from the model.
    """

    __tablename__ = "error_summaries"
    __table_args__ = (
        Index("ix_error_summary_goal_period", "goal_id", "period_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The model's wording. Nullable so the deterministic counts are still
    # available when the LLM call failed.

    stats: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # {"error_counts": ..., "dominant_error": ..., "mean_score": ...,
    #  "by_topic": ..., "total": ...}

    recommendations: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    answer_count: Mapped[int] = mapped_column(Integer, default=0)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TopicResource(Base):
    """A link the learner put beside a topic — a video to watch first.

    Deliberately not generated and deliberately not searched for. Asking a model
    for a video URL produces a well-formed address for a video that does not
    exist, which is worse than offering nothing: it looks right and fails on
    click. So the links are the learner's own, entered by hand, and the row
    records only what they typed.

    ``kind`` is a plain column rather than an enum for the same reason
    ``asset_type`` is: one more kind should not need a migration, and this table
    starts with exactly one.
    """

    __tablename__ = "topic_resources"
    __table_args__ = (
        Index("ix_topic_resource_topic", "topic_id", "order_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[str] = mapped_column(String(20), default="video", nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ComprehensionSession(Base):
    """One free-recall check of whether a topic was actually understood.

    This is deliberately a separate table rather than a ``LearningQuestion`` row.
    The check is a short dialogue with its own state machine — recall, probe,
    re-teach, check — and it must survive a page reload. Modelling it as a task
    would mean stuffing that state into ``LearningQuestion.payload`` and joining
    it back together on every turn.

    ``units`` is the decomposition of the material the learner read. Each unit
    carries a status after every answer (understood / partial / misconception /
    ...), and the score is computed from those fixed weights in
    ``services/adaptive_learning/comprehension.py``. A model is never allowed to
    choose the number: it diagnoses, code scores.
    """

    __tablename__ = "comprehension_sessions"
    __table_args__ = (
        Index(
            "ix_comprehension_session_user_topic",
            "user_id",
            "topic_id",
            "created_at",
        ),
        Index("ix_comprehension_session_topic_status", "topic_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    # in_progress | passed | needs_review | abandoned

    language: Mapped[str] = mapped_column(String(10), default="ru")
    # The language the learner read in, so a resumed session keeps its wording.

    # A hash lets ``start`` resume an unfinished check of the same explanation
    # instead of opening a second one, while a regenerated explanation correctly
    # starts a fresh check.
    material_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_label: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # The exact text this check was written from. Kept so a resumed or delayed
    # answer is diagnosed against what the learner actually read, not against an
    # explanation that was regenerated in the meantime.
    material: Mapped[str] = mapped_column(Text, nullable=False, default="")

    units: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # [{"id","label","expectation","source_quote","common_misconception",
    #   "transfer_question","status","evidence","why"}]

    turns: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # [{"index","phase","question","answer","created_at"}]

    result: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # The state machine and the last verdict: {"phase","reteach_cycles",
    # "target_unit_id","next","verdict","gaps","summary","finalized",...}

    score: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )