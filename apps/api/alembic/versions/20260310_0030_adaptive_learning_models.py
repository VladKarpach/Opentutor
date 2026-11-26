"""Add adaptive learning tables (TZ section 8).

Implements the Goal / Topic / TopicDependency / TopicState / PlanItem / Question /
Answer / DiagnosticSession model set plus user profile settings.

Note on SQLite: local installs bootstrap through
``Base.metadata.create_all()`` (see services/app_lifecycle.py), which does not
add columns to existing tables but does create missing ones. That is why profile
fields live in a new ``user_profiles`` table instead of new ``users`` columns.

Revision ID: 0030_adaptive_learning
Revises: 2870051cd576
Create Date: 2026-03-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_adaptive_learning"
down_revision = "2870051cd576"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def _existing() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _existing()

    if "user_profiles" not in tables:
        op.create_table(
            "user_profiles",
            sa.Column("id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            sa.Column("language", sa.String(10), nullable=False, server_default="ru"),
            sa.Column("preferred_study_time", sa.String(20), nullable=True),
            sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("quiet_hours_start", sa.Integer(), nullable=True),
            sa.Column("quiet_hours_end", sa.Integer(), nullable=True),
            sa.Column("reminder_interval_days", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("missed_reminders", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("gamification_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_profiles_user", "user_profiles", ["user_id"], unique=True)

    if "learning_goals" not in tables:
        op.create_table(
            "learning_goals",
            sa.Column("id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("topic", sa.Text(), nullable=False),
            sa.Column("deadline", sa.Date(), nullable=True),
            sa.Column("minutes_per_day", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("depth", sa.String(20), nullable=False, server_default="confident"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("clarification_needed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("clarification_prompt", sa.Text(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_learning_goal_user_status", "learning_goals", ["user_id", "status"])

    if "learning_topics" not in tables:
        op.create_table(
            "learning_topics",
            sa.Column("id", UUID, nullable=False),
            sa.Column("goal_id", UUID, nullable=False),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("estimated_minutes", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("manual_status", sa.String(20), nullable=True),
            sa.Column("source_node_id", UUID, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["source_node_id"], ["course_content_tree.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_learning_topic_goal_order", "learning_topics", ["goal_id", "order_index"])

    if "topic_dependencies" not in tables:
        op.create_table(
            "topic_dependencies",
            sa.Column("id", UUID, nullable=False),
            sa.Column("topic_id", UUID, nullable=False),
            sa.Column("prerequisite_id", UUID, nullable=False),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["prerequisite_id"], ["learning_topics.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("topic_id", "prerequisite_id", name="uq_topic_dependency"),
        )
        op.create_index("ix_topic_dependency_prerequisite", "topic_dependencies", ["prerequisite_id"])

    if "topic_states" not in tables:
        op.create_table(
            "topic_states",
            sa.Column("id", UUID, nullable=False),
            sa.Column("goal_id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("topic_id", UUID, nullable=False),
            sa.Column("strength", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("uncertainty", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("last_review_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("interval_days", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("repetitions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lapses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stability_days", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="not_started"),
            sa.Column("confidence_label", sa.String(20), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("goal_id", "user_id", "topic_id", name="uq_topic_state"),
        )
        op.create_index("ix_topic_state_next_review", "topic_states", ["user_id", "next_review_at"])

    if "learning_plan_items" not in tables:
        op.create_table(
            "learning_plan_items",
            sa.Column("id", UUID, nullable=False),
            sa.Column("goal_id", UUID, nullable=False),
            sa.Column("topic_id", UUID, nullable=True),
            sa.Column("item_type", sa.String(20), nullable=False, server_default="study"),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("planned_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actual_minutes", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_learning_plan_goal_day", "learning_plan_items", ["goal_id", "day"])

    if "learning_questions" not in tables:
        op.create_table(
            "learning_questions",
            sa.Column("id", UUID, nullable=False),
            sa.Column("topic_id", UUID, nullable=False),
            sa.Column("question_type", sa.String(30), nullable=False, server_default="explain"),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("criteria", sa.JSON(), nullable=True),
            sa.Column("difficulty", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("source_chunk_id", UUID, nullable=True),
            sa.Column("source_label", sa.String(300), nullable=True),
            sa.Column("hints", sa.JSON(), nullable=True),
            sa.Column("parent_question_id", UUID, nullable=True),
            sa.Column("is_diagnostic", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["parent_question_id"], ["learning_questions.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_learning_question_topic", "learning_questions", ["topic_id"])

    if "learning_answers" not in tables:
        op.create_table(
            "learning_answers",
            sa.Column("id", UUID, nullable=False),
            sa.Column("question_id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("raw_score", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("breakdown", sa.JSON(), nullable=True),
            sa.Column("feedback", sa.Text(), nullable=True),
            sa.Column("error_types", sa.JSON(), nullable=True),
            sa.Column("confidence_label", sa.String(20), nullable=True),
            sa.Column("hints_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("answer_time_ms", sa.Integer(), nullable=True),
            sa.Column("disputed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("dispute_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["question_id"], ["learning_questions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_learning_answer_question_user", "learning_answers", ["question_id", "user_id"]
        )
        op.create_index("ix_learning_answer_user_created", "learning_answers", ["user_id", "created_at"])

    if "diagnostic_sessions" not in tables:
        op.create_table(
            "diagnostic_sessions",
            sa.Column("id", UUID, nullable=False),
            sa.Column("goal_id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("turns", sa.JSON(), nullable=True),
            sa.Column("final_estimates", sa.JSON(), nullable=True),
            sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="in_progress"),
            sa.Column("finished_reason", sa.String(40), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_diagnostic_session_goal", "diagnostic_sessions", ["goal_id", "created_at"])


def downgrade() -> None:
    tables = _existing()
    # Children first so foreign keys stay valid while dropping.
    for name in (
        "diagnostic_sessions",
        "learning_answers",
        "learning_questions",
        "learning_plan_items",
        "topic_states",
        "topic_dependencies",
        "learning_topics",
        "learning_goals",
        "user_profiles",
    ):
        if name in tables:
            op.drop_table(name)