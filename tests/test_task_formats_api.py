"""Answering a task in each format, through the real endpoints.

The unit tests in ``test_task_formats.py`` cover the comparisons. These cover the
things only the API can get wrong: that a chosen format survives the round trip,
that the client is never handed the key, and that a task which needs no model is
still graded when no model is configured at all.

That last one is the point of the whole design. A learner working through a chosen
option or a number should not be told "no AI configured" — the verdict is already
known, and asking a model about it could only add a way to be wrong.
"""

import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import database as database_module
from database import Base, get_db
from main import app
from models.learning_goal import LearningAnswer, LearningQuestion

from services.adaptive_learning import task_formats as tf

# pytest-asyncio runs in strict mode here, so async tests opt in explicitly.
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    fd, db_path = tempfile.mkstemp(prefix="opentutor-fmt-", suffix=".db")
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
    try:
        os.remove(db_path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def topic_id(client):
    """A goal and a topic, created through the API.

    Through the API rather than directly, because the app resolves the current
    user itself. A row inserted by hand belongs to nobody as far as the endpoints
    are concerned, and every lookup would answer "not found".
    """
    goal = await client.post(
        "/api/learning/goals",
        json={"topic": "Цель", "days": 14, "minutes_per_day": 60, "depth": "confident"},
    )
    assert goal.status_code == 201, goal.text
    created = await client.post(
        f"/api/learning/goals/{goal.json()['id']}/topics",
        json={"topics": ["Тема"]},
    )
    assert created.status_code == 201, created.text
    # The endpoint answers with the list of topics, not an object.
    return created.json()[0]["id"]


async def make_question(the_topic_id, fmt, payload, text="Задание"):
    """Store one task directly. It hangs on a topic the API already made."""
    async with database_module.async_session() as db:
        question = LearningQuestion(
            topic_id=the_topic_id,
            question_type="apply_situation",
            text=text,
            criteria={"correctness": "c", "completeness": "m", "understanding": "u"},
            difficulty=0.5,
            hints=["подсказка"],
            format=fmt,
            payload=payload,
        )
        db.add(question)
        await db.commit()
        return question.id


def submit(client, question_id, **body):
    body.setdefault("text", "ответ")
    return client.post(f"/api/learning/questions/{question_id}/answer", json=body)


# ── the client is never handed the key ────────────────────────────────────


async def test_a_number_task_does_not_reveal_the_expected_value(client, topic_id):
    question_id = await make_question(
        topic_id, tf.FORMAT_NUMBER, {"expected": 42.0, "tolerance": 0.1, "unit": "шт"}
    )
    response = await client.get(f"/api/learning/questions/{question_id}")
    assert response.status_code == 200
    payload = response.json()["payload"]
    assert "expected" not in payload
    assert payload["unit"] == "шт"
    # The tolerance is sent: it lets the learner see how precise to be, and it is
    # not the answer.
    assert payload["tolerance"] == pytest.approx(0.1)


async def test_a_choice_task_does_not_reveal_the_right_option(client, topic_id):
    payload = tf.normalise_payload(
        tf.FORMAT_SINGLE_CHOICE, {"options": ["a", "b", "c"], "correct": 2}
    )
    question_id = await make_question(
        topic_id, tf.FORMAT_SINGLE_CHOICE, payload)
    response = await client.get(f"/api/learning/questions/{question_id}")
    body = response.json()
    assert body["payload"] == {"options": ["a", "b", "c"]}
    assert "correct_index" not in body["payload"]
    assert "2" not in str(body["payload"].get("correct", ""))


async def test_a_code_task_sends_the_spec_but_not_the_solution(client, topic_id):
    payload = tf.normalise_payload(
        tf.FORMAT_CODE,
        {
            "starter_code": "def f(x):\n    pass\n",
            "tests": [{"name": "zero", "call": "f(0)", "expected": "0"}],
            "solution": "def f(x):\n    return 0\n",
        },
    )
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, payload)
    body = (await client.get(f"/api/learning/questions/{question_id}")).json()
    assert body["format"] == "code"
    assert body["payload"]["starter_code"].startswith("def f")
    # The check is the specification — what the function must return — and the
    # learner is shown it in the task text in any case.
    assert body["payload"]["tests"][0]["expected"] == "0"
    # The reference solution is the answer, and stays on the server.
    assert "solution" not in body["payload"]


