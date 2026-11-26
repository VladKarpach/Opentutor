"""Which topic the learner is on, and what cards and tests may draw from.

Cards, tests and checks follow the *goal*, not the course's chapter list. The
chapter list is the base project's own structure, and generating from it meant a
learner working through a goal got questions about material they had not reached.

Two rules make this work, and they come straight from how the goal is meant to be
worked (FR-5, FR-11):

* While the goal exam has not been passed, only the *current* topic produces cards.
  Each subtopic carries its own assignments; the exam is the single gate at the end
  of the goal, so before it there is nothing to say a later topic is covered.
* Once the exam is passed, the goal is done, and every topic opens. "Everything
  else" in the cards is the material the learner has just proved they know.

Grounding matters as much as scope. A topic carries a title and a one-line
description, which is too thin to write factual questions from: the model would
invent them. The explanation the learner already read is the real material, so that
is preferred, with the description as the fallback when a topic has none yet.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.generated_asset import GeneratedAsset
from models.learning_goal import Exam, LearningGoal, LearningTopic, TopicState

logger = logging.getLogger(__name__)

# FR-11 / drills.EXAM_PASS_SCORE: the same bar the goal auto-complete uses, so a
# goal cannot count itself complete below the mark that opens its cards.
EXAM_PASS_SCORE = 0.7

# A topic the learner explicitly skipped is not a card source either.
STATUS_KNOWN = "known"


async def list_goal_topics(
    db: AsyncSession, goal_id: uuid.UUID
) -> list[LearningTopic]:
    """The goal's topics in plan order."""
    result = await db.execute(
        select(LearningTopic)
        .where(LearningTopic.goal_id == goal_id)
        .order_by(LearningTopic.order_index, LearningTopic.created_at)
    )
    return list(result.scalars().all())


