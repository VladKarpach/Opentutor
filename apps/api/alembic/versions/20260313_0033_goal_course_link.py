"""Link learning goals to a course workspace (variant A of the integration).

The TZ has no notion of a course, but this app is course-based, so a goal can
now hang off a course to reuse its materials and ingestion tree.

``course_id`` is nullable and uses ON DELETE SET NULL: deleting a course must
not take a study plan full of repetition history with it.

Revision ID: 0033_goal_course_link
Revises: 0032_progress_history
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0033_goal_course_link"
down_revision = "0032_progress_history"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "learning_goals" not in tables or "courses" not in tables:
        return

    columns = {c["name"] for c in inspector.get_columns("learning_goals")}
    if "course_id" in columns:
        return

    with op.batch_alter_table("learning_goals") as batch:
        batch.add_column(sa.Column("course_id", UUID, nullable=True))
        batch.create_foreign_key(
            "fk_learning_goal_course",
            "courses",
            ["course_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_learning_goal_course", ["course_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "learning_goals" not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns("learning_goals")}
    if "course_id" not in columns:
        return

    with op.batch_alter_table("learning_goals") as batch:
        batch.drop_index("ix_learning_goal_course")
        batch.drop_constraint("fk_learning_goal_course", type_="foreignkey")
        batch.drop_column("course_id")