async def test_an_ordering_task_sends_the_scrambled_items_only(client, topic_id):
    payload = tf.normalise_payload(
        tf.FORMAT_ORDERING,
        {"items": ["раз", "два", "три"], "solution": [2, 0, 1]},
    )
    question_id = await make_question(
        topic_id, tf.FORMAT_ORDERING, payload)
    body = (await client.get(f"/api/learning/questions/{question_id}")).json()
    assert body["payload"] == {"items": ["раз", "два", "три"]}


# ── the deterministic formats are graded without a model ──────────────────


@pytest.mark.parametrize(
    "fmt,payload,right,wrong",
    [
        (
            tf.FORMAT_NUMBER,
            {"expected": 7.0, "tolerance": 0.01},
            {"value": "7"},
            {"value": "8"},
        ),
        (
            tf.FORMAT_SINGLE_CHOICE,
            {"options": ["да", "нет"], "correct": 1},
            {"value": [1]},
            {"value": [0]},
        ),
        (
            tf.FORMAT_MULTI_CHOICE,
            {"options": ["a", "b", "c"], "correct": [0, 2]},
            {"value": [0, 2]},
            {"value": [0]},
        ),
        (
            tf.FORMAT_TRUE_FALSE,
            {"correct": True},
            {"value": "верно"},
            {"value": "неверно"},
        ),
        (
            tf.FORMAT_ORDERING,
            {"items": ["а", "б"], "solution": [1, 0]},
            {"value": [1, 0]},
            {"value": [0, 1]},
        ),
        (
            tf.FORMAT_MATCHING,
            {"pairs": [["слева1", "справа1"], ["слева2", "справа2"]]},
            {"value": {"слева1": "справа1", "слева2": "справа2"}},
            {"value": {"слева1": "справа2", "слева2": "справа1"}},
        ),
    ],
)
async def test_each_format_is_graded_by_comparison(
    client, topic_id, fmt, payload, right, wrong
):
    # No model is configured in this suite, so a passing verdict proves the verdict
    # did not come from one.
    question_id = await make_question(
        topic_id, fmt, tf.normalise_payload(fmt, payload)
    )

    good = await submit(client, question_id, **right)
    assert good.status_code == 201, good.text
    assert good.json()["final_score"] == 1.0
    assert good.json()["checked_without_model"] is True

    bad = await submit(client, question_id, **wrong)
    assert bad.status_code == 201, bad.text
    assert bad.json()["final_score"] == 0.0
    # A wrong answer still says what the right one was, so it can be fixed.
    assert bad.json()["expected_answer"]


async def test_a_deterministic_task_stores_its_verdict(client, topic_id):
    question_id = await make_question(
        topic_id,
        tf.FORMAT_NUMBER,
        tf.normalise_payload(tf.FORMAT_NUMBER, {"expected": 3.0}),
    )
    await submit(client, question_id, value="3")

    async with database_module.async_session() as db:
        stored = list((await db.execute(select(LearningAnswer))).scalars())
    assert len(stored) == 1
    assert stored[0].score == 1.0
    assert stored[0].question_id == question_id


# ── the learner's confidence changes what is remembered, not what is shown ─


async def test_a_guessed_right_answer_shows_full_but_is_remembered_as_weaker(
    client, topic_id
):
    question_id = await make_question(
        topic_id, tf.FORMAT_NUMBER, tf.normalise_payload(
            tf.FORMAT_NUMBER, {"expected": 5.0}
        )
    )
    confident = (await submit(client, question_id, value="5", confidence="confident")).json()
    guessing = (await submit(client, question_id, value="5", confidence="guessing")).json()

    assert confident["final_score"] == guessing["final_score"] == 1.0
    assert confident["mastery_score"] == 1.0
    assert guessing["mastery_score"] == 0.7
    assert confident["confidence_label"] == "confident"
    assert guessing["confidence_label"] == "guessing"


async def test_a_guess_is_not_recorded_as_a_failure(client, topic_id):
    # Below the medium threshold the topic is sent back for a one-day retry and
    # flagged weak. Getting it right by luck is not failing.
    question_id = await make_question(
        topic_id, tf.FORMAT_NUMBER, tf.normalise_payload(
            tf.FORMAT_NUMBER, {"expected": 5.0}
        )
    )
    body = (await submit(client, question_id, value="5", confidence="guessing")).json()
    assert body["topic_status"] != "weak"


