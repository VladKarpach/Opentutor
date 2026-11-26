"""Add video_channels: the sources lesson videos may be drawn from.

The learner picks the channels once; the system only chooses out of them. A
nullable ``course_id`` carries the scope in one table: NULL is global, set is
that course only.

Revision ID: 0037_video_channels
Revises: 0036_topic_resources
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0037_video_channels"
down_revision = "0036_topic_resources"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    if "video_channels" in set(sa.inspect(op.get_bind()).get_table_names()):
        return

    op.create_table(
        "video_channels",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("course_id", UUID, nullable=True),
        sa.Column("channel_id", sa.String(32), nullable=False),
        sa.Column("handle", sa.String(80), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("refresh_hours", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # SET NULL, not CASCADE: deleting a course drops only that course's
        # channels and leaves the learner's shared list intact.
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_video_channel_user_scope",
        "video_channels",
        ["user_id", "course_id", "enabled"],
    )
    # One channel per user per scope. course_id being NULL makes this unique only
    # for global rows in SQLite, so the application also checks before insert.
    op.create_index(
        "ix_video_channel_unique",
        "video_channels",
        ["user_id", "channel_id"],
        unique=True,
    )


def downgrade() -> None:
    if "video_channels" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_table("video_channels")