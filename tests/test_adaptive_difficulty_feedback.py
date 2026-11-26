"""The feedback loop that makes topic difficulty adaptive.

The initial estimate (easy / medium / hard) sets the baseline time. These
tests cover the second half: graded answers and reviewed cards move the stored
relative difficulty, and the planner charges extra time only for the gap
between expectation and reality.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base
from models.content import CourseContentTree
from models.course import Course
from models.learning_goal import LearningGoal, LearningTopic
from models.progress import LearningProgress
from models.user import User
from services.adaptive_learning.persistence import (
    apply_scored_answer,
    load_topic_inputs,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _goal_with_topic(
    db, difficulty: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    user = User(name="Learner")
    db.add(user)
    await db.flush()
    goal = LearningGoal(user_id=user.id, topic="Пределы")
    db.add(goal)
    await db.flush()
    topic = LearningTopic(
        goal_id=goal.id,
        title="Определение предела",
        estimated_minutes=30,
        difficulty=difficulty,
    )
    db.add(topic)
    await db.flush()
    return user.id, goal.id, topic.id


async def _stored_difficulty(db, goal_id: uuid.UUID, topic_id: uuid.UUID):
    from models.learning_goal import TopicState

    row = await db.execute(
        select(TopicState).where(
            TopicState.goal_id == goal_id, TopicState.topic_id == topic_id
        )
    )
    state = row.scalar_one_or_none()
    return state.fsrs_difficulty if state else None


class TestAnswerFeedback:
    async def test_a_bad_answer_on_an_easy_topic_raises_difficulty(self, db):
        user_id, goal_id, topic_id = await _goal_with_topic(db, "easy")

        await apply_scored_answer(
            db,
            goal_id=goal_id,
            user_id=user_id,
            topic_id=topic_id,
            final_score=0.2,
        )

        stored = await _stored_difficulty(db, goal_id, topic_id)
        # Easy prior 0.2, observed 0.8 → target 1.0; EMA from neutral with
        # alpha=0.3 lands at 0.65.
        expected = 0.5 + settings.adaptive_difficulty_alpha * (1.0 - 0.5)
        assert stored == pytest.approx(expected)

    async def test_repeated_answers_converge(self, db):
        user_id, goal_id, topic_id = await _goal_with_topic(db, "medium")

        for _ in range(20):
            await apply_scored_answer(
                db,
                goal_id=goal_id,
                user_id=user_id,
                topic_id=topic_id,
                final_score=0.0,
            )

        stored = await _stored_difficulty(db, goal_id, topic_id)
        assert stored > 0.95  # consistently harder than expected

    async def test_good_answers_pull_difficulty_down(self, db):
        user_id, goal_id, topic_id = await _goal_with_topic(db, "hard")

        for _ in range(20):
            await apply_scored_answer(
                db,
                goal_id=goal_id,
                user_id=user_id,
                topic_id=topic_id,
                final_score=1.0,
            )

        # Hard prior 0.8, observed 0.0 → target 0.0.
        stored = await _stored_difficulty(db, goal_id, topic_id)
        assert stored < 0.05

    async def test_a_guess_is_not_counted(self, db):
        user_id, goal_id, topic_id = await _goal_with_topic(db, "medium")

        await apply_scored_answer(
            db,
            goal_id=goal_id,
            user_id=user_id,
            topic_id=topic_id,
            final_score=0.0,
            confidence="guessing",
        )

        assert await _stored_difficulty(db, goal_id, topic_id) is None

    async def test_as_expected_performance_stays_neutral(self, db):
        user_id, goal_id, topic_id = await _goal_with_topic(db, "hard")

        for _ in range(20):
            await apply_scored_answer(
                db,
                goal_id=goal_id,
                user_id=user_id,
                topic_id=topic_id,
                final_score=1.0 - settings.difficulty_prior_hard,
            )

        stored = await _stored_difficulty(db, goal_id, topic_id)
        assert stored == pytest.approx(0.5, abs=0.01)


class TestCardFeedback:
    async def test_reviewed_cards_feed_the_topic_difficulty(self, db):
        user = User(name="Learner")
        db.add(user)
        await db.flush()
        course = Course(user_id=user.id, name="Матан")
        db.add(course)
        await db.flush()
        goal = LearningGoal(user_id=user.id, topic="Пределы", course_id=course.id)
        db.add(goal)
        await db.flush()

        root = CourseContentTree(course_id=course.id, title="Глава", level=1)
        db.add(root)
        await db.flush()
        child = CourseContentTree(
            course_id=course.id, parent_id=root.id, title="Раздел", level=2
        )
        db.add(child)
        await db.flush()

        topic = LearningTopic(
            goal_id=goal.id,
            title="Определение предела",
            estimated_minutes=30,
            difficulty="medium",
            source_node_id=root.id,
        )
        db.add(topic)
        db.add(
            LearningProgress(
                user_id=user.id,
                course_id=course.id,
                content_node_id=child.id,
                fsrs_difficulty=10.0,
                fsrs_reps=3,
            )
        )
        await db.flush()

        inputs = await load_topic_inputs(db, goal.id, user.id)

        # Raw 10/10 normalises to 1.0; medium prior 0.5 → relative 1.0.
        assert inputs[0].fsrs_difficulty == pytest.approx(1.0)

    async def test_unreviewed_cards_are_ignored(self, db):
        user = User(name="Learner")
        db.add(user)
        await db.flush()
        course = Course(user_id=user.id, name="Матан")
        db.add(course)
        await db.flush()
        goal = LearningGoal(user_id=user.id, topic="Пределы", course_id=course.id)
        db.add(goal)
        await db.flush()

        node = CourseContentTree(course_id=course.id, title="Глава", level=1)
        db.add(node)
        await db.flush()

        topic = LearningTopic(
            goal_id=goal.id,
            title="Определение предела",
            estimated_minutes=30,
            difficulty="medium",
            source_node_id=node.id,
        )
        db.add(topic)
        db.add(
            LearningProgress(
                user_id=user.id,
                course_id=course.id,
                content_node_id=node.id,
                fsrs_difficulty=10.0,
                fsrs_reps=0,  # never reviewed — the 5.0 default is not a signal
            )
        )
        await db.flush()

        inputs = await load_topic_inputs(db, goal.id, user.id)

        assert inputs[0].fsrs_difficulty is None
