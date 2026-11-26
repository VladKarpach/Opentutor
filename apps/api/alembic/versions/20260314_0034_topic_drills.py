"""Add topic_drills: the required practice series behind one subtopic (FR-8).

Each plan subtopic gets a short set of tasks with a rising difficulty; the exam
is gated on completing every set in the goal.

Revision ID: 0034_topic_drills
Revises: 0033_goal_course_link
Create Date: 2026-03-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0034_topic_drills"
down_revision = "0033_goal_course_link"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    if "topic_drills" in set(sa.inspect(op.get_bind()).get_table_names()):
        return

    op.create_table(
        "topic_drills",
        sa.Column("id", UUID, nullable=False),
        sa.Column("goal_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("topic_id", UUID, nullable=False),
        sa.Column("required_count", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("question_ids", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One series per subtopic per goal: re-preparing must replace, not duplicate.
        sa.UniqueConstraint("goal_id", "topic_id", name="uq_topic_drill_goal_topic"),
    )
    op.create_index("ix_topic_drill_goal_status", "topic_drills", ["goal_id", "status"])


def downgrade() -> None:
    if "topic_drills" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_table("topic_drills")