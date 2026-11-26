"""The comprehension check, through the real endpoints.

Two things are being protected here:

- the score is computed from unit statuses in code, not taken from the model;
- the dialogue actually changes shape: a wrong answer gets a focused re-teach and
  a check question, while a correct recall ends immediately.

The model is replaced by a scripted ``_structured_call``. Everything else — the
database, the state machine, the synthetic question/answer, the topic-strength
update — is the production path.
"""

import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import database as database_module
from database import Base, get_db
from main import app
from models.generated_asset import GeneratedAsset
from models.learning_goal import (
    LearningAnswer,
    LearningQuestion,
    TopicState,
    TopicStrengthPoint,
)
from models.user import User
from services.adaptive_learning import comprehension, content

EXPLANATION = (
    "Предел функции в точке описывает, к какому значению функция приближается, "
    "когда аргумент подходит к этой точке. Сам предел не обязан совпадать со "
    "значением функции в точке: функция может быть не определена там, но предел "
    "существует. Например, у f(x) = (x² - 1)/(x - 1) в точке x = 1 значения нет, "
    "но при x → 1 функция приближается к 2, поэтому предел равен 2. "
    "Важно: предел — это про поведение рядом с точкой, а не про значение в ней."
)

UNITS = [
    {
        "id": "u1",
        "label": "Предел — это поведение рядом с точкой",
        "expectation": "Предел описывает, к чему функция приближается, а не её значение в точке.",
        "source_quote": "предел — это про поведение рядом с точкой, а не про значение в ней",
        "common_misconception": "Предел всегда равен значению функции в точке.",
        "transfer_question": "Если f(1) не существует, может ли предел при x → 1 существовать?",
    },
    {
        "id": "u2",
        "label": "Разрыв и предел",
        "expectation": "Функция может быть не определена в точке, но иметь там предел.",
        "source_quote": "функция может быть не определена там, но предел существует",
        "common_misconception": "Если значения функции в точке нет, то и предела нет.",
        "transfer_question": "Что происходит с пределом, если в точке стоит выколотая точка?",
    },
]

FULL_DIAGNOSIS = {
    "units": [
        {
            "id": "u1",
            "status": "understood",
            "evidence": "предел показывает, к какому значению функция приближается",
            "why": "Верно передана идея приближения.",
            "source_quote": "предел — это про поведение рядом с точкой",
        },
        {
            "id": "u2",
            "status": "misconception",
            "evidence": "если функции нет в точке, то и предела нет",
            "why": "Смешаны значение функции и её поведение рядом с точкой.",
            "source_quote": "функция может быть не определена там, но предел существует",
        },
    ],
    "priority_gap": {
        "unit_id": "u2",
        "kind": "misconception",
        "why": "Значение в точке и предел — разные вещи.",
        "consequence": "Тогда предел f(x) = (x² - 1)/(x - 1) при x → 1 был бы объявлен несуществующим.",
    },
    "clarifying_question": "",
    "reteach": {
        "unit_id": "u2",
        "title": "Значение в точке и предел",
        "why_wrong": "Ты считаешь, что отсутствие значения в точке отменяет предел.",
        "explanation": (
            "Предел отвечает на вопрос, к какому числу приближается функция, "
            "когда аргумент подходит к точке. Значение функции в самой точке — "
            "отдельный вопрос. Функция может быть не определена в точке, но "
            "вести себя вполне предсказуемо рядом с ней. Посмотри на "
            "f(x) = (x² - 1)/(x - 1): при x = 1 формула даёт деление на ноль, "
            "поэтому значения нет. Но если брать x = 1,1; 1,01; 1,001, результат "
            "приближается к 2. Это и есть предел. Выколотая точка на графике не "
            "мешает функции подходить к ней. Ты думал: нет значения — нет предела. "
            "На самом деле: нет значения — это не мешает пределу существовать."
        ),
        "example": "У f(x) = (x² - 1)/(x - 1) при x → 1 предел равен 2, хотя f(1) не существует.",
        "source_quote": "функция может быть не определена там, но предел существует",
    },
    "check_question": "Может ли предел существовать там, где функция не определена? Объясни на примере.",
    "summary": "Ты понял идею приближения, но пока смешиваешь предел со значением в точке.",
}

