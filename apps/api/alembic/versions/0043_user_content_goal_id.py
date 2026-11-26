"""Make course_id optional and add goal_id to user task files / submissions.

Lets a learner upload assignments directly inside a goal's "Задания" tab even
when the goal has no course attached. When goal_id is given but course_id is
not, the course is resolved from the goal server-side.

Revision ID: 0043_user_content_goal_id
Revises: 0042_user_task_submissions
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa

from models.compat import CompatUUID

revision = "0043_user_content_goal_id"
down_revision = "0042_user_task_submissions"
branch_labels = None
depends_on = None

UUID = sa.String(36)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # user_task_files: make course_id nullable, add goal_id column + index
    if "user_task_files" in existing:
        cols = {c["name"] for c in inspector.get_columns("user_task_files")}
        if "course_id" in cols:
            with op.batch_alter_table("user_task_files") as batch:
                batch.alter_column(
                    "course_id",
                    existing_type=UUID,
                    nullable=True,
                )
        if "goal_id" not in cols:
            with op.batch_alter_table("user_task_files") as batch:
                batch.add_column(
                    sa.Column(
                        "goal_id",
                        UUID,
                        nullable=True,
                    )
                )
                batch.create_foreign_key(
                    "fk_user_task_file_goal",
                    "learning_goals",
                    ["goal_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
        idx_names = {ix["name"] for ix in inspector.get_indexes("user_task_files")}
        if "ix_user_task_file_user_goal" not in idx_names:
            op.create_index(
                "ix_user_task_file_user_goal",
                "user_task_files",
                ["user_id", "goal_id"],
            )

    # user_task_submissions: make course_id nullable, add goal_id column + index
    if "user_task_submissions" in existing:
        cols = {c["name"] for c in inspector.get_columns("user_task_submissions")}
        if "course_id" in cols:
            with op.batch_alter_table("user_task_submissions") as batch:
                batch.alter_column(
                    "course_id",
                    existing_type=UUID,
                    nullable=True,
                )
        if "goal_id" not in cols:
            with op.batch_alter_table("user_task_submissions") as batch:
                batch.add_column(
                    sa.Column(
                        "goal_id",
                        UUID,
                        nullable=True,
                    )
                )
                batch.create_foreign_key(
                    "fk_user_task_submission_goal",
                    "learning_goals",
                    ["goal_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
        idx_names = {ix["name"] for ix in inspector.get_indexes("user_task_submissions")}
        if "ix_user_task_submission_user_goal" not in idx_names:
            op.create_index(
                "ix_user_task_submission_user_goal",
                "user_task_submissions",
                ["user_id", "goal_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "user_task_submissions" in existing:
        idx_names = {ix["name"] for ix in inspector.get_indexes("user_task_submissions")}
        if "ix_user_task_submission_user_goal" in idx_names:
            op.drop_index("ix_user_task_submission_user_goal", "user_task_submissions")
        cols = {c["name"] for c in inspector.get_columns("user_task_submissions")}
        if "goal_id" in cols:
            with op.batch_alter_table("user_task_submissions") as batch:
                batch.drop_constraint(
                    "fk_user_task_submission_goal", type_="foreignkey"
                )
                batch.drop_column("goal_id")
        with op.batch_alter_table("user_task_submissions") as batch:
            batch.alter_column("course_id", existing_type=UUID, nullable=False)

    if "user_task_files" in existing:
        idx_names = {ix["name"] for ix in inspector.get_indexes("user_task_files")}
        if "ix_user_task_file_user_goal" in idx_names:
            op.drop_index("ix_user_task_file_user_goal", "user_task_files")
        cols = {c["name"] for c in inspector.get_columns("user_task_files")}
        if "goal_id" in cols:
            with op.batch_alter_table("user_task_files") as batch:
                batch.drop_constraint("fk_user_task_file_goal", type_="foreignkey")
                batch.drop_column("goal_id")
        with op.batch_alter_table("user_task_files") as batch:
            batch.alter_column("course_id", existing_type=UUID, nullable=False)
