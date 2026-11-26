"""API tests for the adaptive learning endpoints (TZ section 10).

Follows the pattern of test_api_integration.py: a per-test SQLite database with
the ``get_db`` dependency overridden.

The LLM-backed endpoints are covered with the content functions monkeypatched,
which keeps these tests hermetic while still asserting that the router applies
the deterministic arithmetic correctly (section 6.2 and 6.3).
"""

import os
import tempfile
import uuid
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import database as database_module
from database import Base, get_db
from main import app
from models.learning_goal import LearningPlanItem

# pytest-asyncio runs in strict mode here, so async tests must opt in. Existing
# integration tests do this per-function with @pytest.mark.asyncio; a module
# level marker is equivalent and less noisy.
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    fd, db_path = tempfile.mkstemp(prefix="opentutor-lrn-", suffix=".db")
    os.close(fd)

    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=False,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
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
    await test_engine.dispose()


async def _make_goal(client: AsyncClient, topics: list[str] | None = None) -> dict:
    """Create a goal and optionally seed manual subtopics."""
    resp = await client.post(
        "/api/learning/goals",
        json={
            "topic": "Основы статистики",
            "days": 14,
            "minutes_per_day": 60,
            "depth": "confident",
        },
    )
    assert resp.status_code == 201, resp.text
    goal = resp.json()
    if topics:
        t = await client.post(
            f"/api/learning/goals/{goal['id']}/topics", json={"topics": topics}
        )
        assert t.status_code == 201, t.text
    return goal


# ──────────────────────────────────────────────────────────────
# Goals — TZ /goals (U1)
# ──────────────────────────────────────────────────────────────

