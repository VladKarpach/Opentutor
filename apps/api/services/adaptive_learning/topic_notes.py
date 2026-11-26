"""Topic notes: the explanation the learner already read, condensed.

The note holds the material from the topic's own explanation and nothing else. That
is a hard rule, not a preference: a note that also carried a study plan, a mindmap
of invented subtopics or a title turned into an outline was mixing two sources, and
the learner could not tell which part they were being taught.

So the note is a faithful condensation of whatever exists. A detailed explanation
gives a detailed note; a short one gives a short note. A topic with no explanation
has no material, and therefore no note — the panel says so instead of filling the
space with something else.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.generated_asset import GeneratedAsset
from models.learning_goal import LearningGoal, LearningTopic
from services.adaptive_learning import topic_flow

logger = logging.getLogger(__name__)

# Same asset type the course note block uses, so saving and listing stay
# consistent with the base project. The topic lives in metadata.
ASSET_TYPE = "notes"

# A summary far shorter than its source means the model dropped most of it.
_MIN_NOTE_CHARS = 80


class NoteUnavailable(Exception):
    """The topic has no explanation, so there is no material for a note."""


def _note_metadata(topic_id: uuid.UUID, note_format: str, source: str) -> dict:
    return {
        "topic_id": str(topic_id),
        "format": note_format,
        # Marks these as topic notes rather than the course's chapter notes, so
        # the two never get mixed up in a list.
        "source": "learning_topic",
        # What the note was written from: explanation, description or title. The
        # UI uses it to say a note is thin instead of implying it is complete.
        "written_from": source,
        "auto_generated": True,
    }


async def saved_topic_note(
    db: AsyncSession, topic_id: uuid.UUID, user_id: uuid.UUID
) -> dict | None:
    """The newest saved note for a topic, if there is one."""
    result = await db.execute(
        select(GeneratedAsset)
        .where(
            GeneratedAsset.user_id == user_id,
            GeneratedAsset.asset_type == ASSET_TYPE,
            GeneratedAsset.is_archived == False,
        )
        .order_by(GeneratedAsset.version.desc())
    )
    for asset in result.scalars().all():
        meta = asset.metadata_ or {}
        if meta.get("topic_id") == str(topic_id):
            content = asset.content or {}
            markdown = content.get("markdown", "") if isinstance(content, dict) else ""
            if not markdown:
                continue
            return {
                "id": str(asset.id),
                "title": asset.title,
                "markdown": markdown,
                "format": meta.get("format") or "qa",
                "written_from": meta.get("written_from") or "explanation",
                "version": asset.version,
            }
    return None


async def build_topic_note(
    db: AsyncSession,
    topic: LearningTopic,
    user_id: uuid.UUID,
    *,
    note_format: str = "qa",
    course_id: uuid.UUID | None = None,
) -> tuple[str, bool, str]:
    """Condense the topic's explanation into a note.

    Returns ``(markdown, from_cache, source)``. Raises :class:`NoteUnavailable`
    when the topic has no explanation: there is no material, and substituting the
    description or the title would put something in the note that the learner never
    read.
    """
    cached = await saved_topic_note(db, topic.id, user_id)
    if cached and cached.get("format") == note_format:
        return (
            cached["markdown"],
            True,
            cached.get("written_from") or "explanation",
        )
    if cached:
        # A saved note in a different format is not an answer to this request.
        # Returning it anyway made the format setting silently ignored — asking
        # for question-and-answer kept serving the old bullet summary.
        logger.info(
            "Topic %s: rewriting note from %r to %r",
            topic.title, cached.get("format"), note_format,
        )

    material, source = await topic_flow.topic_material(db, topic, user_id)
    if source != "explanation":
        raise NoteUnavailable(
            f"Topic {topic.id} has no explanation to condense"
        )

    from services.parser.notes import restructure_notes

    markdown = await restructure_notes(
        content=material,
        title=topic.title,
        note_format=note_format,
    )
    markdown = (markdown or "").strip()
    if len(markdown) < _MIN_NOTE_CHARS:
        logger.warning(
            "Topic %s produced only a %d-char note", topic.id, len(markdown),
        )

    goal = await db.get(LearningGoal, topic.goal_id)
    db.add(
        GeneratedAsset(
            user_id=user_id,
            course_id=course_id or getattr(goal, "course_id", None),
            asset_type=ASSET_TYPE,
            title=f"Конспект: {topic.title}",
            content={"markdown": markdown},
            metadata_=_note_metadata(topic.id, note_format, source),
        )
    )
    await db.commit()
    logger.info("Wrote a topic note for %r (%d chars)", topic.title, len(markdown))
    return markdown, False, source


async def topic_notes_index(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    course_id: uuid.UUID | None = None,
    goal_id: uuid.UUID | None = None,
) -> list[dict]:
    """Topics that have an explanation, and so can have a note.

    Only these are listed. A topic with no explanation has no material for a note,
    and listing it would offer a row that cannot be opened — the list would be
    mostly dead entries.
    """
    goal_query = select(LearningGoal).where(LearningGoal.user_id == user_id)
    if goal_id is not None:
        goal_query = goal_query.where(LearningGoal.id == goal_id)
    elif course_id is not None:
        goal_query = goal_query.where(LearningGoal.course_id == course_id)
    else:
        # Every goal, so the home page can show the whole picture.
        goal_query = goal_query.where(LearningGoal.course_id.is_not(None))

    goals = list((await db.execute(goal_query.order_by(LearningGoal.created_at.desc()))).scalars().all())
    if not goals:
        return []

    goal_ids = [g.id for g in goals]
    goal_by_id = {g.id: g for g in goals}

    topic_rows = (
        await db.execute(
            select(LearningTopic)
            .where(LearningTopic.goal_id.in_(goal_ids))
            .order_by(LearningTopic.order_index, LearningTopic.created_at)
        )
    ).scalars().all()

    entries: list[dict] = []
    for topic in topic_rows:
        explanation = await topic_flow.saved_explanation(db, topic.id, user_id)
        if not explanation.strip():
            continue
        note = await saved_topic_note(db, topic.id, user_id)
        goal = goal_by_id[topic.goal_id]
        entries.append(
            {
                "topic_id": str(topic.id),
                "topic_title": topic.title,
                "goal_id": str(goal.id),
                "goal_topic": goal.topic,
                "course_id": str(goal.course_id) if goal.course_id else None,
                "order_index": topic.order_index,
                "explanation_chars": len(explanation),
                # Always "explanation" now: a topic without one is not listed.
                "note_source": "explanation",
                "has_note": note is not None,
                "note_markdown": note["markdown"] if note else None,
            }
        )
    return entries