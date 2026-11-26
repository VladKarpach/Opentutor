"""Add difficulty weight to learning topics and FSRS difficulty to topic states.

Existing topic rows default to ``medium`` so current plans keep their behaviour.
``fsrs_difficulty`` on topic states is nullable: when no FSRS data exists the
adaptive factor falls back to 1.0.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0045_topic_difficulty"
down_revision = "0044_goal_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "learning_topics" in set(inspector.get_table_names()):
        columns = {c["name"] for c in inspector.get_columns("learning_topics")}
        if "difficulty" not in columns:
            op.add_column(
                "learning_topics",
                sa.Column(
                    "difficulty",
                    sa.String(20),
                    nullable=False,
                    server_default="medium",
                ),
            )

    if "topic_states" in set(inspector.get_table_names()):
        columns = {c["name"] for c in inspector.get_columns("topic_states")}
        if "fsrs_difficulty" not in columns:
            op.add_column(
                "topic_states",
                sa.Column("fsrs_difficulty", sa.Float(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "topic_states" in set(inspector.get_table_names()):
        columns = {c["name"] for c in inspector.get_columns("topic_states")}
        if "fsrs_difficulty" in columns:
            with op.batch_alter_table("topic_states") as batch:
                batch.drop_column("fsrs_difficulty")

    if "learning_topics" in set(inspector.get_table_names()):
        columns = {c["name"] for c in inspector.get_columns("learning_topics")}
        if "difficulty" in columns:
            with op.batch_alter_table("learning_topics") as batch:
                batch.drop_column("difficulty")