async def test_an_undeclared_confidence_discounts_nothing(client, topic_id):
    question_id = await make_question(
        topic_id, tf.FORMAT_NUMBER, tf.normalise_payload(
            tf.FORMAT_NUMBER, {"expected": 5.0}
        )
    )
    body = (await submit(client, question_id, value="5")).json()
    assert body["mastery_score"] == 1.0
    assert body["confidence_label"] == "unspecified"


# ── code: the learner's own run decides, the model only explains ──────────


CODE_TASK = {"tests": [{"name": "zero", "call": "f(0)", "expected": "0"}]}


@pytest.fixture
def reviewer(monkeypatch):
    """Stand in for the model's reading of the code.

    Left to itself this suite would get the mock provider, whose replies are
    canned text with arbitrary numbers in them. Stubbing it is what lets the tests
    below say what the *run* decides as opposed to what a model said about it.
    """

    def _install(correctness: float, feedback: str = "разбор", **extra):
        async def fake(system, user, **kwargs):
            payload = {
                "correctness": correctness,
                "feedback": feedback,
                "error_types": [],
            }
            payload.update(extra)
            return payload

        monkeypatch.setattr(
            "services.adaptive_learning.content._structured_call", fake
        )

    return _install


async def test_a_run_that_passed_is_taken_as_proof(client, topic_id, reviewer):
    reviewer(1.0, "всё верно")
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, tf.normalise_payload(tf.FORMAT_CODE, CODE_TASK)
    )
    response = await submit(
        client,
        question_id,
        text="def f(x):\n    return 0",
        run_results=[{"name": "zero", "passed": True}],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["final_score"] == 1.0
    assert body["checked_without_model"] is False


async def test_a_passing_run_is_not_failed_by_a_model_that_misreads_it(
    client, topic_id, reviewer
):
    # The learner satisfied every check. A model returning zero cannot take that
    # away — only a failing test may.
    reviewer(0.0, "подозрительно")
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, tf.normalise_payload(tf.FORMAT_CODE, CODE_TASK)
    )
    body = (
        await submit(
            client,
            question_id,
            text="def f(x):\n    return 0",
            run_results=[{"name": "zero", "passed": True}],
        )
    ).json()
    assert body["final_score"] > 0.0


async def test_a_run_that_failed_cannot_be_argued_up(client, topic_id, reviewer):
    reviewer(1.0, "всё верно")
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, tf.normalise_payload(tf.FORMAT_CODE, CODE_TASK)
    )
    body = (
        await submit(
            client,
            question_id,
            text="def f(x):\n    return 1",
            run_results=[{"name": "zero", "passed": False}],
        )
    ).json()
    assert body["final_score"] == 0.0


async def test_a_fabricated_run_buys_no_credit_when_there_is_nothing_to_check_it(
    client, topic_id, monkeypatch
):
    # A client that could assert any test passed could buy a grade. Only the tests
    # the task declares are counted, so a name the task never had proves nothing —
    # and with no model to read the code either, nothing remains.
    async def unavailable(system, user, **kwargs):
        raise RuntimeError("no provider")

    monkeypatch.setattr(
        "services.adaptive_learning.content._structured_call", unavailable
    )
    monkeypatch.setattr(
        "services.llm.router.get_registry",
        lambda: (_ for _ in ()).throw(RuntimeError("no provider")),
    )
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, tf.normalise_payload(tf.FORMAT_CODE, CODE_TASK)
    )
    body = (
        await submit(
            client,
            question_id,
            text="print('привет')",
            run_results=[{"name": "выдуманная проверка", "passed": True}],
        )
    ).json()
    assert body["final_score"] == 0.0


async def test_the_model_is_told_when_nothing_ran(client, topic_id, monkeypatch):
    # Whether the model mentions it is up to the model. What must hold is that it
    # was told, and that the sentence carrying the fact exists to be copied — a
    # model that answers "looks right" must at least have been offered the truth.
    seen = {}

    async def capture(system, user, **kwargs):
        seen["user"] = user
        return {"correctness": 1.0, "feedback": "выглядит верно", "error_types": []}

    monkeypatch.setattr(
        "services.adaptive_learning.content._structured_call", capture
    )
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, tf.normalise_payload(tf.FORMAT_CODE, CODE_TASK)
    )
    await submit(client, question_id, text="def f(x):\n    return 0")
    assert "НЕ выполнялись" in seen["user"]


