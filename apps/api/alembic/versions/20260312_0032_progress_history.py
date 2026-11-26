"""Add strength history and error summaries (TZ FR-12.1, FR-12.3).

Two tables:
- ``topic_strength_points`` keeps one row per graded answer so the
  strength-over-time chart is exact (section 8 only stores the current value).
- ``error_summaries`` is the ErrorSummary entity named in TZ section 8.

Revision ID: 0032_progress_history
Revises: 0031_exams
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0032_progress_history"
down_revision = "0031_exams"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "topic_strength_points" not in existing:
        op.create_table(
            "topic_strength_points",
            sa.Column("id", UUID, nullable=False),
            sa.Column("goal_id", UUID, nullable=False),
            sa.Column("topic_id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("strength", sa.Float(), nullable=False),
            sa.Column("interval_days", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("was_lapse", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "recorded_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_strength_point_goal_time",
            "topic_strength_points",
            ["goal_id", "recorded_at"],
        )
        op.create_index(
            "ix_strength_point_topic",
            "topic_strength_points",
            ["topic_id", "recorded_at"],
        )

    if "error_summaries" not in existing:
        op.create_table(
            "error_summaries",
            sa.Column("id", UUID, nullable=False),
            sa.Column("goal_id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("summary_text", sa.Text(), nullable=True),
            sa.Column("stats", sa.JSON(), nullable=True),
            sa.Column("recommendations", sa.JSON(), nullable=True),
            sa.Column("answer_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "generated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_error_summary_goal_period",
            "error_summaries",
            ["goal_id", "period_end"],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "error_summaries" in existing:
        op.drop_table("error_summaries")
    if "topic_strength_points" in existing:
        op.drop_table("topic_strength_points")