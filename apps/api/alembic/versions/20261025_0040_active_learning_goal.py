"""Remember which learning goal the guided daily mode should open.

Several goals may have status "active" at once. "Сегодня" used to guess by
nearest deadline, which made it unclear which goal was actually being worked
on. The choice is now explicit and stored on the learner's profile; no goal row
is changed, so pausing or deleting the selected goal is handled by clearing the
pointer rather than by rewriting statuses.

Revision ID: 0040_active_learning_goal
Revises: 0039_comprehension_sessions
Create Date: 2026-10-25
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_active_learning_goal"
down_revision = "0039_comprehension_sessions"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_profiles" not in set(inspector.get_table_names()):
        return

    columns = {c["name"] for c in inspector.get_columns("user_profiles")}
    if "active_goal_id" in columns:
        return

    op.add_column(
        "user_profiles",
        sa.Column("active_goal_id", UUID, nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_profiles" not in set(inspector.get_table_names()):
        return

    columns = {c["name"] for c in inspector.get_columns("user_profiles")}
    if "active_goal_id" not in columns:
        return

    with op.batch_alter_table("user_profiles") as batch:
        batch.drop_column("active_goal_id")
