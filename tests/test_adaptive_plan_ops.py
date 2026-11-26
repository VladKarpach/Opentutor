"""Tests for the remaining backend pieces: plan_ops and the new endpoints.

Covers FR-2.2, FR-2.3, FR-5.6, FR-6.6, FR-10.5, FR-12.2 and FR-15.1.
"""

import os
import tempfile
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import database as database_module
from database import Base, get_db
from main import app
from services.adaptive_learning.plan_ops import split_daily_minutes

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    fd, db_path = tempfile.mkstemp(prefix="opentutor-ops-", suffix=".db")
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
    original = database_module.async_session
    database_module.async_session = factory

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)
    database_module.async_session = original
    await engine.dispose()


async def _goal_with_topics(client, topics=("Тема 1", "Тема 2")) -> dict:
    resp = await client.post(
        "/api/learning/goals",
        json={"topic": "Производные и интегралы для анализа", "days": 10, "minutes_per_day": 60},
    )
    goal = resp.json()
    await client.post(
        f"/api/learning/goals/{goal['id']}/topics", json={"topics": list(topics)}
    )
    return goal


# ──────────────────────────────────────────────────────────────
# FR-2.3 time split
# ──────────────────────────────────────────────────────────────

class TestTimeSplit:
    def test_equal_split_of_unequal_budget(self):
        """The remainder must go somewhere, not vanish."""
        out = split_daily_minutes([{"id": "a"}, {"id": "b"}, {"id": "c"}], 120)
        assert sum(out.values()) == 120
        assert set(out.values()) == {40}

    def test_single_goal_gets_everything(self):
        assert split_daily_minutes([{"id": "a"}], 90) == {"a": 90}

    def test_explicit_shares_are_respected(self):
        out = split_daily_minutes(
            [{"id": "a", "time_share": 0.75}, {"id": "b", "time_share": 0.25}], 120
        )
        assert out["a"] == 90
        assert out["b"] == 30
        assert sum(out.values()) == 120

    def test_totals_always_match_the_budget(self):
        out = split_daily_minutes(
            [{"id": "a", "time_share": 0.5}, {"id": "b", "time_share": 0.3}], 100
        )
        assert sum(out.values()) == 100

    def test_tiny_shares_are_floored(self):
        """A goal must never end up with a zero-minute budget."""
        out = split_daily_minutes(
            [{"id": "a", "time_share": 0.01}, {"id": "b", "time_share": 0.99}], 120
        )
        assert out["a"] >= 5

    def test_implicit_goals_split_what_is_left(self):
        out = split_daily_minutes(
            [{"id": "a", "time_share": 0.5}, {"id": "b"}, {"id": "c"}], 100
        )
        assert sum(out.values()) == 100
        assert out["b"] == out["c"]
        assert out["a"] == 50

    def test_empty_input(self):
        assert split_daily_minutes([], 120) == {}

    def test_no_shares_at_all_is_even(self):
        out = split_daily_minutes([{"id": "a"}, {"id": "b"}], 61)
        assert sum(out.values()) == 61
        assert abs(out["a"] - out["b"]) <= 1


