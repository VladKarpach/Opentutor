"""SQLite's ``lower`` only folds ASCII, so ``ILIKE`` could not match Russian.

SQLAlchemy compiles ``ilike`` to ``lower(col) LIKE lower(pattern)``. SQLite's
built-in ``lower`` leaves Cyrillic alone, so "Метод Гаусса" never matched
``%метод%``. This is not specific to the topic-material search: the course
search, the agent's RAG retrieval and every other ``ILIKE`` in the codebase were
silently blind to Russian text that started a sentence or named something in
upper case — which, in an app whose interface is Russian, is most of it.

The application installs a Unicode-aware ``lower``/``upper`` on every SQLite
connection. These tests pin both that it works and that a connection without it
really does fail, so the regression cannot come back quietly.
"""

import os
import tempfile
import uuid

import pytest
import pytest_asyncio
import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from database import Base, install_sqlite_unicode_functions
from models.content import CourseContentTree
from models.course import Course
from models.user import User

pytestmark = pytest.mark.asyncio


def _engine(with_unicode: bool):
    fd, path = tempfile.mkstemp(prefix="opentutor-case-", suffix=".db")
    os.close(fd)
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    if with_unicode:

        @sqlalchemy.event.listens_for(eng.sync_engine, "connect")
        def _install(dbapi_conn, _record):
            install_sqlite_unicode_functions(dbapi_conn)

    return eng, path


@pytest_asyncio.fixture
async def plain_db():
    """A connection with SQLite's stock functions — the broken behaviour."""
    eng, path = _engine(with_unicode=False)
    factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        yield db
    await eng.dispose()
    try:
        os.remove(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def db():
    eng, path = _engine(with_unicode=True)
    factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    await eng.dispose()
    try:
        os.remove(path)
    except OSError:
        pass


async def _seed(db):
    user = User(name="Case")
    db.add(user)
    await db.flush()
    course = Course(id=uuid.uuid4(), user_id=user.id, name="Алгебра")
    db.add(course)
    await db.flush()
    node = CourseContentTree(
        id=uuid.uuid4(),
        course_id=course.id,
        title="Гаусс",
        content="Метод Гаусса прямой ход приведение к ступенчатому виду",
        level=1,
        source_file="учебник.pdf",
    )
    db.add(node)
    await db.commit()
    return course, node


async def _ilike_hits(db, course_id, pattern: str) -> int:
    rows = await db.execute(
        select(func.count(CourseContentTree.id)).where(
            CourseContentTree.course_id == course_id,
            CourseContentTree.content.ilike(f"%{pattern}%"),
        )
    )
    return int(rows.scalar_one())


async def test_ilike_finds_russian_text_with_capitals(db):
    """The regression this all exists for."""
    course, _node = await _seed(db)
    assert await _ilike_hits(db, course.id, "метод") == 1
    assert await _ilike_hits(db, course.id, "Гаусс") == 1
    assert await _ilike_hits(db, course.id, "гаусс") == 1


async def test_ilike_still_finds_ascii_and_matches_nothing_when_absent(db):
    course, _node = await _seed(db)
    assert await _ilike_hits(db, course.id, "прямой") == 1
    assert await _ilike_hits(db, course.id, "дифференциал") == 0


async def test_upper_is_also_unicode_aware(db):
    await _seed(db)
    rows = await db.execute(
        select(func.upper(CourseContentTree.title))
    )
    assert rows.scalar_one() == "ГАУСС"


async def test_a_stock_connection_really_does_fail(plain_db):
    """Without the fix this is exactly what the application did: nothing found.

    Pinned so the fix cannot be removed without a test noticing that the
    behaviour it replaced was in fact broken.
    """
    course, _node = await _seed(plain_db)
    assert await _ilike_hits(plain_db, course.id, "метод") == 0
    # Matching only works when the pattern happens to use the stored casing.
    assert await _ilike_hits(plain_db, course.id, "Метод") == 1


async def test_null_passes_through_instead_of_raising(db):
    """SQL three-valued logic expects NULL, and a UDF that raised would turn a
    missing column into a 500."""
    rows = await db.execute(
        select(func.lower(CourseContentTree.parent_id))
    )
    assert rows.scalar_one_or_none() is None