async def test_code_that_was_never_run_and_cannot_be_read_scores_nothing(
    client, topic_id, monkeypatch
):
    # No run and no model: nothing established that this code works, so nothing is
    # claimed. Said plainly rather than with a zero and a shrug.
    async def unavailable(system, user, **kwargs):
        raise RuntimeError("no provider")

    monkeypatch.setattr(
        "services.adaptive_learning.content._structured_call", unavailable
    )
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, tf.normalise_payload(tf.FORMAT_CODE, CODE_TASK)
    )
    body = (await submit(client, question_id, text="def f(x):\n    return 0")).json()
    assert body["final_score"] == 0.0
    assert body["checked_without_model"] is True
    assert "запустить" in (body["feedback"] or "").lower()


# ── asking for a coding task, and the language it is written in ───────────


async def test_a_requested_code_task_with_checks_is_returned(
    client, topic_id, monkeypatch
):
    # "Give me a coding task" has to mean a task with checks to run, or the
    # window cannot show the learner whether their code works.
    import routers.adaptive_learning as router_module
    from services.adaptive_learning import content

    captured = {}

    async def fake_task(**kwargs):
        captured.update(kwargs)
        return {
            "text": "Напишите функцию f.",
            "question_type": "apply_situation",
            "criteria": {"correctness": "c", "completeness": "m", "understanding": "u"},
            "hints": ["a", "b", "c"],
            "difficulty": 0.5,
            "format": "code",
            "payload": tf.normalise_payload(
                tf.FORMAT_CODE,
                {
                    "starter_code": "def f(x):\n    pass\n",
                    "tests": [{"name": "zero", "call": "f(0)", "expected": "0"}],
                    "solution": "def f(x):\n    return 0\n",
                },
            ),
            "expected_answer": "",
        }

    monkeypatch.setattr(router_module, "_require_llm", lambda: None)
    monkeypatch.setattr(content, "generate_task", fake_task)

    response = await client.post(
        f"/api/learning/topics/{topic_id}/question",
        json={"format": "code", "language": "en"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["format"] == "code"
    assert body["payload"]["tests"][0]["name"] == "zero"
    # The interface language the request named reached the generator, so the task
    # is written in it rather than in the goal's original language.
    assert captured["language"] == "en"
    assert captured["format_hint"] == "code"


async def test_a_requested_code_task_without_checks_is_refused(
    client, topic_id, monkeypatch
):
    # A generator that quietly falls back to prose would leave the code window with
    # nothing to verify, so it is refused instead of handed to the learner.
    import routers.adaptive_learning as router_module
    from services.adaptive_learning import content

    async def fake_task(**kwargs):
        return {
            "text": "Ответьте своими словами.",
            "question_type": "explain_own_words",
            "criteria": {"correctness": "c", "completeness": "m", "understanding": "u"},
            "hints": ["a", "b", "c"],
            "difficulty": 0.5,
            "format": "text",
            "payload": {},
            "expected_answer": "",
        }

    monkeypatch.setattr(router_module, "_require_llm", lambda: None)
    monkeypatch.setattr(content, "generate_task", fake_task)

    response = await client.post(
        f"/api/learning/topics/{topic_id}/question",
        json={"format": "code"},
    )
    assert response.status_code == 422, response.text


async def test_code_feedback_is_asked_for_in_the_requested_language(
    client, topic_id, monkeypatch
):
    seen = {}

    async def capture(system, user, **kwargs):
        seen["system"] = system
        return {"correctness": 1.0, "feedback": "ok", "error_types": []}

    monkeypatch.setattr(
        "services.adaptive_learning.content._structured_call", capture
    )
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, tf.normalise_payload(tf.FORMAT_CODE, CODE_TASK)
    )
    await submit(
        client,
        question_id,
        text="def f(x):\n    return 0",
        language="en",
        run_results=[{"name": "zero", "passed": True}],
    )
    assert "English" in seen["system"]



async def test_empty_code_is_refused(client, topic_id):
    question_id = await make_question(
        topic_id, tf.FORMAT_CODE, {})
    response = await submit(client, question_id, text="")
    # The schema requires some text; an empty answer never reaches grading.
    assert response.status_code == 422


# ── a stored task whose payload is unusable must still be answerable ───────


async def test_a_task_with_a_broken_payload_shows_nothing_rather_than_a_key(
    client, topic_id
):
    # options is empty, so there is no choice to offer. The client gets an empty
    # list — not a key, and not a 500.
    question_id = await make_question(topic_id, tf.FORMAT_SINGLE_CHOICE, {"options": []})
    shown = (await client.get(f"/api/learning/questions/{question_id}")).json()
    assert shown["format"] == "single_choice"
    assert shown["payload"] == {"options": []}