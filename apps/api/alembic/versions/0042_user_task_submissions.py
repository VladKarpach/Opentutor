"""Add user_task_submissions: AI-graded answers to user-uploaded tasks.

Each row records one learner answer to a UserTaskFile, the LLM verdict
(correct | partial | incorrect), feedback, and an optional remedial task
generated when the verdict is not fully correct.

Revision ID: 0042_user_task_submissions
Revises: 0041_user_content
Create Date: 2026-11-10
"""

from alembic import op
import sqlalchemy as sa

from models.compat import CompatJSONB, CompatUUID

revision = "0042_user_task_submissions"
down_revision = "0041_user_content"
branch_labels = None
depends_on = None

UUID = sa.String(36)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "user_task_submissions" not in existing:
        op.create_table(
            "user_task_submissions",
            sa.Column("id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("course_id", UUID, nullable=False),
            sa.Column("task_file_id", UUID, nullable=False),
            sa.Column("user_answer", sa.Text(), nullable=False),
            sa.Column(
                "submitted_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("verdict", sa.String(20), nullable=False),
            sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("feedback", sa.Text(), nullable=True),
            sa.Column("error_category", sa.String(50), nullable=True),
            sa.Column("remedial_task", CompatJSONB, nullable=True),
            sa.Column(
                "block_completed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["course_id"], ["courses.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["task_file_id"], ["user_task_files.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_user_task_submission_user_task",
            "user_task_submissions",
            ["user_id", "task_file_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "user_task_submissions" in existing:
        op.drop_table("user_task_submissions")
