"""Give tasks a reply format, so a task is not always a paragraph.

Until now a task was prose in, prose out: ``LearningQuestion.text`` held the whole
task and ``LearningAnswer.text`` held the whole reply. That made "write a function
that reverses a list" and "name the three steps of Gauss elimination" the same kind
of object, so one of them was always a poor fit.

Two columns fix that. ``format`` records how the learner replies — prose, code, a
number, a choice, an ordering, a set of pairs — and ``payload`` holds whatever that
format needs to be checked without a model: the options and the correct one, the
expected value and its tolerance, starter code and test cases.

``question_type`` is untouched. It answers a different question — the pedagogical
intent, find-the-error versus apply-it — and a task needs both.

The index on ``format`` is deliberate. Format is the column the generator filters
and rotates on, the same way ``question_type`` already is, and without it that
becomes a full table scan over every question ever asked.

Revision ID: 0038_task_formats
Revises: 0037_video_channels
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0038_task_formats"
down_revision = "0037_video_channels"
branch_labels = None
depends_on = None


def _columns(bind, table):
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "learning_questions" not in tables:
        return

    existing = _columns(bind, "learning_questions")
    if "format" not in existing:
        # server_default so the column is not NULL for rows inserted while older
        # code is still running during a rolling restart.
        op.add_column(
            "learning_questions",
            sa.Column(
                "format",
                sa.String(24),
                nullable=False,
                server_default="text",
            ),
        )
    if "payload" not in existing:
        op.add_column(
            "learning_questions",
            sa.Column("payload", sa.JSON(), nullable=True),
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("learning_questions")}
    if "ix_learning_question_format" not in indexes:
        op.create_index(
            "ix_learning_question_format",
            "learning_questions",
            ["format"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "learning_questions" not in set(sa.inspect(bind).get_table_names()):
        return

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("learning_questions")}
    if "ix_learning_question_format" in indexes:
        op.drop_index("ix_learning_question_format", table_name="learning_questions")

    existing = _columns(bind, "learning_questions")
    if "payload" in existing:
        op.drop_column("learning_questions", "payload")
    if "format" in existing:
        op.drop_column("learning_questions", "format")