"""Which passages of the learner's own material belong to a topic.

FR-3.2 and FR-3.4. The TZ asks for explanations, tasks and questions to be built
from the material the learner uploaded, and for the answer to carry a reference
to the fragment it used. Neither happened: explanations were written from the
topic's one-line description, so the model was asked to teach something it had
nothing about.

Matching here is by keyword, and that is a deliberate limitation rather than a
placeholder. Vector search needs an embedding provider, and this deployment has
none configured — ``OPENAI_API_KEY`` is empty and the active provider is a custom
chat endpoint, so ``vector_search`` returns an empty list and
``embed_course_content`` writes nothing. Keyword matching is what actually
returns results on SQLite with no model, so it is what the grounding rests on.

Because keyword matching can pick the wrong section, the caller can override it:
a topic's ``source_node_id`` wins over whatever was found here, and the learner
can set that from the topic screen. The automatic match is the default, not the
authority.
"""

import logging
import re
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.content import CourseContentTree

logger = logging.getLogger(__name__)

# A passage is trimmed to this length before it reaches the prompt. Long enough
# to carry a definition with its example, short enough that eight of them still
# fit in one request.
MAX_PASSAGE_CHARS = 4000

# Words too common to match on. Russian and English stopwords, plus the tokens
# that every course title is made of.
_STOPWORDS = frozenset(
    """
    и или но а в во не что он на я с со как а то все она так его но да ты
    к у же вы за бы по только ее мне было вот от меня еще нет о из ему теперь
    когда даже ну вдруг ли если или ни быть был него до вас нибудь опять уж вам
    сказал ведь там потом себя ничего ей может они тут где есть надо ней для мы
    тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто
    этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы
    нее какие много разве три эту моя впрочем хорошо свою этой перед иногда
    лучше чуть том нельзя такой им более всегда конечно всю между это
    the a an and or of to in is are was were be for on with as at by from
    this that these those it its into about which who whom whose
    """.split()
)

# A title this short is a container heading ("Chapter 3"), not a subject.
_MIN_TITLE_CHARS = 3

# Below this the match is not worth putting in front of the model: a single
# common word occurring anywhere in a document matches almost every node.
_MIN_DISTINCT_TERMS = 2


def _terms(text: str) -> list[str]:
    """Content-bearing words, longest first so the specific ones count."""
    words = re.findall(r"[^\W\d_]{3,}", (text or "").lower(), flags=re.UNICODE)
    kept = [w for w in words if w not in _STOPWORDS]
    # Longer words are more specific, so they decide the ranking.
    return sorted(set(kept), key=len, reverse=True)


def _excerpt(node: CourseContentTree) -> str:
    text = (node.content or "").strip()
    if len(text) <= MAX_PASSAGE_CHARS:
        return text
    return text[:MAX_PASSAGE_CHARS].rstrip() + "\n…"