class TestAllocateEndpoint:
    async def test_allocation_endpoint_returns_total(self, client):
        first = await _goal_with_topics(client, ["A"])
        second = await _goal_with_topics(client, ["B"])

        resp = await client.get(
            "/api/learning/allocation",
            params={"goal_id": [first["id"], second["id"]], "minutes_per_day": 100},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["total_allocated"] == 100
        assert len(payload["allocation"]) == 2

    async def test_allocation_requires_goal_ids(self, client):
        resp = await client.get("/api/learning/allocation")
        assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────
# FR-2.2 goal clarification
# ──────────────────────────────────────────────────────────────

class TestClarification:
    async def test_broad_topic_is_flagged(self, client):
        resp = await client.post(
            "/api/learning/goals", json={"topic": "математика", "days": 10}
        )
        goal = resp.json()

        out = await client.post(f"/api/learning/goals/{goal['id']}/clarify")
        assert out.status_code == 200
        payload = out.json()
        assert payload["clarification_needed"] is True
        assert payload["prompt"]

    async def test_clarification_flag_is_persisted(self, client):
        resp = await client.post(
            "/api/learning/goals", json={"topic": "основы", "days": 10}
        )
        goal = resp.json()
        await client.post(f"/api/learning/goals/{goal['id']}/clarify")

        after = await client.get(f"/api/learning/goals/{goal['id']}")
        assert after.json()["clarification_needed"] is True

    async def test_specific_topic_is_not_flagged(self, client):
        goal = await _goal_with_topics(client)
        out = await client.post(f"/api/learning/goals/{goal['id']}/clarify")
        assert out.json()["clarification_needed"] is False


# ──────────────────────────────────────────────────────────────
# FR-5.6 manual topic status
# ──────────────────────────────────────────────────────────────

class TestManualTopicStatus:
    async def test_marking_known_updates_state(self, client):
        goal = await _goal_with_topics(client, ["Тема"])
        topic = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()[0]

        resp = await client.patch(
            f"/api/learning/topics/{topic['id']}", json={"manual_status": "known"}
        )
        assert resp.status_code == 200
        assert resp.json()["manual_status"] == "known"
        assert resp.json()["strength"] == 1.0
        assert resp.json()["status"] == "known"

    async def test_known_topic_is_dropped_from_the_plan(self, client):
        """FR-6.2: a topic marked known must not be scheduled."""
        goal = await _goal_with_topics(client, ["Знаю", "Не знаю"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()

        await client.patch(
            f"/api/learning/topics/{topics[0]['id']}", json={"manual_status": "known"}
        )

        plan = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan",
                json={"days": 5, "minutes_per_day": 60},
            )
        ).json()
        scheduled = {i["topic_id"] for items in plan["days"].values() for i in items}
        assert topics[0]["id"] not in scheduled
        assert topics[1]["id"] in scheduled

    async def test_skip_removes_the_topic_from_planning(self, client):
        goal = await _goal_with_topics(client, ["Пропустить", "Оставить"])
        topics = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()

        await client.patch(
            f"/api/learning/topics/{topics[0]['id']}", json={"manual_status": "skip"}
        )

        plan = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan",
                json={"days": 5, "minutes_per_day": 60},
            )
        ).json()
        scheduled = {i["topic_id"] for items in plan["days"].values() for i in items}
        assert topics[0]["id"] not in scheduled

    async def test_invalid_manual_status_is_rejected(self, client):
        goal = await _goal_with_topics(client, ["Тема"])
        topic = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()[0]
        resp = await client.patch(
            f"/api/learning/topics/{topic['id']}", json={"manual_status": "maybe"}
        )
        assert resp.status_code == 422

    async def test_title_can_be_edited(self, client):
        goal = await _goal_with_topics(client, ["Старое имя"])
        topic = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()[0]
        resp = await client.patch(
            f"/api/learning/topics/{topic['id']}", json={"title": "Новое имя"}
        )
        assert resp.json()["title"] == "Новое имя"


# ──────────────────────────────────────────────────────────────
# FR-6.6 manual plan edits
# ──────────────────────────────────────────────────────────────