FOCUS_DIAGNOSIS = {
    "units": [
        {
            "id": "u2",
            "status": "understood",
            "evidence": "функция может не иметь значения, но предел всё равно есть",
            "why": "Теперь значение в точке и предел различаются.",
            "source_quote": "функция может быть не определена там, но предел существует",
        }
    ],
    "priority_gap": None,
    "clarifying_question": "",
    "reteach": None,
    "check_question": "",
    "summary": "Теперь верно: предел и значение в точке больше не смешиваются.",
}

PARTIAL_DIAGNOSIS = {
    "units": [
        {
            "id": "u1",
            "status": "partial",
            "evidence": "функция приближается к числу",
            "why": "Идея приближения названа, но без связи с точкой.",
            "source_quote": "предел — это про поведение рядом с точкой",
        },
        {
            "id": "u2",
            "status": "partial",
            "evidence": "но что там с точкой — не уверен",
            "why": "Не хватает связи между отсутствием значения и пределом.",
            "source_quote": "функция может быть не определена там, но предел существует",
        },
    ],
    "priority_gap": None,
    "clarifying_question": "Если f(1) не существует, что можно сказать о пределе при x → 1?",
    "reteach": None,
    "check_question": "",
    "summary": "Главная идея понятна, но связь с точкой пока неполная.",
}

# A learner explains one unit and simply never mentions the other: the common
# shape of a 3-5 sentence free-recall answer.
MISSING_DIAGNOSIS = {
    "units": [
        {
            "id": "u1",
            "status": "understood",
            "evidence": "функция приближается к числу",
            "why": "Идея приближения названа.",
            "source_quote": "предел — это про поведение рядом с точкой",
        },
        {
            "id": "u2",
            "status": "missing",
            "evidence": "",
            "why": "",
            "source_quote": "",
        },
    ],
    "priority_gap": None,
    "clarifying_question": "",
    "reteach": None,
    "check_question": "",
    "summary": "",
}


@pytest_asyncio.fixture
async def client():
    fd, db_path = tempfile.mkstemp(prefix="opentutor-compr-", suffix=".db")
    os.close(fd)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=False,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    original = database_module.async_session
    database_module.async_session = factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)
    database_module.async_session = original
    await engine.dispose()


async def _make_topic(client: AsyncClient) -> dict:
    goal = await client.post(
        "/api/learning/goals",
        json={"topic": "Математический анализ", "days": 10, "minutes_per_day": 45},
    )
    assert goal.status_code == 201, goal.text
    topics = await client.post(
        f"/api/learning/goals/{goal.json()['id']}/topics",
        json={"topics": ["Предел функции"]},
    )
    assert topics.status_code == 201, topics.text
    return topics.json()[0]


async def _seed_explanation(topic_id: str) -> None:
    async with database_module.async_session() as db:
        user = (await db.execute(select(User).limit(1))).scalar_one()
        db.add(
            GeneratedAsset(
                user_id=user.id,
                asset_type="explanation",
                title="Explanation",
                content={"text": EXPLANATION},
                metadata_={"topic_id": topic_id, "source_label": "конспект"},
            )
        )
        await db.commit()


def _scripted(*, first: dict = FULL_DIAGNOSIS, focus: dict = FOCUS_DIAGNOSIS):
    calls: list[tuple[str, str]] = []

    async def fake(system: str, user: str, **kwargs):
        calls.append((system, user))
        if "prepare a comprehension check" in system:
            return {"units": UNITS}
        if "This turn checks only unit" in user:
            return focus
        return first

    return fake, calls