class TestGoalSchedule:
    async def test_practice_items_are_named_after_their_topic(self, client):
        """The schedule used to read a title the plan item does not have.

        ``learning_plan_items`` stores no title, so ``item.title`` raised
        ``AttributeError`` and the whole schedule endpoint returned 500 — the
        practice tab then said it could not load the topics, although every
        other tab showed them.
        """
        goal = await _make_goal(client, ["Средние величины"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()

        async with database_module.async_session() as db:
            db.add(
                LearningPlanItem(
                    goal_id=uuid.UUID(goal["id"]),
                    topic_id=uuid.UUID(topics[0]["id"]),
                    item_type="practice",
                    day=date.today(),
                    planned_minutes=20,
                    status="pending",
                    reason="контрольное задание по теме",
                )
            )
            await db.commit()

        resp = await client.get(f"/api/learning/goals/{goal['id']}/schedule")
        assert resp.status_code == 200, resp.text
        items = resp.json()["topics"][0]["practice_items"]
        assert len(items) == 1
        assert items[0]["title"] == "Средние величины"


class TestGoals:
    async def test_create_goal_resolves_relative_deadline(self, client):
        """FR-2.1 accepts a number of days instead of a date."""
        goal = await _make_goal(client)
        assert goal["topic"] == "Основы статистики"
        assert goal["depth"] == "confident"
        assert goal["minutes_per_day"] == 60
        # days=14 must have produced a real calendar deadline.
        assert goal["deadline"] is not None
        assert 13 <= goal["days_left"] <= 14
        assert goal["status"] == "active"

    async def test_absolute_deadline_is_kept(self, client):
        resp = await client.post(
            "/api/learning/goals",
            json={"topic": "Математика", "deadline": "2026-12-31", "minutes_per_day": 45},
        )
        assert resp.status_code == 201
        assert resp.json()["deadline"] == "2026-12-31"

    async def test_empty_topic_is_rejected(self, client):
        resp = await client.post("/api/learning/goals", json={"topic": "   "})
        assert resp.status_code == 422

    async def test_invalid_depth_is_rejected(self, client):
        resp = await client.post(
            "/api/learning/goals", json={"topic": "X", "depth": "impossible"}
        )
        assert resp.status_code == 422

    async def test_list_goals(self, client):
        await _make_goal(client)
        resp = await client.get("/api/learning/goals")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_get_goal_returns_topic_count(self, client):
        goal = await _make_goal(client, ["Средние величины", "Дисперсия"])
        resp = await client.get(f"/api/learning/goals/{goal['id']}")
        assert resp.status_code == 200
        assert resp.json()["topic_count"] == 2

    async def test_unknown_goal_returns_404(self, client):
        resp = await client.get(f"/api/learning/goals/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_update_goal_changes_budget(self, client):
        goal = await _make_goal(client)
        resp = await client.patch(
            f"/api/learning/goals/{goal['id']}",
            json={"minutes_per_day": 120, "depth": "deep", "status": "paused"},
        )
        assert resp.status_code == 200
        assert resp.json()["minutes_per_day"] == 120
        assert resp.json()["depth"] == "deep"
        assert resp.json()["status"] == "paused"

    async def test_delete_goal(self, client):
        goal = await _make_goal(client)
        assert (await client.delete(f"/api/learning/goals/{goal['id']}")).status_code == 204
        assert (await client.get(f"/api/learning/goals/{goal['id']}")).status_code == 404

    async def test_new_goal_becomes_the_active_daily_goal(self, client):
        first = await _make_goal(client)
        assert first["is_active"] is True

        second_resp = await client.post(
            "/api/learning/goals",
            json={"topic": "Физика", "days": 10},
        )
        assert second_resp.status_code == 201
        second = second_resp.json()
        assert second["is_active"] is True

        index = (await client.get("/api/learning/index")).json()
        assert index["active_goal_id"] == second["id"]
        by_id = {goal["id"]: goal for goal in index["unattached"]}
        assert by_id[first["id"]]["is_active"] is False
        assert by_id[second["id"]]["is_active"] is True

    async def test_learner_can_choose_another_active_goal(self, client):
        first = await _make_goal(client)
        second_resp = await client.post(
            "/api/learning/goals",
            json={"topic": "Физика", "days": 10},
        )
        second = second_resp.json()

        chosen = await client.post(f"/api/learning/goals/{first['id']}/activate")
        assert chosen.status_code == 200
        assert chosen.json()["is_active"] is True

        index = (await client.get("/api/learning/index")).json()
        assert index["active_goal_id"] == first["id"]
        by_id = {goal["id"]: goal for goal in index["unattached"]}
        assert by_id[first["id"]]["is_active"] is True
        assert by_id[second["id"]]["is_active"] is False

    async def test_pausing_the_selected_goal_clears_the_choice(self, client):
        goal = await _make_goal(client)
        paused = await client.patch(
            f"/api/learning/goals/{goal['id']}", json={"status": "paused"}
        )
        assert paused.status_code == 200
        assert paused.json()["is_active"] is False

        index = (await client.get("/api/learning/index")).json()
        assert index["active_goal_id"] is None

    async def test_a_paused_goal_cannot_be_selected(self, client):
        goal = await _make_goal(client)
        await client.patch(
            f"/api/learning/goals/{goal['id']}", json={"status": "paused"}
        )
        resp = await client.post(f"/api/learning/goals/{goal['id']}/activate")
        assert resp.status_code == 422

    async def test_deleting_the_selected_goal_clears_the_choice(self, client):
        goal = await _make_goal(client)
        assert (await client.delete(f"/api/learning/goals/{goal['id']}")).status_code == 204

        index = (await client.get("/api/learning/index")).json()
        assert index["active_goal_id"] is None


# ──────────────────────────────────────────────────────────────
# Topics — TZ /topics (U4)
# ──────────────────────────────────────────────────────────────

class TestTopics:
    async def test_manual_topics_are_created_in_order(self, client):
        goal = await _make_goal(client)
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/topics",
            json={"topics": ["Первая тема", "Вторая тема", "Третья тема"]},
        )
        assert resp.status_code == 201
        titles = [t["title"] for t in resp.json()]
        assert titles == ["Первая тема", "Вторая тема", "Третья тема"]
        assert [t["order_index"] for t in resp.json()] == [0, 1, 2]

    async def test_blank_titles_are_dropped(self, client):
        goal = await _make_goal(client)
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/topics", json={"topics": ["  ", "Нормальная"]}
        )
        assert resp.status_code == 201
        assert len(resp.json()) == 1

    async def test_all_blank_topics_is_a_422(self, client):
        goal = await _make_goal(client)
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/topics", json={"topics": ["", "  "]}
        )
        assert resp.status_code == 422

    async def test_list_topics_returns_order(self, client):
        goal = await _make_goal(client, ["A", "B"])
        resp = await client.get(f"/api/learning/goals/{goal['id']}/topics")
        assert resp.status_code == 200
        assert [t["title"] for t in resp.json()] == ["A", "B"]

    async def test_get_single_topic(self, client):
        goal = await _make_goal(client, ["Одна тема"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()
        resp = await client.get(f"/api/learning/topics/{topics[0]['id']}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Одна тема"


# ──────────────────────────────────────────────────────────────
# Plan — TZ /goals/{id}/plan (U3, U7)
# ──────────────────────────────────────────────────────────────

class TestPlan:
    async def test_plan_is_built_without_any_llm(self, client):
        """Section 6.4: scheduling is deterministic, so this must work offline."""
        goal = await _make_goal(client, ["Тема 1", "Тема 2", "Тема 3"])
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/plan",
            json={"days": 7, "minutes_per_day": 60},
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["total_minutes"] > 0
        assert payload["days"]
        assert payload["feasible"] is True
        for day, items in payload["days"].items():
            assert all(item["planned_minutes"] >= 5 for item in items)

    async def test_plan_appears_on_the_get_endpoint(self, client):
        goal = await _make_goal(client, ["Тема 1", "Тема 2"])
        await client.post(
            f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 60}
        )
        resp = await client.get(f"/api/learning/goals/{goal['id']}/plan")
        assert resp.status_code == 200
        assert resp.json()["total_minutes"] > 0

    async def test_plan_without_topics_is_rejected(self, client):
        goal = await _make_goal(client)
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 60}
        )
        assert resp.status_code == 422

    async def test_infeasible_plan_warns_and_suggests(self, client):
        """FR-6.4: warn and offer options rather than silently truncating."""
        goal = await _make_goal(client, ["Очень большая тема"] * 4)
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/plan",
            json={"days": 1, "minutes_per_day": 20},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["feasible"] is False
        assert payload["warnings"]
        assert len(payload["suggestions"]) == 3

    async def test_speed_override_increases_planned_time(self, client):
        goal = await _make_goal(client, ["Тема 1", "Тема 2"])
        fast = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan",
                json={"days": 5, "minutes_per_day": 60, "speed": 1.8},
            )
        ).json()
        normal = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan",
                json={"days": 5, "minutes_per_day": 60, "speed": 1.0},
            )
        ).json()
        assert fast["total_minutes"] > normal["total_minutes"]

    async def test_replan_replaces_items(self, client):
        goal = await _make_goal(client, ["Тема 1", "Тема 2"])
        await client.post(
            f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 60}
        )
        again = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan",
                json={"days": 6, "minutes_per_day": 90},
            )
        ).json()
        stored = (await client.get(f"/api/learning/goals/{goal['id']}/plan")).json()
        assert stored["total_minutes"] == again["total_minutes"]

    async def test_completing_an_item_records_actual_time(self, client):
        """FR-7.4: actual time feeds the speed coefficient."""
        goal = await _make_goal(client, ["Тема 1", "Тема 2"])
        plan = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan",
                json={"days": 5, "minutes_per_day": 60},
            )
        ).json()
        item = next(iter(plan["days"].values()))[0]

        resp = await client.patch(
            f"/api/learning/goals/{goal['id']}/plan/{item['id']}",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"
        assert resp.json()["actual_minutes"] == resp.json()["planned_minutes"]

    async def test_rejecting_an_item_is_recorded(self, client):
        """FR-10.4: the learner can decline a replan change."""
        goal = await _make_goal(client, ["Тема 1", "Тема 2"])
        plan = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan",
                json={"days": 5, "minutes_per_day": 60},
            )
        ).json()
        item = next(iter(plan["days"].values()))[0]
        resp = await client.patch(
            f"/api/learning/goals/{goal['id']}/plan/{item['id']}", json={"rejected": True}
        )
        assert resp.json()["rejected"] is True

    async def test_invalid_plan_item_status_is_rejected(self, client):
        goal = await _make_goal(client, ["Тема 1"])
        plan = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 60}
            )
        ).json()
        item = next(iter(plan["days"].values()))[0]
        resp = await client.patch(
            f"/api/learning/goals/{goal['id']}/plan/{item['id']}", json={"status": "nonsense"}
        )
        assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────
