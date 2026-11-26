"""Add plan_revisions: what a replan changed and why (FR-10.4).

A per-item ``rejected`` flag exists, but the previous rows are deleted when the
plan is rebuilt, so by the time the learner is asked there is nothing left to
show or restore. This keeps the diff, its reasons and the decision.

Revision ID: 0035_plan_revisions
Revises: 0034_topic_drills
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_plan_revisions"
down_revision = "0034_topic_drills"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    if "plan_revisions" in set(sa.inspect(op.get_bind()).get_table_names()):
        return

    op.create_table(
        "plan_revisions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("goal_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("trigger", sa.String(30), nullable=False, server_default="replan"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_plan_revision_goal_status", "plan_revisions", ["goal_id", "status"]
    )


def downgrade() -> None:
    if "plan_revisions" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_table("plan_revisions")