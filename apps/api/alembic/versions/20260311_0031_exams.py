"""Add exams table (TZ section 8, FR-11).

Stores a run over a whole goal: which questions were served, the answers, and
the per-topic breakdown.

Revision ID: 0031_exams
Revises: 0030_adaptive_learning
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0031_exams"
down_revision = "0030_adaptive_learning"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    if "exams" in set(sa.inspect(op.get_bind()).get_table_names()):
        return

    op.create_table(
        "exams",
        sa.Column("id", UUID, nullable=False),
        sa.Column("goal_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="training"),
        sa.Column("status", sa.String(20), nullable=False, server_default="in_progress"),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("question_ids", sa.JSON(), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=True),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column("answered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exam_goal_status", "exams", ["goal_id", "status"])


def downgrade() -> None:
    if "exams" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_table("exams")