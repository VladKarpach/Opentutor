"""Tests for the additive column repair in services/app_lifecycle.py.

Why this exists: ``create_all()`` never adds a column to a table that already
exists, and the SQLite bootstrap deliberately skips Alembic. A model that grows
a column is therefore invisible to existing local installs and every query fails
with "no such column" (observed on the course link: learning_goals.course_id).

These run against real in-memory SQLite, so they exercise the actual DDL.
"""

import pytest
import sqlalchemy as sa

from services.app_lifecycle import _ADDITIVE_COLUMNS, add_missing_columns


@pytest.fixture()
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        yield connection


def _create_stub_table(connection, with_course_id: bool) -> None:
    """Recreate learning_goals the way an older install has it."""
    columns = [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
    ]
    if with_course_id:
        columns.append(sa.Column("course_id", sa.String(36), nullable=True))
    sa.Table("learning_goals", sa.MetaData(), *columns).create(connection)


def _columns(connection) -> set[str]:
    return {c["name"] for c in sa.inspect(connection).get_columns("learning_goals")}


class TestAddsMissingColumn:
    def test_course_id_is_added_to_an_existing_table(self, conn):
        _create_stub_table(conn, with_course_id=False)
        assert "course_id" not in _columns(conn)

        add_missing_columns(conn)

        assert "course_id" in _columns(conn)

    def test_existing_rows_survive(self, conn):
        _create_stub_table(conn, with_course_id=False)
        conn.execute(
            sa.text(
                "INSERT INTO learning_goals (id, user_id, topic) "
                "VALUES ('a', 'u1', 'Моя тема')"
            )
        )
        conn.commit()

        add_missing_columns(conn)

        rows = conn.execute(sa.text("SELECT id, topic, course_id FROM learning_goals")).all()
        assert len(rows) == 1
        assert rows[0][1] == "Моя тема"
        assert rows[0][2] is None  # backfilled as NULL, not a guess

    def test_index_is_created(self, conn):
        _create_stub_table(conn, with_course_id=False)

        add_missing_columns(conn)

        indexes = {i["name"] for i in sa.inspect(conn).get_indexes("learning_goals")}
        assert "ix_learning_goal_course" in indexes


class TestIsIdempotentAndSafe:
    """Startup runs this every boot, and on every replica at once."""

    def test_running_twice_is_fine(self, conn):
        _create_stub_table(conn, with_course_id=False)

        add_missing_columns(conn)
        add_missing_columns(conn)  # must not raise

        assert "course_id" in _columns(conn)

    def test_no_op_when_the_column_already_exists(self, conn):
        _create_stub_table(conn, with_course_id=True)

        add_missing_columns(conn)

        assert "course_id" in _columns(conn)

    def test_missing_table_is_skipped(self, conn):
        """create_all() has not run yet; must not raise."""

        add_missing_columns(conn)  # no learning_goals table at all

    def test_other_tables_are_untouched(self, conn):
        sa.Table(
            "unrelated",
            sa.MetaData(),
            sa.Column("id", sa.String(36), primary_key=True),
        ).create(conn)
        _create_stub_table(conn, with_course_id=False)

        add_missing_columns(conn)

        assert {c["name"] for c in sa.inspect(conn).get_columns("unrelated")} == {"id"}


class TestTopicDifficultyColumns:
    """A plan page reads every LearningTopic column, so a missing one breaks it."""

    @staticmethod
    def _create_old_topic_tables(connection) -> None:
        sa.Table(
            "learning_topics",
            sa.MetaData(),
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("goal_id", sa.String(36), nullable=False),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("estimated_minutes", sa.Integer(), nullable=False),
            sa.Column("order_index", sa.Integer(), nullable=False),
        ).create(connection)
        sa.Table(
            "topic_states",
            sa.MetaData(),
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("topic_id", sa.String(36), nullable=False),
        ).create(connection)
        connection.execute(
            sa.text(
                "INSERT INTO learning_topics "
                "(id, goal_id, title, estimated_minutes, order_index) "
                "VALUES ('t1', 'g1', 'Тема', 30, 0)"
            )
        )
        connection.commit()

    def test_difficulty_is_added_and_backfills_medium(self, conn):
        self._create_old_topic_tables(conn)

        add_missing_columns(conn)

        columns = {c["name"] for c in sa.inspect(conn).get_columns("learning_topics")}
        assert "difficulty" in columns
        row = conn.execute(sa.text("SELECT difficulty FROM learning_topics")).one()
        assert row[0] == "medium"

    def test_fsrs_difficulty_is_added_nullable(self, conn):
        self._create_old_topic_tables(conn)

        add_missing_columns(conn)

        columns = {c["name"]: c for c in sa.inspect(conn).get_columns("topic_states")}
        assert "fsrs_difficulty" in columns
        assert columns["fsrs_difficulty"]["nullable"] is True


class TestDeclarativeList:
    def test_entries_are_well_formed(self):
        for table, column, ddl_type in _ADDITIVE_COLUMNS:
            assert table and column and ddl_type
            assert table.isidentifier()
            assert column.isidentifier()

    def test_learning_goals_course_id_is_declared(self):
        assert ("learning_goals", "course_id", "VARCHAR(36)") in _ADDITIVE_COLUMNS

    def test_user_profiles_active_goal_is_declared(self):
        # Without this entry an existing SQLite install boots with a model that
        # has a column the database lacks, and every profile query fails.
        assert (
            "user_profiles",
            "active_goal_id",
            "VARCHAR(36)",
        ) in _ADDITIVE_COLUMNS

    def test_topic_difficulty_columns_are_declared(self):
        # Without these the plan endpoint 500s with "no such column:
        # learning_topics.difficulty" on every already-created local install.
        declared = {(table, column) for table, column, _ in _ADDITIVE_COLUMNS}
        assert ("learning_topics", "difficulty") in declared
        assert ("topic_states", "fsrs_difficulty") in declared