class TestManualPlanEdits:
    async def test_topic_can_be_appended_to_the_plan(self, client):
        goal = await _goal_with_topics(client, ["Первая", "Вторая", "Третья"])
        plan = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 30}
            )
        ).json()
        item = next(iter(plan["days"].values()))[0]

        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/plan/{item['id']}/add-topic",
            json={"topic_id": item["topic_id"], "minutes": 25},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["planned_minutes"] == 25
        # FR-10.4 shows plan reasons to the learner, so they are Russian now.
        assert resp.json()["reason"] == "добавлено вручную"

    async def test_appended_topic_appears_in_the_stored_plan(self, client):
        goal = await _goal_with_topics(client, ["Первая", "Вторая"])
        plan = (
            await client.post(
                f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 30}
            )
        ).json()
        item = next(iter(plan["days"].values()))[0]

        await client.post(
            f"/api/learning/goals/{goal['id']}/plan/{item['id']}/add-topic",
            json={"topic_id": item["topic_id"]},
        )
        stored = (await client.get(f"/api/learning/goals/{goal['id']}/plan")).json()
        assert len(stored["days"]) >= 1

    async def test_unknown_plan_item_is_404(self, client):
        goal = await _goal_with_topics(client)
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/plan/{uuid.uuid4()}/add-topic",
            # httpx cannot encode a UUID object into a JSON body, so stringify.
            json={"topic_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────
# FR-10.5 catch-up mode
# ──────────────────────────────────────────────────────────────

class TestCatchUp:
    async def test_catch_up_without_missed_days_builds_a_normal_plan(self, client):
        goal = await _goal_with_topics(client)
        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/plan/catch-up",
            json={"days": 5, "minutes_per_day": 60},
        )
        assert resp.status_code == 200
        assert resp.json()["total_minutes"] > 0

    async def test_catch_up_reports_the_gap(self, client):
        """After missed work the plan must say it compressed the backlog."""
        from datetime import date, timedelta

        goal = await _goal_with_topics(client, ["A", "B", "C", "D"])
        past = date.today() - timedelta(days=3)

        await client.post(
            f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 30}
        )

        # Move two items into the past, leaving them unfinished.
        from database import async_session
        from models.learning_goal import LearningPlanItem
        from sqlalchemy import select

        async with async_session() as db:
            rows = list(
                (await db.execute(
                    select(LearningPlanItem).where(LearningPlanItem.goal_id == uuid.UUID(goal["id"]))
                )).scalars().all()
            )
            for row in rows[:2]:
                row.day = past
                row.status = "pending"
            await db.commit()

        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/plan/catch-up",
            json={"days": 5, "minutes_per_day": 30},
        )
        assert resp.status_code == 200
        warnings = resp.json()["warnings"]
        assert any("Catch-up" in w for w in warnings)

    async def test_catch_up_shrinks_the_backlog(self, client):
        """FR-10.5: catch-up drops low-priority topics instead of piling them on."""
        from datetime import date, timedelta

        goal = await _goal_with_topics(client, [f"Тема {i}" for i in range(6)])
        past = date.today() - timedelta(days=2)

        await client.post(
            f"/api/learning/goals/{goal['id']}/plan", json={"days": 10, "minutes_per_day": 30}
        )

        from database import async_session
        from models.learning_goal import LearningPlanItem
        from sqlalchemy import select

        async with async_session() as db:
            rows = list(
                (await db.execute(
                    select(LearningPlanItem).where(LearningPlanItem.goal_id == uuid.UUID(goal["id"]))
                )).scalars().all()
            )
            for row in rows[:3]:
                row.day = past
                row.status = "pending"
            await db.commit()

        resp = await client.post(
            f"/api/learning/goals/{goal['id']}/plan/catch-up",
            json={"days": 10, "minutes_per_day": 30},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["total_minutes"] > 0
        assert any("postponed" in w for w in payload["warnings"])


# ──────────────────────────────────────────────────────────────
# FR-15.1 .ics export
# ──────────────────────────────────────────────────────────────

class TestIcsExport:
    async def test_export_returns_a_calendar(self, client):
        goal = await _goal_with_topics(client, ["Тема 1", "Тема 2"])
        await client.post(
            f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 60}
        )

        resp = await client.get(f"/api/learning/goals/{goal['id']}/export.ics")
        assert resp.status_code == 200
        assert "text/calendar" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]

        body = resp.text
        assert body.startswith("BEGIN:VCALENDAR")
        assert "VERSION:2.0" in body
        assert body.rstrip().endswith("END:VCALENDAR")
        assert "BEGIN:VEVENT" in body
        assert "DTSTART:" in body and "DTEND:" in body
        assert "SUMMARY:" in body

    async def test_export_without_a_plan_is_a_422(self, client):
        goal = await _goal_with_topics(client)
        resp = await client.get(f"/api/learning/goals/{goal['id']}/export.ics")
        assert resp.status_code == 422

    async def test_export_uses_crlf_line_endings(self, client):
        """RFC 5545 requires CRLF."""
        goal = await _goal_with_topics(client, ["Тема"])
        await client.post(
            f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 60}
        )
        resp = await client.get(f"/api/learning/goals/{goal['id']}/export.ics")
        assert "\r\n" in resp.text

    async def test_export_escapes_special_characters(self, client):
        goal = await _goal_with_topics(client, ["Тема; с запятой, и точкой"])
        await client.post(
            f"/api/learning/goals/{goal['id']}/plan", json={"days": 5, "minutes_per_day": 60}
        )
        body = (await client.get(f"/api/learning/goals/{goal['id']}/export.ics")).text
        assert "\\;" in body
        assert "\\," in body


# ──────────────────────────────────────────────────────────────
# FR-12.2 answer history filters
# ──────────────────────────────────────────────────────────────

