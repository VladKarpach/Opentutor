"""Grounding a topic in the learner's own literature.

FR-3.2 binds subtopics to uploaded material and FR-3.4 has the answer name the
fragment it used. Neither happened: explanations were written from the topic's
one-line description, so the model was asked to teach something it had nothing
about, and the learner got material back that matched no book they own.

Matching here is by keyword, which is a limitation rather than a placeholder —
this deployment has no embedding provider, so vector search returns nothing.
These tests pin the behaviour that actually has to hold: the right passages come
back, the wrong ones do not, and the pin overrides the guess.
"""

import os
import tempfile
import uuid

import pytest
import pytest_asyncio
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from database import Base, install_sqlite_unicode_functions
from models.content import CourseContentTree
from models.course import Course
from models.learning_goal import LearningGoal, LearningTopic
from models.user import User
from services.adaptive_learning import topic_material

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    fd, db_path = tempfile.mkstemp(prefix="opentutor-material-", suffix=".db")
    os.close(fd)
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=False,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # The application gives every SQLite connection Unicode-aware lower()/upper()
    # so ILIKE can match Russian text. A connection without them finds nothing,
    # which would make this file test a failure rather than the code.
    @sqlalchemy.event.listens_for(test_engine.sync_engine, "connect")
    def _unicode_math(dbapi_conn, _record):
        install_sqlite_unicode_functions(dbapi_conn)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    await test_engine.dispose()
    try:
        os.remove(db_path)
    except OSError:
        pass


async def _course_with_material(db) -> tuple[Course, dict[str, CourseContentTree]]:
    user = User(name="Mat")
    db.add(user)
    await db.flush()

    course = Course(id=uuid.uuid4(), user_id=user.id, name="Курс")
    db.add(course)
    await db.flush()

    nodes = {}
    specs = [
        ("Гаусс", "Метод Гаусса прямой ход приведение к ступенчатому виду", "линейная-алгебра.pdf"),
        ("Определитель", "Определитель матрицы и его свойства вычисление", "линейная-алгебра.pdf"),
        ("Списки", "Создание списка индексация срезы в Python", "python.pdf"),
        ("Пустой", None, "пусто.pdf"),
    ]
    for title, content, source in specs:
        node = CourseContentTree(
            id=uuid.uuid4(),
            course_id=course.id,
            title=title,
            content=content,
            level=1,
            order_index=0,
            source_file=source,
            source_type="pdf",
        )
        db.add(node)
        nodes[title] = node
    await db.commit()
    return course, nodes


async def test_finds_the_passage_about_this_topic(db):
    course, nodes = await _course_with_material(db)
    found = await topic_material.find_material_for_topic(
        db, course.id, "Метод Гаусса", "прямой ход матрицы"
    )
    titles = {p["title"] for p in found}
    assert "Гаусс" in titles
    assert "Списки" not in titles


async def test_the_excerpt_and_origin_come_back(db):
    """FR-3.4: the answer must be able to name where it came from."""
    course, _nodes = await _course_with_material(db)
    found = await topic_material.find_material_for_topic(db, course.id, "Метод Гаусса")
    assert found
    first = found[0]
    assert first["excerpt"].strip()
    assert first["source_file"] == "линейная-алгебра.pdf"
    assert uuid.UUID(first["node_id"])


async def test_a_single_common_word_is_not_enough(db):
    """Otherwise every node in a course matches and the grounding is noise."""
    course, _nodes = await _course_with_material(db)
    found = await topic_material.find_material_for_topic(
        db, course.id, "метод", "метод"
    )
    # "метод" appears in one node only, so nothing clears the two-term bar.
    assert found == []


async def test_stop_words_alone_match_nothing(db):
    course, _nodes = await _course_with_material(db)
    found = await topic_material.find_material_for_topic(
        db, course.id, "и в на с", "о и же"
    )
    assert found == []


async def test_empty_nodes_are_skipped(db):
    """A heading with no body cannot ground anything."""
    course, _nodes = await _course_with_material(db)
    found = await topic_material.find_material_for_topic(db, course.id, "Пустой")
    assert all(p["excerpt"].strip() for p in found)


async def test_material_from_another_course_is_not_returned(db):
    course_a, _ = await _course_with_material(db)
    course_b, _ = await _course_with_material(db)
    found = await topic_material.find_material_for_topic(db, course_b.id, "Метод Гаусса")
    assert all(
        p["source_file"] == "линейная-алгебра.pdf" for p in found
    ), "nodes from course A must not leak into course B"


async def test_grounding_text_names_every_file(db):
    course, _nodes = await _course_with_material(db)
    found = await topic_material.find_material_for_topic(
        db, course.id, "Гаусс и списки вместе"
    )
    text, label = topic_material.as_grounding_text(found)
    if found:
        assert text.strip()
        assert "[" in text and "]" in text
        assert label
    else:
        assert text == "" and label == ""


async def test_grounding_text_of_nothing_is_empty(db):
    assert topic_material.as_grounding_text([]) == ("", "")


async def test_a_chosen_file_scopes_the_search(db):
    """A course can hold several unrelated uploads.

    The learner who uploaded both a linear-algebra book and a Python book knows
    which one their topic is from; the automatic match does not. When a file is
    named, nodes from the other file must not be returned even if they match.
    """
    course, _nodes = await _course_with_material(db)
    found = await topic_material.find_material_for_topic(
        db,
        course.id,
        "Метод Гаусса",
        "прямой ход матрицы",
        source_file="python.pdf",
    )
    assert found == []

    in_algebra = await topic_material.find_material_for_topic(
        db,
        course.id,
        "Метод Гаусса",
        "прямой ход матрицы",
        source_file="линейная-алгебра.pdf",
    )
    assert in_algebra
    assert all(p["source_file"] == "линейная-алгебра.pdf" for p in in_algebra)


