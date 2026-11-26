"""A completed diagnostic must reach the planner.

The planner reads TopicState and nothing else, so the estimates used to stop at
the session row: the dialogue completed, the learner saw the bands, and the plan
was still built as if every topic were untouched. These tests pin that the
estimates are written, and the two guards around them.
"""

import os
import tempfile
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from database import Base
from models.learning_goal import LearningGoal, LearningTopic, TopicState
from models.user import User

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    """Its own temporary SQLite, following the pattern the other tests use."""
    fd, db_path = tempfile.mkstemp(prefix="opentutor-diag-", suffix=".db")
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

    async with factory() as session:
        yield session

    await test_engine.dispose()
    try:
        os.remove(db_path)
    except OSError:
        pass


async def _seed(session):
    user = User(name="Diag")
    session.add(user)
    await session.flush()

    goal = LearningGoal(
        id=uuid.uuid4(), user_id=user.id, topic="Матрицы", status="active"
    )
    session.add(goal)
    await session.flush()

    topics = []
    for i in range(3):
        t = LearningTopic(
            id=uuid.uuid4(),
            goal_id=goal.id,
            title=f"Тема {i}",
            description="d",
            order_index=i,
        )
        session.add(t)
        topics.append(t)
    await session.flush()
    return user, goal, topics


async def _states(session, goal_id, user_id):
    rows = await session.execute(
        select(TopicState).where(
            TopicState.goal_id == goal_id, TopicState.user_id == user_id
        )
    )
    return {s.topic_id: s for s in rows.scalars().all()}


async def test_estimates_reach_the_topic_states(db):
    """The whole point: the planner must see what the dialogue inferred."""
    from routers.adaptive_learning import _apply_estimates_to_topics

    user, goal, topics = await _seed(db)
    written = await _apply_estimates_to_topics(
        db,
        goal.id,
        user.id,
        [
            {"id": str(topics[0].id), "estimate": 0.9},
            {"id": str(topics[1].id), "estimate": 0.6},
            {"id": str(topics[2].id), "estimate": 0.2},
        ],
    )
    await db.commit()

    assert written == 3
    states = await _states(db, goal.id, user.id)
    assert states[topics[0].id].strength == pytest.approx(0.9)
    assert states[topics[0].id].status == "known"
    assert states[topics[1].id].status == "learning"
    assert states[topics[2].id].strength == pytest.approx(0.2)
    assert states[topics[2].id].status == "weak"


async def test_a_practised_topic_keeps_its_answers(db):
    """Two probes are a guess; a graded answer is evidence. The guess does not
    overwrite it."""
    from routers.adaptive_learning import _apply_estimates_to_topics

    user, goal, topics = await _seed(db)

    db.add(
        TopicState(
            goal_id=goal.id,
            user_id=user.id,
            topic_id=topics[0].id,
            strength=0.85,
            status="known",
            last_review_at=datetime.now(timezone.utc),
            repetitions=4,
        )
    )
    await db.commit()

    written = await _apply_estimates_to_topics(
        db,
        goal.id,
        user.id,
        [
            {"id": str(topics[0].id), "estimate": 0.1},
            {"id": str(topics[1].id), "estimate": 0.7},
        ],
    )
    await db.commit()

    assert written == 1
    states = await _states(db, goal.id, user.id)
    assert states[topics[0].id].strength == pytest.approx(0.85)
    assert states[topics[1].id].strength == pytest.approx(0.7)


async def test_a_manual_correction_overrides_practice(db):
    """FR-4.5: the learner states the level, so it wins over the guess — and
    over their own earlier answers."""
    from routers.adaptive_learning import _apply_estimates_to_topics

    user, goal, topics = await _seed(db)
    db.add(
        TopicState(
            goal_id=goal.id,
            user_id=user.id,
            topic_id=topics[0].id,
            strength=0.9,
            status="known",
            last_review_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    written = await _apply_estimates_to_topics(
        db,
        goal.id,
        user.id,
        [{"id": str(topics[0].id), "estimate": 0.15}],
        force=True,
    )
    await db.commit()

    assert written == 1
    states = await _states(db, goal.id, user.id)
    assert states[topics[0].id].strength == pytest.approx(0.15)
    assert states[topics[0].id].status == "weak"


async def test_an_estimate_cannot_name_another_goals_topic(db):
    """A stale session must not reach across goals through this one."""
    from routers.adaptive_learning import _apply_estimates_to_topics

    user, goal, _topics = await _seed(db)
    other = LearningGoal(
        id=uuid.uuid4(), user_id=user.id, topic="Другая", status="active"
    )
    db.add(other)
    await db.flush()
    foreign = LearningTopic(
        id=uuid.uuid4(), goal_id=other.id, title="Чужая", description="d"
    )
    db.add(foreign)
    await db.flush()

    written = await _apply_estimates_to_topics(
        db, goal.id, user.id, [{"id": str(foreign.id), "estimate": 0.99}]
    )
    await db.commit()

    assert written == 0
    assert await _states(db, other.id, user.id) == {}


async def test_malformed_estimates_are_dropped_not_fatal(db):
    """A bad id or a missing number costs that entry, not the whole session."""
    from routers.adaptive_learning import _apply_estimates_to_topics

    user, goal, topics = await _seed(db)
    written = await _apply_estimates_to_topics(
        db,
        goal.id,
        user.id,
        [
            {"id": "not-a-uuid", "estimate": 0.5},
            {"id": str(topics[0].id), "estimate": "high"},
            {"id": str(topics[1].id), "estimate": 0.42},
        ],
    )
    await db.commit()

    assert written == 1
    states = await _states(db, goal.id, user.id)
    assert topics[1].id in states


async def test_estimates_are_clamped_to_the_scale(db):
    """The slider and the model can both overshoot; storage must not."""
    from routers.adaptive_learning import _apply_estimates_to_topics

    user, goal, topics = await _seed(db)
    await _apply_estimates_to_topics(
        db,
        goal.id,
        user.id,
        [
            {"id": str(topics[0].id), "estimate": 4.2},
            {"id": str(topics[1].id), "estimate": -3.0},
        ],
    )
    await db.commit()

    states = await _states(db, goal.id, user.id)
    assert states[topics[0].id].strength == pytest.approx(1.0)
    assert states[topics[1].id].strength == pytest.approx(0.0)


async def test_topics_report_the_explanations_they_actually_have(db):
    """`has_explanation` decides which topics can produce a note or a quiz.

    It was permanently False because the membership test compared a string
    against a set of UUIDs, which never matches. The topic board would then ask
    for explanations the learner already has.
    """
    from models.generated_asset import GeneratedAsset
    from routers.adaptive_learning import _topics_with_explanations
    from schemas.learning import topic_to_response

    user, goal, topics = await _seed(db)
    for topic in topics[:2]:
        db.add(
            GeneratedAsset(
                user_id=user.id,
                asset_type="explanation",
                title=f"Explanation: {topic.title}",
                content={"text": "Разбор темы."},
                metadata_={"topic_id": str(topic.id), "style": "short"},
            )
        )
    await db.commit()

    explained = await _topics_with_explanations(db, user.id, topics)
    flagged = {
        topic.id: topic_to_response(
            topic, None, has_explanation=topic.id in explained
        ).has_explanation
        for topic in topics
    }

    assert flagged[topics[0].id] is True
    assert flagged[topics[1].id] is True
    assert flagged[topics[2].id] is False