async def find_material_for_topic(
    db: AsyncSession,
    course_id: uuid.UUID,
    topic_title: str,
    topic_description: str | None = None,
    *,
    limit: int = 4,
    source_file: str | None = None,
) -> list[dict]:
    """Passages from ``course_id`` that look like they are about this topic.

    Returns dicts of ``node_id``, ``title``, ``source_file``, ``excerpt`` and
    ``matched_terms``, best first. Empty when nothing clears the bar — the
    caller then falls back to the description, and says so.

    ``source_file`` narrows the search to one uploaded file. The learner picked
    it explicitly, so the rest of the course — which may hold a different
    subject entirely — is not allowed to outvote it.
    """
    terms = _terms(topic_title)
    if not terms:
        return []

    # The description adds context the title lacks ("…by Gauss elimination"),
    # but the title stays primary: it is what the learner named.
    described = _terms(topic_description or "")[:6]
    probe_terms = list(dict.fromkeys(terms[:8] + described))

    conditions = [
        or_(
            CourseContentTree.title.ilike(f"%{t}%"),
            CourseContentTree.content.ilike(f"%{t}%"),
        )
        for t in probe_terms
    ]

    scope = [
        CourseContentTree.course_id == course_id,
        or_(*conditions),
    ]
    if source_file:
        scope.append(CourseContentTree.source_file == source_file)

    rows = await db.execute(select(CourseContentTree).where(*scope))
    candidates = list(rows.scalars().all())
    if not candidates:
        return []

    scored: list[tuple[float, dict]] = []
    for node in candidates:
        title = (node.title or "").lower()
        body = (node.content or "").lower()
        if not title and not body:
            continue
        # A node only counts if the topic's own words appear in it, not merely
        # somewhere in the course.
        title_hits = {t for t in probe_terms if t in title}
        body_hits = {t for t in probe_terms if t in body}
        distinct = title_hits | body_hits
        if len(distinct) < _MIN_DISTINCT_TERMS:
            continue

        # Weighting: a hit in the heading is worth more than one buried in the
        # body, and rare words are worth more than common ones.
        score = sum((3.0 if t in title_hits else 1.0) * len(t) for t in distinct)
        # Shallower nodes are summaries and usually the better source.
        score -= (node.level or 0) * 0.5
        if len(title or "") < _MIN_TITLE_CHARS:
            score -= 1.0

        excerpt = _excerpt(node)
        if not excerpt:
            continue

        scored.append(
            (
                score,
                {
                    "node_id": str(node.id),
                    "title": node.title,
                    "source_file": node.source_file,
                    "level": node.level,
                    "matched_terms": sorted(distinct),
                    "excerpt": excerpt,
                },
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _score, entry in scored[:limit]]


async def list_course_sources(db: AsyncSession, course_id: uuid.UUID) -> list[dict]:
    """The uploaded files a topic can be explained from, with node counts.

    File-level choice is what the learner actually knows ("this subject is from
    that book"); the automatic match guesses at node level and, in a course that
    holds several unrelated uploads, guesses wrong.
    """
    rows = await db.execute(
        select(CourseContentTree.source_file, func.count())
        .where(
            CourseContentTree.course_id == course_id,
            CourseContentTree.source_file.is_not(None),
        )
        .group_by(CourseContentTree.source_file)
        .order_by(CourseContentTree.source_file)
    )
    return [
        {"name": name, "nodes": count}
        for name, count in rows.all()
        if name and name.strip()
    ]


async def file_passages(
    db: AsyncSession,
    course_id: uuid.UUID,
    source_file: str,
    *,
    topic_title: str = "",
    topic_description: str | None = None,
    limit: int = 4,
) -> list[dict]:
    """Passages from one explicitly chosen file.

    Keyword matches inside the file come first; when none clear the bar, the
    file's own opening sections are used. Falling back to "no source" after an
    explicit choice would make the choice look broken, and the learner asked
    for *this* book to be read, not for a keyword game.
    """
    matched = await find_material_for_topic(
        db,
        course_id,
        topic_title,
        topic_description,
        limit=limit,
        source_file=source_file,
    )
    if matched:
        return matched

    rows = await db.execute(
        select(CourseContentTree)
        .where(
            CourseContentTree.course_id == course_id,
            CourseContentTree.source_file == source_file,
        )
        .order_by(CourseContentTree.level, CourseContentTree.order_index)
    )
    passages: list[dict] = []
    for node in rows.scalars().all():
        excerpt = _excerpt(node)
        if not excerpt:
            continue
        passages.append(
            {
                "node_id": str(node.id),
                "title": node.title,
                "source_file": node.source_file,
                "level": node.level,
                "matched_terms": [],
                "excerpt": excerpt,
            }
        )
        if len(passages) >= limit:
            break
    return passages


def as_grounding_text(passages: list[dict]) -> tuple[str, str]:
    """Join passages into the ``(text, label)`` shape the prompts take.

    The label names every file that contributed, because FR-3.4 asks the answer
    to say which section it used — that is the whole value of grounding in the
    learner's own material rather than the model's recall.
    """
    if not passages:
        return "", ""

    blocks = []
    for index, passage in enumerate(passages, start=1):
        heading = (
            passage.get("title") or passage.get("source_file") or f"Раздел {index}"
        )
        origin = passage.get("source_file")
        shown = f"{heading} (файл: {origin})" if origin else heading
        blocks.append(f"[{index}] {shown}\n{passage['excerpt']}")

    files: list[str] = []
    for passage in passages:
        origin = passage.get("source_file")
        if origin and origin not in files:
            files.append(origin)

    label = ", ".join(files[:3]) + ("…" if len(files) > 3 else "")
    return "\n\n".join(blocks), label


async def get_passages_by_ids(
    db: AsyncSession, course_id: uuid.UUID, node_ids: list[uuid.UUID]
) -> list[dict]:
    """Fetch specific nodes by id, for the manual override.

    Scoped to the course: a learner must not be able to bind a topic to a node
    from a course they cannot see.
    """
    if not node_ids:
        return []
    rows = await db.execute(
        select(CourseContentTree).where(
            CourseContentTree.course_id == course_id,
            CourseContentTree.id.in_(node_ids),
        )
    )
    by_id = {node.id: node for node in rows.scalars().all()}
    passages = []
    for node_id in node_ids:
        node = by_id.get(node_id)
        if node is None:
            logger.warning("Bound node %s is not in course %s", node_id, course_id)
            continue
        excerpt = _excerpt(node)
        if not excerpt:
            continue
        passages.append(
            {
                "node_id": str(node.id),
                "title": node.title,
                "source_file": node.source_file,
                "level": node.level,
                "matched_terms": [],
                "excerpt": excerpt,
            }
        )
    return passages