# Progress and graph — TZ /goals/{id}/progress (U9)
# ──────────────────────────────────────────────────────────────

class TestProgressAndGraph:
    async def test_progress_starts_all_topics_as_not_started(self, client):
        goal = await _make_goal(client, ["Тема 1", "Тема 2"])
        resp = await client.get(f"/api/learning/goals/{goal['id']}/progress")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["topic_count"] == 2
        assert payload["by_status"]["not_started"] == 2
        assert payload["forecast"]["level"] in {"high", "medium", "low"}

    async def test_forecast_reasons_are_present(self, client):
        goal = await _make_goal(client, ["Тема 1"])
        payload = (await client.get(f"/api/learning/goals/{goal['id']}/progress")).json()
        assert payload["forecast"]["reasons"]

    async def test_graph_returns_nodes_and_order(self, client):
        goal = await _make_goal(client, ["A", "B"])
        resp = await client.get(f"/api/learning/goals/{goal['id']}/graph")
        assert resp.status_code == 200
        payload = resp.json()
        assert len(payload["nodes"]) == 2
        assert payload["cycle"] is None
        assert set(payload["order"]) == {n["id"] for n in payload["nodes"]}


# ──────────────────────────────────────────────────────────────
# Exam — TZ /goals/{id}/exam (U8, FR-11.1)
# ──────────────────────────────────────────────────────────────

