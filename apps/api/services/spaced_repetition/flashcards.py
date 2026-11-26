"""Flashcard generation service.

Cards follow the learner's goal, not the course's chapter list: see
``services.adaptive_learning.topic_flow`` for which topic may produce cards and
why. The material is the topic's saved explanation, because a topic's title and
one-line description are too thin to write factual questions from.
"""

import uuid
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.learning_goal import LearningTopic
from services.llm.router import get_llm_client
from services.spaced_repetition.fsrs import FSRSCard, review_card

logger = logging.getLogger(__name__)

FLASHCARD_PROMPT = """Generate flashcards about one topic of a study programme.

TOPIC NAME: {topic}
MATERIAL:
{content}

Create {count} flashcards. Each flashcard should have:
- A clear, specific question (front)
- A concise, accurate answer (back)
- A difficulty tag: easy, medium, or hard

Output as JSON array:
[
  {{"front": "question", "back": "answer", "difficulty": "medium"}},
  ...
]

Rules:
- Write each question as a teacher would ask it about the subject, never about a
  document. Do not mention the material, the note, the text or the passage in a
  question, and never say "according to" it — ask about the subject directly.
- Every question must be answerable from the material above and nothing else.
  The learner has not studied the topics after this one, so a question that
  needs them is unanswerable and teaches the wrong lesson.
- Do not write questions about the subject area in general, only about what the
  material actually states.
- Focus on key concepts and definitions
- Avoid trivial questions
- Make questions specific enough to have one clear answer
- Include a mix of recall, understanding, and application questions
- Write in the same language as the material"""

# Used when the topic has no explanation, only a description or just a name.
# Without this the "answerable from the content above and nothing else" rule makes
# a bare title produce nothing, or questions about the title itself.
_THIN_PREAMBLE = """\
Everything written below is all that exists for this topic: it may be only its
description, or only its name. In that case write cards that set out what this
topic must cover and what has to be learned from it. Do not invent formulas,
numbers or examples that are not written below.

"""

_MODE_FLASHCARD_HINTS: dict[str, str] = {
    "self_paced": "\nMode: SELF-PACED — Include exploratory, open-ended questions. Encourage cross-topic connections.",
    "course_following": "\nMode: COURSE FOLLOWING — Follow the syllabus order. Only test concepts from the provided content.",
}


async def generate_flashcards(
    db: AsyncSession,
    course_id: uuid.UUID,
    goal_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
    count: int = 5,
    mode: str | None = None,
    user_id: uuid.UUID | None = None,
) -> list[dict]:
    """Generate flashcards for the topic the learner is on.

    The material comes from the topic's saved explanation, not from the course's
    chapter tree. That tree is the base project's own structure: a learner working
    through a goal got questions about chapters they had never opened, which is the
    "questions about the course name" problem.

    Questions for topics already covered are not regenerated here — those cards
    exist as saved batches and return through FSRS on their own schedule.
    """
    from services.adaptive_learning import topic_flow

    if user_id is None:
        logger.warning("Flashcard generation without a user; nothing to scope to")
        return []

    topic: LearningTopic | None = None
    topic_label = ""

    if topic_id:
        result = await db.execute(
            select(LearningTopic).where(LearningTopic.id == topic_id)
        )
        topic = result.scalar_one_or_none()
        if topic is None:
            logger.warning("Flashcard topic %s not found", topic_id)
            return []
        topic_label = topic.title
    else:
        if goal_id is None:
            logger.info(
                "No goal or topic given for course %s; cards need a goal", course_id,
            )
            return []
        in_scope = await topic_flow.topics_in_scope(db, goal_id, user_id)
        if not in_scope:
            logger.info("Goal %s: nothing in scope for cards yet", goal_id)
            return []
        # After the exam every topic is in scope, so the scope itself already
        # answers which material the cards should retain.
        topic = in_scope[0]
        topic_label = (
            f"{topic.title} (and the rest of the goal, exam passed)"
            if len(in_scope) > 1
            else topic.title
        )

    logger.info("Generating flashcards for topic %r", topic.title)
    content, source = await topic_flow.topic_material(db, topic, user_id)
    if source == "none":
        content = f"Topic name: {topic.title}"
    if source != "explanation":
        # With only a description or a title, the prompt's rule "answerable from
        # the content above and nothing else" leaves nothing to ask about, and the
        # model answers by asking about the wording instead. So the target is
        # restated: cards that set out what this topic has to cover.
        content = _THIN_PREAMBLE + content
    content = content[:5000]  # Limit context size

    client = get_llm_client()
    prompt = FLASHCARD_PROMPT.format(
        content=content, count=count, topic=topic_label,
    )
    if mode and mode in _MODE_FLASHCARD_HINTS:
        prompt += _MODE_FLASHCARD_HINTS[mode]
    response, _ = await client.chat(
        "You are an expert at creating educational flashcards. Output only valid JSON.",
        prompt,
    )

    # Parse response
    from libs.text_utils import parse_llm_json

    flashcards = parse_llm_json(response, default=[])
    if not isinstance(flashcards, list):
        logger.warning("Failed to parse flashcard JSON, returning empty")
        flashcards = []

    # Add metadata
    for i, card in enumerate(flashcards):
        card["id"] = str(uuid.uuid4())
        card["course_id"] = str(course_id)
        # Provenance: without this a saved card cannot be traced back to the topic
        # it came from, so the goal could not tell its own cards apart.
        card["topic_id"] = str(topic.id)
        card["goal_id"] = str(topic.goal_id)
        card["topic_title"] = topic.title
        card["fsrs"] = {
            "difficulty": 5.0,
            "stability": 0.0,
            "reps": 0,
            "lapses": 0,
            "state": "new",
            "due": None,
        }

    return flashcards


def review_flashcard(card_data: dict, rating: int) -> dict:
    """Process a flashcard review using FSRS algorithm.

    rating: 1=Again, 2=Hard, 3=Good, 4=Easy
    Returns updated card data with next review date.
    """
    fsrs_data = card_data.get("fsrs", {})

    card = FSRSCard(
        difficulty=fsrs_data.get("difficulty", 5.0),
        stability=fsrs_data.get("stability", 0.0),
        reps=fsrs_data.get("reps", 0),
        lapses=fsrs_data.get("lapses", 0),
        state=fsrs_data.get("state", "new"),
        last_review=datetime.fromisoformat(fsrs_data["last_review"]) if fsrs_data.get("last_review") else None,
    )

    card, log = review_card(card, rating)

    card_data["fsrs"] = {
        "difficulty": card.difficulty,
        "stability": card.stability,
        "reps": card.reps,
        "lapses": card.lapses,
        "state": card.state,
        "last_review": card.last_review.isoformat() if card.last_review else None,
        "due": card.due.isoformat() if card.due else None,
    }

    return card_data
