"""Add user-owned content tables: theory links and task files.

The learner can pin documentation/theory URLs to a course block and upload their
own task/assignment files. Both tables are scoped to (user, course) and are
independent of agent-generated assets.

Revision ID: 0041_user_content
Revises: 0040_active_learning_goal
Create Date: 2026-11-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0041_user_content"
down_revision = "0040_active_learning_goal"
branch_labels = None
depends_on = None

UUID = sa.String(36)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "user_theory_links" not in existing:
        op.create_table(
            "user_theory_links",
            sa.Column("id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("course_id", UUID, nullable=False),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("url", sa.String(1000), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("source_type", sa.String(30), nullable=False, server_default="documentation"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_user_theory_link_user_course",
            "user_theory_links",
            ["user_id", "course_id"],
        )

    if "user_task_files" not in existing:
        op.create_table(
            "user_task_files",
            sa.Column("id", UUID, nullable=False),
            sa.Column("user_id", UUID, nullable=False),
            sa.Column("course_id", UUID, nullable=False),
            sa.Column("filename", sa.String(300), nullable=False),
            sa.Column("file_path", sa.String(1000), nullable=False),
            sa.Column("mime_type", sa.String(100), nullable=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_user_task_file_user_course",
            "user_task_files",
            ["user_id", "course_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "user_task_files" in existing:
        op.drop_table("user_task_files")
    if "user_theory_links" in existing:
        op.drop_table("user_theory_links")