class TestComprehensionFlow:
    pytestmark = pytest.mark.asyncio

    async def test_wrong_recall_gets_reteach_then_passes(
        self, client: AsyncClient, monkeypatch
    ):
        topic = await _make_topic(client)
        await _seed_explanation(topic["id"])
        fake, calls = _scripted()
        monkeypatch.setattr(content, "_structured_call", fake)

        start = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/start",
            json={"language": "ru"},
        )
        assert start.status_code == 200, start.text
        started = start.json()
        assert started["resumed"] is False
        assert "Объясни своими словами" in started["question"]

        first = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/"
            f"{started['session_id']}/answer",
            json={
                "language": "ru",
                "text": "Предел показывает, к какому значению функция приближается. "
                "Но если функции нет в точке, то и предела нет.",
            },
        )
        assert first.status_code == 200, first.text
        turn = first.json()
        assert turn["status"] == "in_progress"
        assert turn["next"]["kind"] == "reteach"
        assert turn["next"]["reteach"]["unit_id"] == "u2"
        assert turn["verdict"]["target_status"] == "misconception"
        assert turn["verdict"]["gaps"][0]["unit_id"] == "u2"
        assert abs(turn["verdict"]["score"] - 0.575) < 0.01
        assert "предел" in turn["next"]["question"].lower()

        # A reload resumes the same dialogue, including the diagnosis and the
        # re-teach block that the current check question refers to.
        resumed = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/start",
            json={"language": "ru"},
        )
        assert resumed.status_code == 200, resumed.text
        resumed_body = resumed.json()
        assert resumed_body["resumed"] is True
        assert resumed_body["step"] == 1
        assert resumed_body["next"]["kind"] == "reteach"
        assert resumed_body["verdict"]["target_status"] == "misconception"

        second = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/"
            f"{started['session_id']}/answer",
            json={
                "language": "ru",
                "text": "Да: функция может не иметь значения в точке, но предел "
                "всё равно существует — как у (x²-1)/(x-1) при x → 1.",
            },
        )
        assert second.status_code == 200, second.text
        final = second.json()
        assert final["status"] == "passed"
        assert final["score"] == 1.0
        assert final["next"]["kind"] == "done"
        assert final["verdict"]["target_status"] == "understood"
        assert final["verdict"]["interval_days"] is not None

        latest = await client.get(
            f"/api/learning/topics/{topic['id']}/comprehension/latest"
        )
        assert latest.status_code == 200
        assert latest.json()["checked"] is True
        assert latest.json()["score"] == 1.0
        assert latest.json()["has_explanation"] is True

        async with database_module.async_session() as db:
            questions = await db.scalar(
                select(func.count(LearningQuestion.id)).where(
                    LearningQuestion.question_type == "comprehension_check"
                )
            )
            answers = await db.scalar(select(func.count(LearningAnswer.id)))
            points = await db.scalar(select(func.count(TopicStrengthPoint.id)))
            state = (
                await db.execute(select(TopicState).limit(1))
            ).scalar_one()
        assert questions == 1
        assert answers == 1
        assert points == 1
        assert state.strength > 0
        assert len(calls) >= 3

    async def test_partial_recall_gets_one_probe_not_a_reteach(
        self, client: AsyncClient, monkeypatch
    ):
        topic = await _make_topic(client)
        await _seed_explanation(topic["id"])
        fake, _ = _scripted(first=PARTIAL_DIAGNOSIS)
        monkeypatch.setattr(content, "_structured_call", fake)

        start = (
            await client.post(
                f"/api/learning/topics/{topic['id']}/comprehension/start",
                json={"language": "ru"},
            )
        ).json()
        turn = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/"
            f"{start['session_id']}/answer",
            json={"language": "ru", "text": "Предел — это приближение к числу."},
        )
        assert turn.status_code == 200, turn.text
        body = turn.json()
        assert body["status"] == "in_progress"
        assert body["next"]["kind"] == "question"
        assert body["next"]["reteach"] is None
        assert "f(1)" in body["next"]["question"]

    async def test_one_unmentioned_unit_finishes_with_an_explanation(
        self, client: AsyncClient, monkeypatch
    ):
        """An untouched unit must not drag the learner through another round.

        The old weights scored every ``missing`` unit as zero and required 0.8 to
        finish, so an answer that covered half the topic ended as a failure. Now
        the omission lowers the score, is named with an explanation, and the
        session closes.
        """
        topic = await _make_topic(client)
        await _seed_explanation(topic["id"])
        fake, _ = _scripted(first=MISSING_DIAGNOSIS)
        monkeypatch.setattr(content, "_structured_call", fake)

        start = (
            await client.post(
                f"/api/learning/topics/{topic['id']}/comprehension/start",
                json={"language": "ru"},
            )
        ).json()
        turn = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/"
            f"{start['session_id']}/answer",
            json={
                "language": "ru",
                "text": "Предел показывает, к какому значению функция приближается.",
            },
        )
        assert turn.status_code == 200, turn.text
        body = turn.json()

        assert body["next"]["kind"] == "done"
        assert body["status"] == "needs_review"  # 0.65: one of two units is done
        assert abs(body["verdict"]["score"] - 0.65) < 0.01

        gaps = body["verdict"]["gaps"]
        assert [gap["unit_id"] for gap in gaps] == ["u2"]
        # The result must say what is wrong and what it costs, not only a label.
        assert gaps[0]["why"]
        assert gaps[0]["consequence"]
        assert body["verdict"]["summary"]

    async def test_start_refuses_a_topic_with_no_explanation(
        self, client: AsyncClient, monkeypatch
    ):
        topic = await _make_topic(client)
        fake, _ = _scripted()
        monkeypatch.setattr(content, "_structured_call", fake)
        response = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/start",
            json={"language": "ru"},
        )
        assert response.status_code == 422

    async def test_answer_outside_the_users_topic_is_404(
        self, client: AsyncClient, monkeypatch
    ):
        topic = await _make_topic(client)
        await _seed_explanation(topic["id"])
        fake, _ = _scripted()
        monkeypatch.setattr(content, "_structured_call", fake)
        response = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/"
            "00000000-0000-0000-0000-000000000000/answer",
            json={"language": "ru", "text": "Ответ"},
        )
        assert response.status_code == 404

    async def test_english_request_gets_an_english_recall_question(
        self, client: AsyncClient, monkeypatch
    ):
        topic = await _make_topic(client)
        await _seed_explanation(topic["id"])
        fake, calls = _scripted()
        monkeypatch.setattr(content, "_structured_call", fake)
        response = await client.post(
            f"/api/learning/topics/{topic['id']}/comprehension/start",
            json={"language": "en"},
        )
        assert response.status_code == 200, response.text
        assert "Explain the main idea" in response.json()["question"]
        assert "Language: English" in calls[0][1]


class TestComprehensionArithmetic:
    def test_understood_outweighs_a_misconception(self):
        assert comprehension.score_units(
            [{"status": "understood"}, {"status": "misconception"}]
        ) == 0.57

    def test_unmentioned_units_cost_less_than_a_misconception(self):
        """Silence is an omission, not a wrong mental model."""
        assert comprehension.score_units(
            [{"status": "missing"}]
        ) > comprehension.score_units([{"status": "misconception"}])
        assert comprehension.score_units(
            [{"status": "missing"}, {"status": "missing_prerequisite"}]
        ) == pytest.approx(0.2)

    def test_material_contains_the_explanation_and_only_new_source_text(self):
        combined = comprehension.combine_material(
            "Объяснение темы.", "Источник: определение предела."
        )
        assert combined.startswith("EXPLANATION:")
        assert "SOURCE MATERIAL:" in combined
        duplicate = comprehension.combine_material(
            "Объяснение темы.", "Объяснение темы."
        )
        assert "SOURCE MATERIAL:" not in duplicate
