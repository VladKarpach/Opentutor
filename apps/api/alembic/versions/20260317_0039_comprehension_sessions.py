"""Add comprehension_sessions: a dialogue that checks whether a topic was understood.

The existing task flow grades one answer. This table keeps the state of a
different interaction: a free-recall answer, a targeted probe when the answer is
incomplete, one or two re-teach cycles focused on a single weak unit, and the
final diagnosis. The state has to survive a page reload, and the score must be
computed from the unit statuses rather than stored as a number the model chose.

Revision ID: 0039_comprehension_sessions
Revises: 0038_task_formats
Create Date: 2026-10-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0039_comprehension_sessions"
down_revision = "0038_task_formats"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    if "comprehension_sessions" in set(sa.inspect(op.get_bind()).get_table_names()):
        return

    op.create_table(
        "comprehension_sessions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("goal_id", UUID, nullable=False),
        sa.Column("topic_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="in_progress"),
        sa.Column("language", sa.String(10), nullable=False, server_default="ru"),
        sa.Column("material_hash", sa.String(64), nullable=False),
        sa.Column("source_label", sa.String(300), nullable=True),
        sa.Column("material", sa.Text(), nullable=False, server_default=""),
        sa.Column("units", sa.JSON(), nullable=True),
        sa.Column("turns", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_comprehension_session_user_topic",
        "comprehension_sessions",
        ["user_id", "topic_id", "created_at"],
    )
    op.create_index(
        "ix_comprehension_session_topic_status",
        "comprehension_sessions",
        ["topic_id", "status"],
    )


def downgrade() -> None:
    if "comprehension_sessions" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_table("comprehension_sessions")
