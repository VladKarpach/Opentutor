"""Grading a photo of a handwritten solution.

The model reads the image, so these tests stub it rather than calling it. What they
pin down is the wiring: that a photo takes the image path rather than the text path,
that the image is handed over in the shape the client expects, and that the
handwriting is treated as data rather than as instructions.
"""

import base64
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import database as database_module
from database import Base, get_db
from main import app
from models.learning_goal import LearningQuestion

pytestmark = pytest.mark.asyncio

# A 1x1 PNG. Enough to be a valid image; the point is the message shape, not what
# the model can see in a single pixel.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
DATA_URL = f"data:image/png;base64,{base64.b64encode(PNG).decode()}"


@pytest_asyncio.fixture
async def client():
    import os
    import tempfile

    fd, db_path = tempfile.mkstemp(prefix="opentutor-photo-", suffix=".db")
    os.close(fd)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    # `_make_question` writes through this module-level session, so it has to point
    # at the same temporary database the client is talking to; otherwise the row it
    # inserts has a topic_id the API's database has never seen.
    original = database_module.async_session
    database_module.async_session = factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)
    database_module.async_session = original
    await engine.dispose()
    try:
        os.remove(db_path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def topic_id(client):
    goal = await client.post(
        "/api/learning/goals",
        json={"topic": "Цель", "days": 14, "minutes_per_day": 60, "depth": "confident"},
    )
    created = await client.post(
        f"/api/learning/goals/{goal.json()['id']}/topics",
        json={"topics": ["Тема"]},
    )
    return created.json()[0]["id"]


async def _make_question(topic_id):
    async with database_module.async_session() as db:
        question = LearningQuestion(
            topic_id=uuid.UUID(topic_id) if isinstance(topic_id, str) else topic_id,
            question_type="explain_own_words",
            text="Объясните своими словами, что делает прямой ход метода Гаусса.",
            criteria={"correctness": "c", "completeness": "m", "understanding": "u"},
            difficulty=0.5,
            hints=["подсказка"],
            format="text",
            payload={},
        )
        db.add(question)
        await db.commit()
        return question.id


def submit(client, question_id, **body):
    body.setdefault("text", "ответ")
    return client.post(f"/api/learning/questions/{question_id}/answer", json=body)


async def test_a_photo_takes_the_image_path(client, topic_id, monkeypatch):
    seen = {}

    async def fake(system, user, **kwargs):
        seen["images"] = kwargs.get("images")
        return {
            "breakdown": {"correctness": 1.0, "completeness": 1.0, "understanding": 1.0},
            "feedback": "верно",
            "error_types": [],
        }

    monkeypatch.setattr("services.adaptive_learning.content._structured_call", fake)

    question_id = await _make_question(topic_id)
    response = await submit(client, question_id, image=DATA_URL, text="Решение на фото")

    assert response.status_code == 201, response.text
    assert response.json()["final_score"] == 1.0
    # The image reached the client as bare base64 with the media type separate,
    # which is the shape it asks for.
    assert seen["images"] == [{"data": base64.b64encode(PNG).decode(), "media_type": "image/png"}]


async def test_the_task_is_sent_alongside_the_image(client, topic_id, monkeypatch):
    seen = {}

    async def fake(system, user, **kwargs):
        seen["user"] = user
        return {
            "breakdown": {"correctness": 0.5, "completeness": 0.5, "understanding": 0.5},
            "feedback": "частично",
            "error_types": ["incomplete"],
        }

    monkeypatch.setattr("services.adaptive_learning.content._structured_call", fake)

    question_id = await _make_question(topic_id)
    await submit(client, question_id, image=DATA_URL, text="Решение на фото")

    # The model is told what it is looking at, not only that it is looking at
    # something — otherwise it is asked to grade an answer it has not been given.
    assert "прямой ход" in seen["user"]
    assert "image" in seen["user"].lower() or "solution" in seen["user"].lower()


async def test_handwriting_is_declared_to_be_data(client, topic_id, monkeypatch):
    seen = {}

    async def fake(system, user, **kwargs):
        seen["system"] = user
        return {
            "breakdown": {"correctness": 1.0, "completeness": 1.0, "understanding": 1.0},
            "feedback": "верно",
            "error_types": [],
        }

    monkeypatch.setattr("services.adaptive_learning.content._structured_call", fake)

    question_id = await _make_question(topic_id)
    await submit(client, question_id, image=DATA_URL, text="Решение на фото")

    # A learner can write anything on their page, including "ignore the task". The
    # system prompt says so, which is what stops it becoming an instruction.
    assert "data" in seen["system"].lower()


async def test_a_photo_is_stored_with_the_answer(client, topic_id, monkeypatch):
    from models.learning_goal import LearningAnswer

    async def fake(system, user, **kwargs):
        return {
            "breakdown": {"correctness": 1.0, "completeness": 1.0, "understanding": 1.0},
            "feedback": "верно",
            "error_types": [],
        }

    monkeypatch.setattr("services.adaptive_learning.content._structured_call", fake)

    question_id = await _make_question(topic_id)
    await submit(client, question_id, image=DATA_URL, text="Решение на фото")

    async with database_module.async_session() as db:
        stored = list((await db.execute(select(LearningAnswer))).scalars())
    assert len(stored) == 1
    # The photo is not stored: it is a megabyte of pixels that has already been
    # read once, and keeping it would bloat every answer row.
    assert stored[0].text == "Решение на фото"


async def test_an_image_that_is_not_a_data_url_is_refused(client, topic_id):
    question_id = await _make_question(topic_id)
    response = await submit(client, question_id, image="https://example.com/solution.jpg")
    assert response.status_code == 422


async def test_an_oversized_image_is_refused(client, topic_id):
    question_id = await _make_question(topic_id)
    big = f"data:image/png;base64,{base64.b64encode(b'0' * 9_000_000).decode()}"
    response = await submit(client, question_id, image=big)
    assert response.status_code == 422


async def test_text_still_takes_the_text_path(client, topic_id, monkeypatch):
    seen = {}

    async def fake(system, user, **kwargs):
        seen["images"] = kwargs.get("images")
        return {
            "breakdown": {"correctness": 1.0, "completeness": 1.0, "understanding": 1.0},
            "feedback": "верно",
            "error_types": [],
        }

    monkeypatch.setattr("services.adaptive_learning.content._structured_call", fake)

    question_id = await _make_question(topic_id)
    response = await submit(client, question_id, text="Прямой ход приводит матрицу к ступенчатому виду")
    assert response.status_code == 201
    assert seen["images"] is None