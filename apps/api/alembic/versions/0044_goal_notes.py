"""Add notes column to learning_goals for per-goal summary / conspectus.

Learners can write a free-form note attached to a goal (e.g. a summary of what
they need to remember, key formulas, links). Stored directly on the goal row so
it travels with the goal and is always available on the goal page.

Revision ID: 0044_goal_notes
Revises: 0043_user_content_goal_id
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0044_goal_notes"
down_revision = "0043_user_content_goal_id"
branch_labels = None
depends_on = None

UUID = sa.String(36)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("learning_goals")}
    if "notes" not in columns:
        op.add_column(
            "learning_goals",
            sa.Column("notes", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("learning_goals")}
    if "notes" in columns:
        with op.batch_alter_table("learning_goals") as batch:
            batch.drop_column("notes")
