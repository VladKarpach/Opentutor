"""The guided daily session, through the real endpoints.

The plan remains the source of truth. These tests protect the three promises of
the daily mode:

- a session is assembled without the learner choosing a topic;
- an unfinished past day is carried forward and the dates move;
- a weak topic opens with a longer review, and finishing marks only the topics
  that were actually completed.
"""

import os
import tempfile
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import database as database_module
from database import Base, get_db
from main import app
from models.learning_goal import (
    LearningAnswer,
    LearningPlanItem,
    LearningQuestion,
    TopicDrill,
)
from models.user import User
from routers import adaptive_learning as adaptive_router
from services.adaptive_learning import daily
from services.adaptive_learning.persistence import apply_scored_answer

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    fd, db_path = tempfile.mkstemp(prefix="opentutor-daily-", suffix=".db")
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


@pytest_asyncio.fixture
async def db_session(client):
    async with database_module.async_session() as session:
        yield session


async def _make_goal(client: AsyncClient, topics: list[str]) -> tuple[dict, list[dict]]:
    resp = await client.post(
        "/api/learning/goals",
        json={
            "topic": "Основы анализа",
            "days": 14,
            "minutes_per_day": 60,
            "depth": "confident",
        },
    )
    assert resp.status_code == 201, resp.text
    goal = resp.json()
    created = await client.post(
        f"/api/learning/goals/{goal['id']}/topics", json={"topics": topics}
    )
    assert created.status_code == 201, created.text
    return goal, created.json()


async def _prepare(client: AsyncClient, goal_id: str) -> dict:
    resp = await client.post(f"/api/learning/goals/{goal_id}/daily/prepare")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_prepare_builds_a_runnable_day_without_llm(client):
    goal, _ = await _make_goal(client, ["Пределы", "Производные"])

    data = await _prepare(client, goal["id"])

    assert data["completed"] is False
    assert data["steps"]
    kinds = [step["kind"] for step in data["steps"]]
    assert "theory" in kinds
    assert "practice" in kinds
    assert data["total_minutes"] > 0
    assert all(step["minutes"] >= 5 for step in data["steps"])
    assert all(step["item_id"] for step in data["steps"])


async def test_agenda_includes_every_goal_with_work_today(client):
    first, _ = await _make_goal(client, ["Пределы"])
    second, _ = await _make_goal(client, ["Производные"])
    for goal in (first, second):
        plan = await client.post(
            f"/api/learning/goals/{goal['id']}/plan",
            json={"days": 5, "minutes_per_day": 60},
        )
        assert plan.status_code == 200, plan.text

    resp = await client.post("/api/learning/daily/prepare")

    assert resp.status_code == 200, resp.text
    agenda = resp.json()
    assert agenda["date"] == datetime.now(timezone.utc).date().isoformat()
    assert {session["goal_id"] for session in agenda["sessions"]} == {
        first["id"],
        second["id"],
    }
    assert all(session["steps"] for session in agenda["sessions"])
    assert agenda["total_minutes"] == sum(
        session["total_minutes"] for session in agenda["sessions"]
    )