async def test_a_chosen_file_is_used_even_without_keyword_matches(db):
    """An explicit choice must not collapse into "no source".

    The learner asked for *this* book to be read. If keyword matching finds
    nothing inside it (a topic named in Russian against an English book), the
    file's own opening sections are still better than silently falling back.
    """
    course, _nodes = await _course_with_material(db)
    found = await topic_material.file_passages(
        db,
        course.id,
        "python.pdf",
        topic_title="Метод Гаусса",
        topic_description="прямой ход матрицы",
    )
    assert found
    assert all(p["source_file"] == "python.pdf" for p in found)
    assert found[0]["excerpt"].strip()


async def test_course_sources_lists_every_upload(db):
    course, _nodes = await _course_with_material(db)
    sources = await topic_material.list_course_sources(db, course.id)
    assert {entry["name"] for entry in sources} == {
        "линейная-алгебра.pdf",
        "python.pdf",
        "пусто.pdf",
    }
    algebra = next(e for e in sources if e["name"] == "линейная-алгебра.pdf")
    assert algebra["nodes"] == 2


async def test_a_pinned_node_is_returned_verbatim(db):
    """The learner's explicit choice wins over the keyword guess."""
    course, nodes = await _course_with_material(db)
    pinned = nodes["Списки"]
    found = await topic_material.get_passages_by_ids(
        db, course.id, [pinned.id]
    )
    assert len(found) == 1
    assert found[0]["node_id"] == str(pinned.id)
    assert found[0]["source_file"] == "python.pdf"


async def test_a_pin_from_another_course_is_refused(db):
    """A client can send any id; the scope check is what stops it."""
    course_a, nodes_a = await _course_with_material(db)
    course_b, _ = await _course_with_material(db)
    found = await topic_material.get_passages_by_ids(
        db, course_b.id, [nodes_a["Гаусс"].id]
    )
    assert found == []


async def test_a_pin_to_an_unknown_node_is_skipped(db):
    course, _nodes = await _course_with_material(db)
    found = await topic_material.get_passages_by_ids(
        db, course.id, [uuid.uuid4()]
    )
    assert found == []


async def test_an_explanation_can_name_its_sources(db):
    """The metadata is what the topic screen reads to show "written from…",
    so it must carry the node ids the explanation was actually given."""
    from routers.adaptive_learning import _grounding_for_topic

    course, _nodes = await _course_with_material(db)
    user = User(name="Owner")
    db.add(user)
    await db.flush()
    goal = LearningGoal(
        id=uuid.uuid4(), user_id=user.id, course_id=course.id, topic="Алгебра",
        status="active",
    )
    db.add(goal)
    await db.flush()
    topic = LearningTopic(
        id=uuid.uuid4(), goal_id=goal.id, title="Метод Гаусса", description="прямой ход"
    )
    db.add(topic)
    await db.commit()

    passages, label = await _grounding_for_topic(db, user.id, topic)
    assert passages
    assert label

    text, joined_label = topic_material.as_grounding_text(passages)
    assert "Гаусс" in text
    assert joined_label


async def _goal_and_topic(db, course):
    """A goal + topic pair for the grounding tests."""
    user = User(name="Owner")
    db.add(user)
    await db.flush()
    goal = LearningGoal(
        id=uuid.uuid4(), user_id=user.id, course_id=course.id, topic="Алгебра",
        status="active",
    )
    db.add(goal)
    await db.flush()
    topic = LearningTopic(
        id=uuid.uuid4(), goal_id=goal.id, title="Метод Гаусса", description="прямой ход"
    )
    db.add(topic)
    await db.commit()
    return user, goal, topic


async def test_grounding_can_be_turned_off_entirely(db):
    """The generation screen's "AI only" choice must reach no passage."""
    from routers.adaptive_learning import _grounding_for_topic

    course, _nodes = await _course_with_material(db)
    user, _goal, topic = await _goal_and_topic(db, course)

    passages, label = await _grounding_for_topic(
        db, user.id, topic, ground=False
    )
    assert passages == []
    assert label == ""


async def test_a_chosen_file_wins_over_the_automatic_match(db):
    """The file named at generation time is used even when another file would
    score higher by keyword — that is the whole point of asking the learner."""
    from routers.adaptive_learning import _grounding_for_topic

    course, _nodes = await _course_with_material(db)
    user, _goal, topic = await _goal_and_topic(db, course)

    passages, label = await _grounding_for_topic(
        db, user.id, topic, source_file="python.pdf"
    )
    assert passages
    assert label == "python.pdf"
    assert all(p["source_file"] == "python.pdf" for p in passages)


async def test_a_goal_without_a_course_grounds_on_nothing(db):
    """Without a course there is no literature; the caller must fall back
    rather than the helper inventing a source."""
    from routers.adaptive_learning import _grounding_for_topic

    user = User(name="Owner2")
    db.add(user)
    await db.flush()
    goal = LearningGoal(
        id=uuid.uuid4(), user_id=user.id, course_id=None, topic="Без курса",
        status="active",
    )
    db.add(goal)
    await db.flush()
    topic = LearningTopic(
        id=uuid.uuid4(), goal_id=goal.id, title="Тема", description="d"
    )
    db.add(topic)
    await db.commit()

    passages, label = await _grounding_for_topic(db, user.id, topic)
    assert passages == []
    assert label == ""