class TestExam:
    async def test_exam_without_answers_is_rejected(self, client):
        goal = await _make_goal(client, ["Тема 1"])
        resp = await client.post(f"/api/learning/goals/{goal['id']}/exam")
        assert resp.status_code == 422

    async def test_exam_prioritises_weak_topics(self, client, monkeypatch):
        """FR-11.1: weak topics must come first in the exam composition."""
        from datetime import datetime, timedelta, timezone as _tz

        goal = await _make_goal(client, ["Слабая", "Сильная"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()

        # Seed one strong and one weak topic state through the ORM.
        from models.learning_goal import TopicState
        from database import async_session

        async with async_session() as db:
            row = await db.get(__import__("models").user.User, list(
                (await db.execute(__import__("sqlalchemy").select(__import__("models").user.User)))
            )[0][0].id)
            db.add_all([
                TopicState(
                    goal_id=uuid.UUID(goal["id"]),
                    user_id=row.id,
                    topic_id=uuid.UUID(topics[0]["id"]),
                    strength=0.2, status="weak",
                    next_review_at=datetime.now(_tz.utc) + timedelta(days=1),
                ),
                TopicState(
                    goal_id=uuid.UUID(goal["id"]),
                    user_id=row.id,
                    topic_id=uuid.UUID(topics[1]["id"]),
                    strength=0.95, status="known",
                    next_review_at=datetime.now(_tz.utc) + timedelta(days=30),
                ),
            ])
            await db.commit()

        resp = await client.post(f"/api/learning/goals/{goal['id']}/exam")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["topics"][0]["focus"] == "weak"
        assert payload["topics"][0]["strength"] == 0.2
        assert payload["weak_share"] > 0.5


# ──────────────────────────────────────────────────────────────
# Review — TZ /goals/{id}/review (U6)
# ──────────────────────────────────────────────────────────────

class TestReview:
    async def test_review_without_history_is_rejected(self, client):
        goal = await _make_goal(client, ["Тема 1"])
        resp = await client.post(f"/api/learning/goals/{goal['id']}/review")
        assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────
# Profile — FR-1.2, FR-1.3
# ──────────────────────────────────────────────────────────────

class TestProfile:
    async def test_profile_is_created_on_first_read(self, client):
        resp = await client.get("/api/learning/profile")
        assert resp.status_code == 200
        assert resp.json()["timezone"] == "UTC"
        assert resp.json()["language"] == "ru"

    async def test_profile_can_be_updated(self, client):
        await client.get("/api/learning/profile")
        resp = await client.patch(
            "/api/learning/profile",
            json={
                "timezone": "Europe/Moscow",
                "quiet_hours_start": 23,
                "quiet_hours_end": 7,
                "gamification_enabled": False,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["timezone"] == "Europe/Moscow"
        assert body["quiet_hours_start"] == 23
        assert body["gamification_enabled"] is False

    async def test_quiet_hours_are_validated(self, client):
        await client.get("/api/learning/profile")
        resp = await client.patch("/api/learning/profile", json={"quiet_hours_start": 99})
        assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────
# LLM-gated endpoints must fail clearly when no provider exists
# ──────────────────────────────────────────────────────────────

class TestLLMRequired:
    async def test_diagnostic_start_is_gated_without_llm(self, client, monkeypatch):
        import routers.adaptive_learning as router_module

        def _boom():
            raise router_module.ValidationError("no provider")

        monkeypatch.setattr(router_module, "_require_llm", _boom)
        goal = await _make_goal(client, ["Тема 1"])
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/diagnostic/start", json={}
        )
        assert resp.status_code == 422

    async def test_explain_is_gated_without_llm(self, client, monkeypatch):
        import routers.adaptive_learning as router_module

        def _boom():
            raise router_module.ValidationError("no provider")

        monkeypatch.setattr(router_module, "_require_llm", _boom)
        goal = await _make_goal(client, ["Тема 1"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()
        resp = await client.post(
            f"/api/learning/topics/{topics[0]['id']}/explain", json={"style": "short"}
        )
        assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────
# Answer grading — section 6.2 arithmetic applied by the router
# ──────────────────────────────────────────────────────────────

class TestAnswerGrading:
    async def test_hint_penalty_is_applied_server_side(self, client, monkeypatch):
        """Section 6.2: the client sends hints_used, the server applies the penalty."""
        import routers.adaptive_learning as router_module
        from services.adaptive_learning import content

        async def _fake_task(**kwargs):
            return {
                "text": "Объясните своими словами, что такое среднее.",
                "question_type": "explain_own_words",
                "criteria": {
                    "correctness": "верное определение",
                    "completeness": "называет виды средних",
                    "understanding": "корректно применяет",
                },
                "hints": ["Подсказка 1", "Подсказка 2", "Подсказка 3"],
                "difficulty": 0.5,
                "expected_answer": "",
            }

        async def _fake_grade(**kwargs):
            return {
                "breakdown": {"correctness": 0.8, "completeness": 0.6, "understanding": 0.7},
                "feedback": "Хорошо, но пропущено взвешенное среднее.",
                "error_types": ["incomplete"],
            }

        monkeypatch.setattr(router_module, "_require_llm", lambda: None)
        monkeypatch.setattr(content, "generate_task", _fake_task)
        monkeypatch.setattr(content, "grade_answer", _fake_grade)

        goal = await _make_goal(client, ["Средние величины"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()

        q = await client.post(f"/api/learning/topics/{topics[0]['id']}/question", json={})
        assert q.status_code == 201, q.text
        question = q.json()
        assert question["question_type"] == "explain_own_words"
        assert len(question["hints"]) == 3

        resp = await client.post(
            f"/api/learning/questions/{question['id']}/answer",
            json={"text": "Это сумма значений, делённая на их количество", "hints_used": 2},
        )
        assert resp.status_code == 201, resp.text
        payload = resp.json()

        raw = round((0.8 + 0.6 + 0.7) / 3, 4)
        assert payload["raw_score"] == pytest.approx(raw)
        assert payload["hint_penalty"] == pytest.approx(0.4)
        assert payload["final_score"] == pytest.approx(raw * 0.6, abs=1e-3)
        assert payload["error_types"] == ["incomplete"]
        # Section 6.3 then derives the interval in code.
        assert payload["interval_days"] >= 1
        assert payload["next_review_at"] is not None

    async def test_hint_endpoint_returns_nothing_without_consent(self, client, monkeypatch):
        import routers.adaptive_learning as router_module
        from services.adaptive_learning import content

        async def _fake_task(**kwargs):
            return {
                "text": "Вопрос", "question_type": "find_error",
                "criteria": {"correctness": "x", "completeness": "y", "understanding": "z"},
                "hints": ["первый", "второй", "третий"], "difficulty": 0.5,
                "expected_answer": "",
            }

        monkeypatch.setattr(router_module, "_require_llm", lambda: None)
        monkeypatch.setattr(content, "generate_task", _fake_task)

        goal = await _make_goal(client, ["Тема"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()
        question = (
            await client.post(f"/api/learning/topics/{topics[0]['id']}/question", json={})
        ).json()

        first = await client.get(f"/api/learning/questions/{question['id']}/hints/1")
        assert first.status_code == 200
        assert first.json()["hint"] == "первый"

        # FR-8.6: asking beyond the prepared levels is an error, not a silent null.
        beyond = await client.get(f"/api/learning/questions/{question['id']}/hints/4")
        assert beyond.status_code == 422

    async def test_dispute_records_the_challenge(self, client, monkeypatch):
        import routers.adaptive_learning as router_module
        from services.adaptive_learning import content

        async def _fake_task(**kwargs):
            return {
                "text": "Вопрос", "question_type": "explain_own_words",
                "criteria": {"correctness": "a", "completeness": "b", "understanding": "c"},
                "hints": ["h1", "h2", "h3"], "difficulty": 0.5, "expected_answer": "",
            }

        async def _fake_grade(**kwargs):
            return {
                "breakdown": {"correctness": 0.5, "completeness": 0.5, "understanding": 0.5},
                "feedback": "средне", "error_types": [],
            }

        monkeypatch.setattr(router_module, "_require_llm", lambda: None)
        monkeypatch.setattr(content, "generate_task", _fake_task)
        monkeypatch.setattr(content, "grade_answer", _fake_grade)

        goal = await _make_goal(client, ["Тема"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()
        question = (
            await client.post(f"/api/learning/topics/{topics[0]['id']}/question", json={})
        ).json()
        answer = (
            await client.post(
                f"/api/learning/questions/{question['id']}/answer", json={"text": "ответ"}
            )
        ).json()

        resp = await client.post(
            f"/api/learning/answers/{answer['id']}/dispute",
            json={"note": "Я считаю, что ответ верный: поясните"},
        )
        assert resp.status_code == 200

    async def test_invalid_confidence_label_is_rejected(self, client, monkeypatch):
        import routers.adaptive_learning as router_module
        from services.adaptive_learning import content

        async def _fake_task(**kwargs):
            return {
                "text": "Вопрос", "question_type": "apply_situation",
                "criteria": {"correctness": "a", "completeness": "b", "understanding": "c"},
                "hints": ["h1", "h2", "h3"], "difficulty": 0.5, "expected_answer": "",
            }

        monkeypatch.setattr(router_module, "_require_llm", lambda: None)
        monkeypatch.setattr(content, "generate_task", _fake_task)

        goal = await _make_goal(client, ["Тема"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()
        question = (
            await client.post(f"/api/learning/topics/{topics[0]['id']}/question", json={})
        ).json()

        resp = await client.post(
            f"/api/learning/questions/{question['id']}/answer",
            json={"text": "x", "confidence": "very_sure"},
        )
        assert resp.status_code == 422

    async def test_hints_used_is_capped_at_three(self, client, monkeypatch):
        import routers.adaptive_learning as router_module
        from services.adaptive_learning import content

        async def _fake_task(**kwargs):
            return {
                "text": "Вопрос", "question_type": "compare",
                "criteria": {"correctness": "a", "completeness": "b", "understanding": "c"},
                "hints": ["h1", "h2", "h3"], "difficulty": 0.5, "expected_answer": "",
            }

        monkeypatch.setattr(router_module, "_require_llm", lambda: None)
        monkeypatch.setattr(content, "generate_task", _fake_task)

        goal = await _make_goal(client, ["Тема"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()
        question = (
            await client.post(f"/api/learning/topics/{topics[0]['id']}/question", json={})
        ).json()

        resp = await client.post(
            f"/api/learning/questions/{question['id']}/answer",
            json={"text": "x", "hints_used": 99},
        )
        assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────
# Diagnostic — FR-4.1, FR-4.5
# ──────────────────────────────────────────────────────────────

class TestDiagnostic:
    async def _prepare(self, client, monkeypatch):
        import routers.adaptive_learning as router_module
        from services.adaptive_learning import content

        async def _fake_diag(**kwargs):
            return {
                "question": "Сможете объяснить, как считается среднее взвешенное?",
                "technique": "own_words",
                "difficulty": kwargs.get("target_difficulty", 0.5),
                "criteria": {"correctness": "a", "completeness": "b", "understanding": "c"},
            }

        async def _fake_grade(**kwargs):
            return {
                "breakdown": {"correctness": 0.9, "completeness": 0.8, "understanding": 0.85},
                "feedback": "хорошо", "error_types": [],
            }

        monkeypatch.setattr(router_module, "_require_llm", lambda: None)
        monkeypatch.setattr(content, "generate_diagnostic_question", _fake_diag)
        monkeypatch.setattr(content, "grade_answer", _fake_grade)
        return _fake_grade

    async def test_start_returns_a_question_not_a_level_prompt(self, client, monkeypatch):
        await self._prepare(client, monkeypatch)
        goal = await _make_goal(client, ["Средние величины", "Дисперсия"])

        resp = await client.post(f"/api/learning/goals/{goal['id']}/diagnostic/start", json={})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["question"]
        assert body["done"] is False
        assert body["reply_count" if "reply_count" in body else "turn_index"] is not None
        # FR-4.1: the returned turn must not ask about level.
        from services.adaptive_learning.content import assert_no_level_question

        assert_no_level_question(body["question"])

    async def test_dialogue_stays_within_the_reply_budget(self, client, monkeypatch):
        """FR-4.1: 4-8 replies, then it finishes on its own."""
        await self._prepare(client, monkeypatch)
        goal = await _make_goal(client, ["A", "B"])

        start = await client.post(
            f"/api/learning/goals/{goal['id']}/diagnostic/start", json={"max_replies": 4}
        )
        session_id = start.json()["session_id"]

        turns = 1
        reply = start
        for _ in range(10):
            reply = await client.post(
                f"/api/learning/goals/{goal['id']}/diagnostic/{session_id}/reply",
                json={"text": "ну примерно делим сумму на количество"},
            )
            assert reply.status_code == 200, reply.text
            if reply.json()["done"]:
                break
            turns += 1

        assert reply.json()["done"] is True
        assert 4 <= turns <= 8

    async def test_final_state_exposes_estimates_for_correction(self, client, monkeypatch):
        """FR-4.5: the learner sees the inferred level and may correct it."""
        await self._prepare(client, monkeypatch)
        goal = await _make_goal(client, ["A", "B"])
        start = await client.post(
            f"/api/learning/goals/{goal['id']}/diagnostic/start", json={"max_replies": 4}
        )
        session_id = start.json()["session_id"]

        reply = start
        for _ in range(10):
            reply = await client.post(
                f"/api/learning/goals/{goal['id']}/diagnostic/{session_id}/reply",
                json={"text": "ответ"},
            )
            if reply.json()["done"]:
                break

        state = await client.get(
            f"/api/learning/goals/{goal['id']}/diagnostic/{session_id}"
        )
        assert state.status_code == 200
        assert state.json()["status"] == "completed"
        assert len(state.json()["estimates"]) >= 1
        assert state.json()["finished_reason"]

    async def test_correction_overwrites_the_estimate(self, client, monkeypatch):
        await self._prepare(client, monkeypatch)
        goal = await _make_goal(client, ["A", "B"])
        start = await client.post(
            f"/api/learning/goals/{goal['id']}/diagnostic/start", json={"max_replies": 4}
        )
        session_id = start.json()["session_id"]
        reply = start
        for _ in range(10):
            reply = await client.post(
                f"/api/learning/goals/{goal['id']}/diagnostic/{session_id}/reply",
                json={"text": "ответ"},
            )
            if reply.json()["done"]:
                break

        state = await client.get(f"/api/learning/goals/{goal['id']}/diagnostic/{session_id}")
        topic_id = state.json()["estimates"][0]["id"]

        resp = await client.patch(
            f"/api/learning/goals/{goal['id']}/diagnostic/{session_id}",
            json={"estimates": {topic_id: 0.95}},
        )
        assert resp.status_code == 200
        corrected = next(e for e in resp.json()["estimates"] if e["id"] == topic_id)
        assert corrected["estimate"] == 0.95
        assert corrected["band"] == "strong"

    async def test_a_level_question_is_rejected_by_the_guard(self, client, monkeypatch):
        """FR-4.1 enforced in code, not just in the prompt."""
        import routers.adaptive_learning as router_module
        from services.adaptive_learning import content

        async def _bad_diag(**kwargs):
            return {
                "question": "Какой у вас уровень знаний по этой теме?",
                "technique": "own_words",
                "difficulty": 0.5,
                "criteria": {},
            }

        monkeypatch.setattr(router_module, "_require_llm", lambda: None)
        monkeypatch.setattr(content, "generate_diagnostic_question", _bad_diag)

        goal = await _make_goal(client, ["Тема"])
        with pytest.raises(content.ContentGenerationError):
            await client.post(f"/api/learning/goals/{goal['id']}/diagnostic/start", json={})