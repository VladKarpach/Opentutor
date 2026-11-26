"""Add topic_resources: the learner's own links beside a topic.

Videos to watch before a topic. Kept out of ``generated_assets`` because nothing
here is generated — every row is something the learner typed, which is the whole
reason these links are trustworthy. A model asked for a video URL invents one
that looks right and does not open.

Revision ID: 0036_topic_resources
Revises: 0035_plan_revisions
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0036_topic_resources"
down_revision = "0035_plan_revisions"
branch_labels = None
depends_on = None


UUID = sa.String(36)


def upgrade() -> None:
    if "topic_resources" in set(sa.inspect(op.get_bind()).get_table_names()):
        return

    op.create_table(
        "topic_resources",
        sa.Column("id", UUID, nullable=False),
        sa.Column("topic_id", UUID, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="video"),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_topic_resource_topic", "topic_resources", ["topic_id", "order_index"]
    )


def downgrade() -> None:
    if "topic_resources" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_table("topic_resources")