async def active_goals_for_course(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> list[LearningGoal]:
    """The course's goals a learner is still working through.

    Cards need a goal; a course can have several, and picking the wrong one would
    quiz the learner on a subject they are not on. Completed and archived goals are
    left out because the learner moved on.

    Goals that actually have topics come first. A goal still waiting for its topics
    to be generated cannot produce cards, and it is often the oldest row in the
    course — picking it by date alone meant generating nothing while a real goal
    sat right behind it. Such goals are still returned, last, so the UI can say the
    topics have not been made yet instead of claiming there is no goal.
    """
    result = await db.execute(
        select(LearningGoal)
        .where(
            LearningGoal.user_id == user_id,
            LearningGoal.course_id == course_id,
            LearningGoal.status.in_(("active", "paused")),
        )
        .order_by(LearningGoal.created_at)
    )
    goals = list(result.scalars().all())
    if len(goals) < 2:
        return goals

    topic_rows = await db.execute(
        select(LearningTopic.goal_id).where(
            LearningTopic.goal_id.in_([g.id for g in goals])
        )
    )
    with_topics = {row[0] for row in topic_rows.all()}

    return sorted(
        goals,
        key=lambda g: (0 if g.id in with_topics else 1,),
    )


async def topic_counts_for_goals(
    db: AsyncSession, goal_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """How many topics each goal has, in one query.

    The goal picker shows a count per goal. Asking the DB once per goal made the
    picker's cost grow with the number of goals on a single screen.
    """
    if not goal_ids:
        return {}
    result = await db.execute(
        select(LearningTopic.goal_id, func.count())
        .where(LearningTopic.goal_id.in_(goal_ids))
        .group_by(LearningTopic.goal_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def goals_for_course(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> list[LearningGoal]:
    """Every goal in the course, in the order a picker should show them.

    The picker this serves lists goals so the learner can say which subject the
    cards or the quiz are about. That makes silent guessing wrong: picking by date
    and hoping it matches what the learner wants is how material gets built from a
    goal they are not working on.

    Completed and archived goals are included — a learner revising an earlier goal
    still needs to reach it. Goals whose topics exist come first for the same
    reason :func:`active_goals_for_course` puts them first.
    """
    result = await db.execute(
        select(LearningGoal)
        .where(
            LearningGoal.user_id == user_id,
            LearningGoal.course_id == course_id,
        )
        .order_by(LearningGoal.created_at)
    )
    goals = list(result.scalars().all())
    if len(goals) < 2:
        return goals

    counts = await topic_counts_for_goals(db, [g.id for g in goals])

    return sorted(goals, key=lambda g: (0 if counts.get(g.id) else 1,))


async def goal_exam_passed(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """True once a completed exam cleared the pass mark.

    Any completed attempt counts. A learner who failed twice and then passed has
    passed; requiring the *latest* attempt would revoke that.
    """
    result = await db.execute(
        select(Exam.total_score).where(
            Exam.goal_id == goal_id,
            Exam.user_id == user_id,
            Exam.status == "completed",
            Exam.total_score.is_not(None),
        )
    )
    scores = [float(row[0]) for row in result.all()]
    return any(score >= EXAM_PASS_SCORE for score in scores)


async def _covered_topic_ids(
    db: AsyncSession,
    goal_id: uuid.UUID,
    user_id: uuid.UUID,
    topics: list[LearningTopic],
) -> set[uuid.UUID]:
    known = {t.id for t in topics}
    result = await db.execute(
        select(TopicState.topic_id, TopicState.status).where(
            TopicState.goal_id == goal_id,
            TopicState.user_id == user_id,
            TopicState.topic_id.is_not(None),
        )
    )
    return {
        topic_id
        for topic_id, status in result.all()
        if status == STATUS_KNOWN and topic_id in known
    }


async def resolve_current_topic(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[LearningTopic | None, list[LearningTopic], bool]:
    """The topic the learner is on, the goal's topics, and whether the exam is done.

    The current topic is the first in plan order that is not yet covered. Topics
    the learner marked known, or that the exam already covered, are skipped.
    """
    topics = await list_goal_topics(db, goal_id)
    if not topics:
        return None, [], False

    covered = await _covered_topic_ids(db, goal_id, user_id, topics)
    passed = await goal_exam_passed(db, goal_id, user_id)

    if passed:
        # The goal is complete, so there is no "current" topic left to work
        # through. Reporting the last one would keep suggesting unfinished work.
        logger.info("Goal %s: exam passed, all %s topics open", goal_id, len(topics))
        return None, topics, True

    for topic in topics:
        if topic.id not in covered and topic.manual_status != "skip":
            return topic, topics, False

    # Everything is covered but the exam was never taken or never passed: keep
    # pointing at the last topic rather than reporting nothing to do.
    last = topics[-1]
    logger.info("Goal %s: every topic covered, holding at %r", goal_id, last.title)
    return last, topics, False


def pick_current_topic(
    topics: list[LearningTopic], covered: set[uuid.UUID]
) -> LearningTopic | None:
    """First topic in plan order that is neither covered nor skipped.

    Shared with the batched progress loader so the card scope and the course page
    cannot disagree about which topic is current. Returns the last topic when
    everything is covered, and None only for an empty goal.
    """
    if not topics:
        return None
    for topic in topics:
        if topic.id not in covered and topic.manual_status != "skip":
            return topic
    return topics[-1]


@dataclass
class GoalProgress:
    """How far a goal has got, for display next to it."""

    topics_total: int = 0
    topics_done: int = 0
    exam_status: str = "none"
    exam_score: float | None = None
    exam_passed: bool = False
    current_topic_title: str | None = None

    @property
    def progress_percent(self) -> int:
        # A passed exam means the goal is finished whatever the per-topic numbers
        # say, so it reports 100 rather than the last topic count.
        if self.exam_passed:
            return 100
        if self.topics_total <= 0:
            return 0
        return round(100 * self.topics_done / self.topics_total)


async def course_goal_progress(
    db: AsyncSession, user_id: uuid.UUID, goal_ids: list[uuid.UUID]
) -> dict[uuid.UUID, GoalProgress]:
    """Progress for many goals in three queries.

    The course page lists every goal, and asking for each one's topics, states and
    exam separately meant three queries per row. Everything is fetched for the whole
    batch instead and combined here.
    """
    progress = {gid: GoalProgress() for gid in goal_ids}
    if not goal_ids:
        return progress

    topic_rows = (
        await db.execute(
            select(
                LearningTopic.goal_id,
                LearningTopic.id,
                LearningTopic.title,
                LearningTopic.order_index,
                LearningTopic.manual_status,
            ).where(LearningTopic.goal_id.in_(goal_ids))
        )
    ).all()

    topics_by_goal: dict[uuid.UUID, list[LearningTopic]] = {}
    for goal_id, topic_id, title, order_index, manual_status in topic_rows:
        topics_by_goal.setdefault(goal_id, []).append(
            LearningTopic(
                id=topic_id,
                goal_id=goal_id,
                title=title,
                order_index=order_index,
                manual_status=manual_status,
            )
        )

    state_rows = (
        await db.execute(
            select(TopicState.goal_id, TopicState.topic_id, TopicState.status).where(
                TopicState.goal_id.in_(goal_ids),
                TopicState.user_id == user_id,
            )
        )
    ).all()

    covered: dict[uuid.UUID, set[uuid.UUID]] = {}
    for goal_id, topic_id, status in state_rows:
        if status == STATUS_KNOWN:
            covered.setdefault(goal_id, set()).add(topic_id)

    exam_rows = (
        await db.execute(
            select(Exam.goal_id, Exam.status, Exam.total_score).where(
                Exam.goal_id.in_(goal_ids),
                Exam.user_id == user_id,
            )
        )
    ).all()

    exams: dict[uuid.UUID, list[tuple[str, float | None]]] = {}
    for goal_id, status, total_score in exam_rows:
        exams.setdefault(goal_id, []).append((status, total_score))

    for goal_id in goal_ids:
        entry = progress[goal_id]
        ordered = sorted(
            topics_by_goal.get(goal_id, []),
            key=lambda t: (t.order_index, t.title or ""),
        )
        done = covered.get(goal_id, set())
        entry.topics_total = len(ordered)
        entry.topics_done = sum(1 for t in ordered if t.id in done)

        attempts = exams.get(goal_id, [])
        completed = [s for st, s in attempts if st == "completed" and s is not None]
        if completed:
            entry.exam_status = "completed"
            # Any passing attempt counts, matching goal_exam_passed: failing later
            # does not take a pass away.
            entry.exam_score = max(float(s) for s in completed)
            entry.exam_passed = entry.exam_score >= EXAM_PASS_SCORE
        elif any(st == "in_progress" for st, _ in attempts):
            entry.exam_status = "in_progress"

        if entry.exam_passed:
            # The goal is done, so there is no current topic to point at.
            entry.current_topic_title = None
        else:
            current = pick_current_topic(ordered, done)
            entry.current_topic_title = current.title if current else None

    return progress


async def topics_in_scope(
    db: AsyncSession, goal_id: uuid.UUID, user_id: uuid.UUID
) -> list[LearningTopic]:
    """Topics that may produce cards or tests right now.

    Everything once the exam is passed, otherwise only the current topic. Topics
    after it are excluded on purpose: the learner has not studied them.
    """
    current, topics, passed = await resolve_current_topic(db, goal_id, user_id)
    if passed:
        return topics
    if current is None:
        return []
    return [current]


async def saved_explanation(
    db: AsyncSession, topic_id: uuid.UUID, user_id: uuid.UUID
) -> str:
    """The learner's own explanation for this topic, if they have read one.

    Written by FR-7.1 and keyed by ``metadata_["topic_id"]``. The newest wins so
    that "explain it differently" (FR-7.2) is what cards follow.
    """
    result = await db.execute(
        select(GeneratedAsset.content)
        .where(
            GeneratedAsset.user_id == user_id,
            GeneratedAsset.asset_type == "explanation",
            GeneratedAsset.metadata_.contains(str(topic_id)),
        )
        .order_by(GeneratedAsset.created_at.desc())
        .limit(1)
    )
    stored = result.scalar_one_or_none()
    if isinstance(stored, dict):
        return str(stored.get("text") or "")
    if isinstance(stored, str):
        return stored
    return ""


async def topic_material(
    db: AsyncSession, topic: LearningTopic, user_id: uuid.UUID
) -> tuple[str, str]:
    """Text a question may be built from, and where it came from.

    Returns ``(text, source)`` with source one of ``explanation``, ``description``
    or ``none``. The caller must check the source: a topic with neither has nothing
    to ask about, and prompting on its bare title produces questions about the
    title itself — the exact failure this module exists to stop.

    The topic title is passed to the prompt as a labelled field, never as a
    markdown heading. A literal ``## Интегралы`` in the body reads to the model as
    part of a document, and it answers by asking about heading levels and how many
    sections the note has.
    """
    text = await saved_explanation(db, topic.id, user_id)
    if text.strip():
        return text.strip(), "explanation"

    fallback = (topic.description or "").strip()
    if fallback:
        return (
            f"{fallback}\n\n"
            "(This topic has no detailed material yet: only its one-line "
            "description is available. Stay close to it and do not add specifics "
            "it does not state.)",
            "description",
        )

    return "", "none"


# A description is thin but usable. A bare title is not: the model would invent
# the content, which is the complaint the whole topic flow answers.
MIN_USABLE_SOURCE = "description"