class TestAnswerHistory:
    async def _seed_answers(self, client, goal, monkeypatch):
        import routers.adaptive_learning as router_module
        from services.adaptive_learning import content

        # Three graded answers, deliberately out of order so the "newest first"
        # assertion has something to check.
        answers = [
            ({"correctness": 0.9, "completeness": 0.9, "understanding": 0.9}, []),
            ({"correctness": 0.4, "completeness": 0.4, "understanding": 0.4},
             ["concept_confusion"]),
            ({"correctness": 0.7, "completeness": 0.7, "understanding": 0.7}, ["incomplete"]),
        ]
        queue = list(answers)

        async def _fake_grade(**kwargs):
            breakdown, errors = queue.pop(0)
            return {
                "breakdown": breakdown,
                "feedback": "разбор",
                "error_types": errors,
            }

        async def _fake_task(**kwargs):
            return {
                "text": "Вопрос", "question_type": "explain_own_words",
                "criteria": {"correctness": "a", "completeness": "b", "understanding": "c"},
                "hints": ["h1", "h2", "h3"], "difficulty": 0.5, "expected_answer": "",
            }

        monkeypatch.setattr(router_module, "_require_llm", lambda: None)
        monkeypatch.setattr(content, "generate_task", _fake_task)
        monkeypatch.setattr(content, "grade_answer", _fake_grade)

        topic = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()[0]
        question = (
            await client.post(f"/api/learning/topics/{topic['id']}/question", json={})
        ).json()
        ids = []
        for _ in range(3):
            resp = await client.post(
                f"/api/learning/questions/{question['id']}/answer", json={"text": "ответ"}
            )
            ids.append(resp.json()["id"])
        return ids

    async def test_history_lists_answers_newest_first(self, client, monkeypatch):
        goal = await _goal_with_topics(client, ["Тема"])
        await self._seed_answers(client, goal, monkeypatch)

        resp = await client.get(f"/api/learning/goals/{goal['id']}/answers")
        assert resp.status_code == 200
        assert len(resp.json()) == 3
        # Insertion order was 0.9, 0.4, 0.7, so newest first means reversed
        # insertion order — not descending score.
        scores = [round(a["final_score"], 2) for a in resp.json()]
        assert scores == [0.7, 0.4, 0.9]

    async def test_history_filters_by_min_score(self, client, monkeypatch):
        goal = await _goal_with_topics(client, ["Тема"])
        await self._seed_answers(client, goal, monkeypatch)

        resp = await client.get(
            f"/api/learning/goals/{goal['id']}/answers", params={"min_score": 0.5}
        )
        assert all(a["final_score"] >= 0.5 for a in resp.json())

    async def test_history_filters_by_error_type(self, client, monkeypatch):
        goal = await _goal_with_topics(client, ["Тема"])
        await self._seed_answers(client, goal, monkeypatch)

        resp = await client.get(
            f"/api/learning/goals/{goal['id']}/answers",
            params={"error_type": "concept_confusion"},
        )
        assert len(resp.json()) == 1
        assert resp.json()[0]["error_types"] == ["concept_confusion"]

    async def test_history_filters_by_topic(self, client, monkeypatch):
        goal = await _goal_with_topics(client, ["Тема"])
        await self._seed_answers(client, goal, monkeypatch)
        topic = (await client.get(f"/api/learning/goals/{goal['id']}/topics")).json()[0]

        resp = await client.get(
            f"/api/learning/goals/{goal['id']}/answers", params={"topic_id": topic["id"]}
        )
        assert len(resp.json()) == 3

    async def test_history_respects_limit(self, client, monkeypatch):
        goal = await _goal_with_topics(client, ["Тема"])
        await self._seed_answers(client, goal, monkeypatch)

        resp = await client.get(
            f"/api/learning/goals/{goal['id']}/answers", params={"limit": 2}
        )
        assert len(resp.json()) == 2

    async def test_disputed_filter_finds_the_challenge(self, client, monkeypatch):
        goal = await _goal_with_topics(client, ["Тема"])
        ids = await self._seed_answers(client, goal, monkeypatch)

        await client.post(
            f"/api/learning/answers/{ids[0]}/dispute", json={"note": "не согласен"}
        )

        resp = await client.get(
            f"/api/learning/goals/{goal['id']}/answers", params={"disputed": True}
        )
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == ids[0]

    async def test_history_for_unknown_goal_is_404(self, client):
        resp = await client.get(f"/api/learning/goals/{uuid.uuid4()}/answers")
        assert resp.status_code == 404