async def test_agenda_skips_future_only_and_goalless_plans(
    client, db_session
):
    future, _ = await _make_goal(client, ["Пределы"])
    no_plan, _ = await _make_goal(client, ["Производные"])
    plan = (
        await client.post(
            f"/api/learning/goals/{future['id']}/plan",
            json={"days": 5, "minutes_per_day": 60},
        )
    ).json()
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    rows = (
        (
            await db_session.execute(
                select(LearningPlanItem).where(
                    LearningPlanItem.goal_id == uuid.UUID(future["id"])
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.day = tomorrow
    await db_session.commit()
    assert plan["days"]

    resp = await client.post("/api/learning/daily/prepare")

    assert resp.status_code == 200, resp.text
    agenda = resp.json()
    assert future["id"] not in {
        session["goal_id"] for session in agenda["sessions"]
    }
    # Opening "Сегодня" must not create a plan for a goal that had none.
    stored = (
        await client.get(f"/api/learning/goals/{no_plan['id']}/plan")
    ).json()
    assert not stored["days"]


async def test_agenda_carries_an_overdue_plan_forward(client):
    goal, topics = await _make_goal(client, ["Пределы", "Производные"])
    plan = (
        await client.post(
            f"/api/learning/goals/{goal['id']}/plan",
            json={"days": 5, "minutes_per_day": 60},
        )
    ).json()
    item = next(iter(plan["days"].values()))[0]
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    patched = await client.patch(
        f"/api/learning/goals/{goal['id']}/plan/{item['id']}",
        json={"day": yesterday.isoformat()},
    )
    assert patched.status_code == 200, patched.text

    agenda = (await client.post("/api/learning/daily/prepare")).json()
    session = next(
        row for row in agenda["sessions"] if row["goal_id"] == goal["id"]
    )

    assert session["shifted_days"] == 1
    assert session["scheduled_for"] == today.isoformat()
    assert topics[0]["title"] in session["carried_topics"]
    stored = (await client.get(f"/api/learning/goals/{goal['id']}/plan")).json()
    moved = [
        row
        for rows in stored["days"].values()
        for row in rows
        if row["id"] == item["id"]
    ]
    assert moved and moved[0]["day"] == today.isoformat()


async def test_two_study_topics_on_one_day_get_their_own_practice(client, db_session):
    goal, topics = await _make_goal(client, ["Пределы", "Производные"])
    today = datetime.now(timezone.utc).date()
    db_session.add_all(
        [
            LearningPlanItem(
                goal_id=uuid.UUID(goal["id"]),
                topic_id=uuid.UUID(topic["id"]),
                item_type="study",
                day=today,
                planned_minutes=15,
                status="pending",
                reason="тест",
                rejected=False,
            )
            for topic in topics
        ]
    )
    await db_session.commit()

    data = await _prepare(client, goal["id"])

    assert data["scheduled_for"] == today.isoformat()
    assert [step["kind"] for step in data["steps"]] == [
        "theory",
        "practice",
        "theory",
        "practice",
    ]
    assert [step["topic_id"] for step in data["steps"] if step["kind"] == "theory"] == [
        topic["id"] for topic in topics
    ]

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/daily/complete",
        json={
            "day": data["scheduled_for"],
            "topic_ids": [topic["id"] for topic in topics],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_items"] == 2


async def test_overdue_day_is_carried_forward_and_dates_move(client):
    goal, topics = await _make_goal(client, ["Пределы", "Производные"])
    plan = (
        await client.post(
            f"/api/learning/goals/{goal['id']}/plan",
            json={"days": 5, "minutes_per_day": 60},
        )
    ).json()
    item = next(iter(plan["days"].values()))[0]

    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    patched = await client.patch(
        f"/api/learning/goals/{goal['id']}/plan/{item['id']}",
        json={"day": yesterday.isoformat()},
    )
    assert patched.status_code == 200, patched.text

    data = await _prepare(client, goal["id"])

    assert data["shifted_days"] == 1
    assert topics[0]["title"] in data["carried_topics"]
    assert data["steps"]

    stored = (await client.get(f"/api/learning/goals/{goal['id']}/plan")).json()
    moved = [
        row
        for rows in stored["days"].values()
        for row in rows
        if row["id"] == item["id"]
    ]
    assert moved and moved[0]["day"] == today.isoformat()


async def test_finishing_marks_only_the_finished_day(client):
    goal, _ = await _make_goal(client, ["Пределы", "Производные"])
    data = await _prepare(client, goal["id"])
    day = data["scheduled_for"]
    topic_ids = list({step["topic_id"] for step in data["steps"]})

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/daily/complete",
        json={"day": day, "topic_ids": topic_ids},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_items"] >= 1

    stored = (await client.get(f"/api/learning/goals/{goal['id']}/plan")).json()
    done = [row for row in stored["days"].get(day, []) if row["status"] == "done"]
    assert done


async def test_a_topic_replanned_to_another_day_stays_pending(client, db_session):
    """A delayed weak topic must not be marked done on its old day.

    Practice answers replan in the background. If that moves the topic, the
    completion call only knows the day it prepared. Marking a future item done
    would skip the repeat the learner needs; leaving it pending keeps it first.
    """
    goal, _ = await _make_goal(client, ["Пределы", "Производные"])
    data = await _prepare(client, goal["id"])
    day = data["scheduled_for"]
    topic_ids = list({step["topic_id"] for step in data["steps"]})

    tomorrow = date.fromisoformat(day) + timedelta(days=1)
    rows = (
        (
            await db_session.execute(
                select(LearningPlanItem).where(
                    LearningPlanItem.goal_id == uuid.UUID(goal["id"]),
                    LearningPlanItem.day == date.fromisoformat(day),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.day = tomorrow
    await db_session.commit()

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/daily/complete",
        json={"day": day, "topic_ids": topic_ids},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_items"] == 0

    stored = (await client.get(f"/api/learning/goals/{goal['id']}/plan")).json()
    moved = stored["days"].get(tomorrow.isoformat(), [])
    assert moved
    assert all(row["status"] == "pending" for row in moved)


async def test_existing_drill_opens_without_a_model(client, db_session, monkeypatch):
    goal, topics = await _make_goal(client, ["Пределы"])
    user = (await db_session.execute(select(User).limit(1))).scalar_one()
    topic_id = uuid.UUID(topics[0]["id"])
    db_session.add(
        TopicDrill(
            goal_id=uuid.UUID(goal["id"]),
            user_id=user.id,
            topic_id=topic_id,
            required_count=3,
            question_ids=[],
            status="pending",
        )
    )
    await db_session.commit()

    def _unexpected_provider_check():
        raise AssertionError("an existing drill must not require an LLM")

    monkeypatch.setattr(adaptive_router, "_require_llm", _unexpected_provider_check)

    resp = await client.post(f"/api/learning/topics/{topic_id}/drill")

    assert resp.status_code == 201, resp.text
    assert resp.json()["topic_id"] == str(topic_id)


async def test_weak_topic_opens_with_a_longer_review(client, db_session):
    goal, topics = await _make_goal(client, ["Пределы", "Производные"])
    await client.post(
        f"/api/learning/goals/{goal['id']}/plan",
        json={"days": 5, "minutes_per_day": 60},
    )

    user = (await db_session.execute(select(User).limit(1))).scalar_one()
    topic_id = uuid.UUID(topics[0]["id"])
    await apply_scored_answer(
        db_session,
        goal_id=uuid.UUID(goal["id"]),
        user_id=user.id,
        topic_id=topic_id,
        final_score=0.3,
    )
    question = LearningQuestion(topic_id=topic_id, text="Что такое предел?")
    db_session.add(question)
    await db_session.flush()
    db_session.add(
        LearningAnswer(
            question_id=question.id,
            user_id=user.id,
            text="Не помню",
            score=0.3,
            raw_score=0.3,
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    data = await _prepare(client, goal["id"])
    first = data["steps"][0]

    assert first["kind"] == "review"
    assert first["topic_id"] == str(topic_id)
    assert first["minutes"] == 10
    # A failed topic is not merely warmed up before a new lesson: the lesson
    # itself stays on it, so the next day actually repeats the material.
    assert all(step["topic_id"] == str(topic_id) for step in data["steps"])


async def test_theory_gets_the_larger_share_of_a_lesson(client, db_session):
    goal, topics = await _make_goal(client, ["Пределы"])
    today = datetime.now(timezone.utc).date()
    db_session.add(
        LearningPlanItem(
            goal_id=uuid.UUID(goal["id"]),
            topic_id=uuid.UUID(topics[0]["id"]),
            item_type="study",
            day=today,
            planned_minutes=30,
            status="pending",
            reason="тест",
            rejected=False,
        )
    )
    await db_session.commit()

    data = await _prepare(client, goal["id"])
    theory = next(step for step in data["steps"] if step["kind"] == "theory")
    practice = next(step for step in data["steps"] if step["kind"] == "practice")

    assert theory["minutes"] >= 10
    assert theory["minutes"] > practice["minutes"]
    assert theory["minutes"] + practice["minutes"] >= 30


async def test_pull_next_day_moves_work_and_leaves_40_percent_review(
    client, db_session
):
    goal, topics = await _make_goal(client, ["Пределы", "Производные"])
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    db_session.add(
        LearningPlanItem(
            goal_id=uuid.UUID(goal["id"]),
            topic_id=uuid.UUID(topics[0]["id"]),
            item_type="review",
            day=today,
            planned_minutes=10,
            status="done",
            reason="сегодняшнее занятие",
            rejected=False,
        )
    )
    db_session.add_all(
        [
            LearningPlanItem(
                goal_id=uuid.UUID(goal["id"]),
                topic_id=uuid.UUID(topic["id"]),
                item_type="study",
                day=tomorrow,
                planned_minutes=minutes,
                status="pending",
                reason="тест",
                rejected=False,
            )
            for topic, minutes in zip(topics, (30, 20))
        ]
    )
    await db_session.commit()

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/daily/pull",
        json={"day": tomorrow.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    pulled = resp.json()
    assert pulled["moved_items"] == 2
    assert pulled["review_minutes"] == 20  # 40% of 50 minutes

    plan = (await client.get(f"/api/learning/goals/{goal['id']}/plan")).json()
    today_rows = plan["days"][today.isoformat()]
    moved = [row for row in today_rows if row["status"] == "pending"]
    assert len(moved) == 2
    assert all(row["day"] == today.isoformat() for row in moved)

    tomorrow_rows = plan["days"][tomorrow.isoformat()]
    assert len(tomorrow_rows) == 1
    review = tomorrow_rows[0]
    assert review["item_type"] == "review"
    assert review["planned_minutes"] == 20
    assert review["reason"].startswith(daily.PULL_REVIEW_REASON_PREFIX)


async def test_pull_is_blocked_while_today_has_pending_work(client, db_session):
    goal, topics = await _make_goal(client, ["Пределы"])
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    db_session.add_all(
        [
            LearningPlanItem(
                goal_id=uuid.UUID(goal["id"]),
                topic_id=uuid.UUID(topics[0]["id"]),
                item_type="study",
                day=today,
                planned_minutes=20,
                status="pending",
                reason="сегодня",
                rejected=False,
            ),
            LearningPlanItem(
                goal_id=uuid.UUID(goal["id"]),
                topic_id=uuid.UUID(topics[0]["id"]),
                item_type="study",
                day=tomorrow,
                planned_minutes=30,
                status="pending",
                reason="завтра",
                rejected=False,
            ),
        ]
    )
    await db_session.commit()

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/daily/pull",
        json={"day": tomorrow.isoformat()},
    )
    assert resp.status_code == 409, resp.text


async def test_tomorrow_after_an_early_pull_is_a_short_review(
    client, db_session, monkeypatch
):
    goal, topics = await _make_goal(client, ["Пределы", "Производные"])
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    db_session.add_all(
        [
            LearningPlanItem(
                goal_id=uuid.UUID(goal["id"]),
                topic_id=uuid.UUID(topic["id"]),
                item_type="study",
                day=tomorrow,
                planned_minutes=minutes,
                status="pending",
                reason="тест",
                rejected=False,
            )
            for topic, minutes in zip(topics, (30, 20))
        ]
    )
    await db_session.commit()

    pulled = (
        await client.post(
            f"/api/learning/goals/{goal['id']}/daily/pull",
            json={"day": tomorrow.isoformat()},
        )
    ).json()
    assert pulled["review_minutes"] == 20

    # Finish the moved work today, then look at tomorrow. The shortened review
    # must stay short instead of turning into a full lesson again.
    completed = await client.post(
        f"/api/learning/goals/{goal['id']}/daily/complete",
        json={
            "day": today.isoformat(),
            "topic_ids": [topic["id"] for topic in topics],
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["completed_items"] == 2

    monkeypatch.setattr(
        daily,
        "_utcnow",
        lambda: datetime.combine(tomorrow, time(12), tzinfo=timezone.utc),
    )
    data = await _prepare(client, goal["id"])

    assert data["scheduled_for"] == tomorrow.isoformat()
    assert len(data["steps"]) == 1
    assert data["steps"][0]["kind"] == "review"
    assert data["steps"][0]["minutes"] == 20
    assert data["total_minutes"] == 20
    assert data["message"] and "40%" in data["message"]


async def test_pull_ahead_halves_original_and_copies_full_time(client, db_session):
    goal, topics = await _make_goal(client, ["Пределы"])
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    original = LearningPlanItem(
        goal_id=uuid.UUID(goal["id"]),
        topic_id=uuid.UUID(topics[0]["id"]),
        item_type="study",
        day=tomorrow,
        planned_minutes=30,
        status="pending",
        reason="завтрашняя тема",
        rejected=False,
    )
    db_session.add(original)
    await db_session.commit()

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/plan/{original.id}/pull-ahead",
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The copy on today keeps the full time.
    assert body["pulled"]["day"] == today.isoformat()
    assert body["pulled"]["planned_minutes"] == 30
    assert body["pulled"]["status"] == "pending"
    # The original stays on its day, halved but never below 5 minutes.
    assert body["original"]["day"] == tomorrow.isoformat()
    assert body["original"]["planned_minutes"] == 15

    plan = (await client.get(f"/api/learning/goals/{goal['id']}/plan")).json()
    assert len(plan["days"][today.isoformat()]) == 1
    assert len(plan["days"][tomorrow.isoformat()]) == 1


async def test_pull_ahead_works_for_practice_assignments(client, db_session):
    goal, topics = await _make_goal(client, ["Пределы"])
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    practice = LearningPlanItem(
        goal_id=uuid.UUID(goal["id"]),
        topic_id=uuid.UUID(topics[0]["id"]),
        item_type="practice",
        day=tomorrow,
        planned_minutes=20,
        status="pending",
        reason="контрольное задание по теме",
        rejected=False,
    )
    db_session.add(practice)
    await db_session.commit()

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/plan/{practice.id}/pull-ahead",
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pulled"]["item_type"] == "practice"
    assert body["pulled"]["planned_minutes"] == 20
    assert body["original"]["planned_minutes"] == 10


async def test_pull_ahead_is_blocked_while_todays_own_work_is_open(
    client, db_session
):
    goal, topics = await _make_goal(client, ["Пределы"])
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    db_session.add_all(
        [
            LearningPlanItem(
                goal_id=uuid.UUID(goal["id"]),
                topic_id=uuid.UUID(topics[0]["id"]),
                item_type="study",
                day=today,
                planned_minutes=20,
                status="pending",
                reason="сегодня",
                rejected=False,
            ),
            LearningPlanItem(
                goal_id=uuid.UUID(goal["id"]),
                topic_id=uuid.UUID(topics[0]["id"]),
                item_type="study",
                day=tomorrow,
                planned_minutes=30,
                status="pending",
                reason="завтра",
                rejected=False,
            ),
        ]
    )
    await db_session.commit()

    future = (
        await db_session.execute(
            select(LearningPlanItem).where(LearningPlanItem.day == tomorrow)
        )
    ).scalar_one()

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/plan/{future.id}/pull-ahead",
        json={},
    )
    assert resp.status_code == 409, resp.text


async def test_pull_ahead_cannot_be_repeated_for_the_same_item(
    client, db_session
):
    goal, topics = await _make_goal(client, ["Пределы"])
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)

    original = LearningPlanItem(
        goal_id=uuid.UUID(goal["id"]),
        topic_id=uuid.UUID(topics[0]["id"]),
        item_type="study",
        day=tomorrow,
        planned_minutes=30,
        status="pending",
        reason="завтра",
        rejected=False,
    )
    db_session.add(original)
    await db_session.commit()

    first = await client.post(
        f"/api/learning/goals/{goal['id']}/plan/{original.id}/pull-ahead",
        json={},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        f"/api/learning/goals/{goal['id']}/plan/{original.id}/pull-ahead",
        json={},
    )
    assert second.status_code == 409, second.text

    # The refusal did not halve the original a second time.
    await db_session.refresh(original)
    assert original.planned_minutes == 15


async def test_pull_ahead_refuses_the_exam(client, db_session):
    goal, _ = await _make_goal(client, ["Пределы"])
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)

    exam = LearningPlanItem(
        goal_id=uuid.UUID(goal["id"]),
        topic_id=None,
        item_type="exam",
        day=tomorrow,
        planned_minutes=60,
        status="pending",
        reason="экзамен",
        rejected=False,
    )
    db_session.add(exam)
    await db_session.commit()

    resp = await client.post(
        f"/api/learning/goals/{goal['id']}/plan/{exam.id}/pull-ahead",
        json={},
    )
    assert resp.status_code == 409, resp.text


async def test_finished_practice_series_marks_control_items_done(
    client, db_session
):
    goal, topics = await _make_goal(client, ["Пределы"])
    future = datetime.now(timezone.utc).date() + timedelta(days=3)

    practice = LearningPlanItem(
        goal_id=uuid.UUID(goal["id"]),
        topic_id=uuid.UUID(topics[0]["id"]),
        item_type="practice",
        day=future,
        planned_minutes=20,
        status="pending",
        reason="контрольное задание по теме",
        rejected=False,
    )
    db_session.add(practice)
    await db_session.commit()

    count = await adaptive_router._mark_topic_practice_done(
        db_session, uuid.UUID(goal["id"]), uuid.UUID(topics[0]["id"])
    )
    await db_session.commit()

    assert count == 1
    await db_session.refresh(practice)
    assert practice.status == "done"
    assert practice.actual_minutes == 20


async def test_plan_exposes_task_only_and_exam_days(client):
    goal, _ = await _make_goal(client, ["Пределы", "Производные"])

    plan = (
        await client.post(
            f"/api/learning/goals/{goal['id']}/plan",
            json={"days": 5, "minutes_per_day": 60},
        )
    ).json()
    items = [item for items in plan["days"].values() for item in items]
    practice = next(
        item
        for item in items
        if item["item_type"] == "practice" and item["topic_id"] is None
    )
    exam = next(item for item in items if item["item_type"] == "exam")

    assert practice["topic_id"] is None
    assert exam["topic_id"] is None
    assert practice["title"] == "Практика для допуска к экзамену"
    assert exam["title"] == "Экзамен"
    assert practice["id"]
    assert exam["id"]
    assert practice["day"] < exam["day"]


async def test_practice_day_is_task_only_and_completes_by_item_id(
    client, db_session
):
    goal, topics = await _make_goal(client, ["Пределы", "Производные"])
    today = datetime.now(timezone.utc).date()
    practice = LearningPlanItem(
        goal_id=uuid.UUID(goal["id"]),
        topic_id=None,
        item_type="practice",
        day=today,
        planned_minutes=60,
        status="pending",
        reason="решаем задания по всем темам, чтобы открыть экзамен",
        rejected=False,
    )
    db_session.add(practice)
    await db_session.commit()
    practice_id = str(practice.id)

    data = await _prepare(client, goal["id"])

    assert [step["kind"] for step in data["steps"]] == ["exam_prep"]
    step = data["steps"][0]
    assert step["title"] == "Практика для допуска к экзамену"
    assert step["item_id"] == practice_id
    assert step["topic_id"] in {topic["id"] for topic in topics}
    assert data["total_minutes"] == 60
    assert data["message"] and "день практики" in data["message"].lower()

    completed = await client.post(
        f"/api/learning/goals/{goal['id']}/daily/complete",
        json={
            "day": today.isoformat(),
            "topic_ids": [],
            "item_ids": [practice_id],
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["completed_items"] == 1

    stored = (
        await client.get(f"/api/learning/goals/{goal['id']}/plan")
    ).json()
    rows = [item for items in stored["days"].values() for item in items]
    assert next(item for item in rows if item["id"] == practice_id)["status"] == "done"


async def test_exam_day_is_its_own_step(client, db_session):
    goal, _ = await _make_goal(client, ["Пределы"])
    today = datetime.now(timezone.utc).date()
    db_session.add(
        LearningPlanItem(
            goal_id=uuid.UUID(goal["id"]),
            topic_id=None,
            item_type="exam",
            day=today,
            planned_minutes=60,
            status="pending",
            reason="итоговая проверка знаний по цели",
            rejected=False,
        )
    )
    await db_session.commit()

    data = await _prepare(client, goal["id"])

    assert [step["kind"] for step in data["steps"]] == ["exam"]
    assert data["steps"][0]["title"] == "Экзамен"
    assert data["message"] and "день экзамена" in data["message"].lower()
