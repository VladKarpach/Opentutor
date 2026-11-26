"""Study plan scheduling — TZ sections 6.4 and 6.5.

Pure deterministic code, no LLM (TZ section 6.4: "scheduling, intervals and
strength calculation are performed by deterministic code. The model is
responsible for content").

Implements:
- FR-5.3: topological ordering of the dependency graph
- FR-6.1/6.2: plan built from topics, skipping or shrinking already-known ones
- FR-6.3: 10-15% time reserve and review slots
- FR-6.4: warn when the deadline is unrealistic and offer options
- FR-10.3 / section 6.5: speed coefficient from actual vs planned time
- Section 6.4: overdue reviews first, then new topics
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from config import settings

from services.adaptive_learning.repetition import (
    STATUS_KNOWN,
    STATUS_WEAK,
    TopicReviewState,
    current_strength,
)

logger = logging.getLogger(__name__)

# FR-6.3: reserve share of the available time.
RESERVE_RATIO = 0.12

# FR-6.2: a topic at/above this strength is skipped, below KNOWN_SKIP_RATIO of
# it is shrunk instead.
KNOWN_SKIP_RATIO = 0.8
WEAK_SHRINK_RATIO = 0.5

# FR-10.1: a topic already covered in another goal is a refresher. Kept here
# rather than imported from the context module so the planner stays free of the
# database layer, and so the wording cannot drift from the thresholds used here.
PRIOR_KNOWN_RATIO = 0.8
PRIOR_FAMILIAR_RATIO = 0.5


def _PRIOR_REASON(prior_strength: float) -> str:
    """Learner-facing reason for a topic that was shortened by prior knowledge."""
    if prior_strength >= PRIOR_KNOWN_RATIO:
        return "уже освоено ранее — короткое повторение"
    if prior_strength >= PRIOR_FAMILIAR_RATIO:
        return "знакомо ранее — время сокращено"
    return "частично знакомо ранее"

# Section 6.5 speed coefficient bounds.
MIN_SPEED = 0.5
MAX_SPEED = 2.0

# Mode-based scheduling overrides (FR-10.x: the plan reshapes itself to the
# chosen learning regime instead of always being deadline-driven).
MODE_SPEED: dict[str, float] = {
    "self_paced": 1.0,
    "course_following": 1.0,
}
MODE_RESERVE: dict[str, float] = {
    "self_paced": 0.12,
    "course_following": 0.12,
}

PlanItemType = str  # study | review | practice | exam
ITEM_STUDY = "study"
ITEM_REVIEW = "review"
ITEM_PRACTICE = "practice"
ITEM_EXAM = "exam"

# The last two days of a plan are reserved for the two things that are not
# "learn another topic": solving the tasks that unlock the exam, and taking the
# exam itself. Keeping the wording here means the calendar, the plan list and
# the daily session all describe those days the same way.
PRACTICE_DAY_TITLE = "Практика для допуска к экзамену"
EXAM_DAY_TITLE = "Экзамен"
PRACTICE_DAY_REASON = "решаем задания по всем темам, чтобы открыть экзамен"
EXAM_DAY_REASON = "итоговая проверка знаний по цели"
RESERVED_SPECIAL_DAYS = 2

# Контрольные задания по теме: по умолчанию 3 на тему (confident / deep), 2 — на обзор.
# Каждое контрольное задание отводит фиксированный слот в плане, чтобы learner видел
# в расписании, когда именно их решать.
CONTROL_ASSIGNMENT_MINUTES = 10

# Закрепление на следующий день: по той же теме, но время в 2 раза меньше.
FOLLOW_UP_DAY_RATIO = 0.5

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_MISSED = "missed"


@dataclass
class TopicInput:
    """A topic as the planner sees it."""

    id: str
    title: str
    estimated_minutes: int
    difficulty: str = "medium"
    # Relative difficulty (0.5 = as expected) — see ``relative_difficulty``.
    # None means no graded evidence yet, so no adaptive adjustment.
    fsrs_difficulty: float | None = None
    prerequisites: list[str] = field(default_factory=list)
    state: TopicReviewState | None = None
    available_from: date | None = None

    # FR-10.1 / section 6.4: how well this subject was already covered in the
    # learner's *other* goals. 0 means "never seen". The planner uses it to
    # shorten a refresher instead of booking a full first pass, so the plan is
    # built from what the person already knows rather than from the topic list.
    prior_strength: float = 0.0


@dataclass
class PlanItem:
    """One scheduled unit of work.

    ``topic_id`` is optional because the last two days of a plan are not tied to
    a single topic: one is for the practice series that unlocks the exam, the
    other is the exam itself.
    """

    title: str
    item_type: PlanItemType
    day: date
    planned_minutes: int
    topic_id: str | None = None
    status: str = STATUS_PENDING
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "item_type": self.item_type,
            "day": self.day.isoformat(),
            "planned_minutes": self.planned_minutes,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class PlanResult:
    """Full scheduling outcome, including feasibility warnings (FR-6.4)."""

    items: list[PlanItem]
    days: dict[str, list[PlanItem]]
    total_minutes: int = 0
    available_minutes: int = 0
    feasible: bool = True
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    speed: float = 1.0


# ──────────────────────────────────────────────────────────────
# Graph ordering (FR-5.2, FR-5.3)
# ──────────────────────────────────────────────────────────────

def find_cycle(topics: dict[str, TopicInput]) -> list[str] | None:
    """FR-5.2: the TZ requires a DAG and a check for cycles.

    Returns the participating topic ids if a cycle exists, otherwise None.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in topics}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GREY
        stack.append(node)
        for prereq in topics[node].prerequisites:
            if prereq not in topics:
                continue  # dangling prerequisite, ignore rather than crash
            if color[prereq] == GREY:
                start = stack.index(prereq)
                return stack[start:] + [prereq]
            if color[prereq] == WHITE:
                found = visit(prereq)
                if found:
                    return found
        color[node] = BLACK
        stack.pop()
        return None

    for tid in topics:
        if color[tid] == WHITE:
            cycle = visit(tid)
            if cycle:
                return cycle
    return None


def topological_order(
    topics: dict[str, TopicInput],
    strength_lookup=None,
) -> list[str]:
    """FR-5.3: learning order from the dependency DAG.

    Among topics whose prerequisites are already satisfied, the weakest one goes
    first so that gaps get closed before new material is introduced
    (FR-5.4, "find the root of the problem").
    """
    remaining = dict(topics)
    ordered: list[str] = []
    placed: set[str] = set()

    def strength_of(tid: str) -> float:
        if strength_lookup:
            return strength_lookup(tid)
        st = topics[tid].state
        return current_strength(st) if st else 0.0

    while remaining:
        ready = [
            tid
            for tid, topic in remaining.items()
            if all(
                prereq not in remaining or prereq in placed
                for prereq in topic.prerequisites
            )
        ]
        if not ready:
            # Cycle or unsatisfiable dependencies: keep deterministic progress.
            logger.warning("Topic graph has unresolved dependencies; scheduling remaining by title")
            ready = sorted(remaining)
        ready.sort(key=lambda tid: (strength_of(tid), topics[tid].title))
        chosen = ready[0]
        ordered.append(chosen)
        placed.add(chosen)
        del remaining[chosen]

    return ordered


# ──────────────────────────────────────────────────────────────
# Speed coefficient (FR-10.3, section 6.5)
# ──────────────────────────────────────────────────────────────

def speed_coefficient(actual: list[float], planned: list[float]) -> float:
    """Section 6.5: median(actual / planned) over the last N topics, clamped."""
    ratios: list[float] = []
    for act, plan in zip(actual, planned):
        if plan and plan > 0:
            ratios.append(act / plan)
    if not ratios:
        return 1.0
    median = statistics.median(ratios)
    return max(MIN_SPEED, min(MAX_SPEED, median))


# Difficulty multipliers from config. Keys are the LearningTopic.difficulty
# values; missing keys fall back to medium (1.0).
_DIFFICULTY_MULTIPLIERS: dict[str, float] = {
    "easy": settings.difficulty_multiplier_easy,
    "medium": settings.difficulty_multiplier_medium,
    "hard": settings.difficulty_multiplier_hard,
}


def difficulty_multiplier(difficulty: str | None) -> float:
    """Return the static time multiplier for a topic difficulty level.

    Unknown or missing difficulty is treated as medium.
    """
    if not difficulty:
        return _DIFFICULTY_MULTIPLIERS["medium"]
    return _DIFFICULTY_MULTIPLIERS.get(difficulty, _DIFFICULTY_MULTIPLIERS["medium"])


# Where the initial (model- or learner-given) estimate puts a topic on the
# 0..1 difficulty scale. Raw FSRS difficulty is normalised onto the same
# scale, so the two numbers can be compared.
_DIFFICULTY_PRIORS: dict[str, float] = {
    "easy": settings.difficulty_prior_easy,
    "medium": settings.difficulty_prior_medium,
    "hard": settings.difficulty_prior_hard,
}

# "As expected" point on the relative-difficulty scale stored on TopicState.
NEUTRAL_DIFFICULTY = 0.5

# FSRS-4.5 stores difficulty on a 1..10 scale (LearningProgress defaults to 5).
FSRS_MIN_DIFFICULTY = 1.0
FSRS_MAX_DIFFICULTY = 10.0


def difficulty_prior(difficulty: str | None) -> float:
    """Initial difficulty estimate on the 0..1 scale.

    Unknown or missing difficulty is treated as medium, like the multiplier.
    """
    if not difficulty:
        return _DIFFICULTY_PRIORS["medium"]
    return _DIFFICULTY_PRIORS.get(difficulty, _DIFFICULTY_PRIORS["medium"])


def normalise_raw_fsrs(value: float | None) -> float | None:
    """Map a raw FSRS difficulty (1..10) onto 0..1.

    ``None`` passes through, because "no cards" is not the same as "medium".
    Callers use this before :func:`normalise_fsrs_difficulty`, which assumes
    the value is already on the 0..1 scale.
    """
    if value is None:
        return None
    span = FSRS_MAX_DIFFICULTY - FSRS_MIN_DIFFICULTY
    return max(0.0, min(1.0, (value - FSRS_MIN_DIFFICULTY) / span))


def observed_difficulty_from_score(final_score: float) -> float:
    """How hard one graded answer implies the topic is.

    0 = answered effortlessly and correctly, 1 = got it completely wrong.
    """
    return max(0.0, min(1.0, 1.0 - final_score))


def relative_difficulty(difficulty: str | None, observed: float | None) -> float | None:
    """Where actual performance sits relative to the initial estimate.

    ``NEUTRAL_DIFFICULTY`` means "as expected"; above it the topic is harder
    than the estimate said, below it easier. Anchoring at the expectation is
    what stops the adaptive factor from double-counting the static multiplier:
    a hard topic that performs exactly as a hard topic should gets no extra
    multiplier on top of the one it already has.
    """
    if observed is None:
        return None
    gap = max(0.0, min(1.0, observed)) - difficulty_prior(difficulty)
    return max(0.0, min(1.0, NEUTRAL_DIFFICULTY + gap))


def update_relative_difficulty(
    current: float | None,
    difficulty: str | None,
    observed: float | None,
    *,
    alpha: float | None = None,
) -> float | None:
    """Fold one observation into the stored relative difficulty (EMA).

    Starts from neutral — the initial estimate already lives in the static
    multiplier — and moves ``alpha`` of the way toward each new target, so one
    lucky or unlucky answer cannot flip a topic. ``None`` current is treated
    as neutral rather than as "unknown", because the actual estimate exists
    and is neutral on this relative scale by definition.
    """
    target = relative_difficulty(difficulty, observed)
    if target is None:
        return current
    weight = settings.adaptive_difficulty_alpha if alpha is None else alpha
    base = NEUTRAL_DIFFICULTY if current is None else current
    return max(0.0, min(1.0, base + weight * (target - base)))


def normalise_fsrs_difficulty(value: float | None) -> float:
    """Clamp an already-normalised difficulty to the 0..1 range.

    Raw FSRS difficulty (1..10) must go through :func:`normalise_raw_fsrs`
    first; this function only guards against values that slipped outside the
    scale. ``None`` returns 0.5 (neutral, no adaptive adjustment).
    """
    if value is None:
        return 0.5
    return max(0.0, min(1.0, value))


def adaptive_difficulty_factor(fsrs_difficulty: float | None) -> float:
    """Adaptive time multiplier from the topic's relative difficulty.

    ``adaptive_factor = clamp(1.0 + k * (relative_difficulty - 0.5), min, max)``.

    The input is the relative value stored on ``TopicState`` (see
    :func:`relative_difficulty`), not raw FSRS difficulty. 0.5 means the
    learner is doing as well as the initial estimate implied, so the factor is
    1.0; higher means harder than expected, lower easier.
    """
    if fsrs_difficulty is None:
        return 1.0
    norm = normalise_fsrs_difficulty(fsrs_difficulty)
    factor = 1.0 + settings.adaptive_difficulty_k * (norm - 0.5)
    return max(
        settings.adaptive_difficulty_min,
        min(settings.adaptive_difficulty_max, factor),
    )


# ──────────────────────────────────────────────────────────────
# Feasibility (FR-6.4)
# ──────────────────────────────────────────────────────────────

def _estimate_minutes(topic: TopicInput, today: date) -> int:
    """Shrink or skip topics based on current strength (FR-6.2).

    The base estimate is first scaled by the topic's static difficulty
    multiplier (easy / medium / hard), then by the strength-based factor, and
    finally by the adaptive factor derived from how the learner is actually
    doing relative to that initial estimate.
    """
    minutes = topic.estimated_minutes

    # Static difficulty weight: hard topics get more time, easy ones less.
    minutes = int(round(minutes * difficulty_multiplier(topic.difficulty)))

    st = topic.state
    strength: float | None = None
    if st is not None:
        strength = current_strength(st)
        if strength >= KNOWN_SKIP_RATIO or st.status == STATUS_KNOWN:
            logger.debug(
                "topic=%s difficulty=%s strength=%.2f minutes=0 (known/skipped)",
                topic.id, topic.difficulty, strength,
            )
            return 0
        if strength <= WEAK_SHRINK_RATIO or st.status == STATUS_WEAK:
            minutes = int(round(minutes * 1.3))  # weak topics need more, not less
        else:
            minutes = int(round(minutes * (1.0 - 0.3 * strength)))

    # Adaptive factor from how the learner is actually doing relative to the
    # initial estimate: more time only for difficulty that was not expected.
    # Applies without a TopicState too, because card reviews can carry the
    # signal for a topic that was never graded directly.
    adaptive = adaptive_difficulty_factor(topic.fsrs_difficulty)
    before = minutes
    minutes = int(round(minutes * adaptive))
    logger.debug(
        "topic=%s difficulty=%s relative_difficulty=%s strength=%s "
        "minutes=%s->%s adaptive=%.2f",
        topic.id, topic.difficulty, topic.fsrs_difficulty, strength,
        before, minutes, adaptive,
    )
    return minutes


def check_feasibility(
    required_minutes: int,
    days: int,
    minutes_per_day: int,
) -> tuple[bool, list[str], list[str]]:
    """FR-6.4: warn and propose options when the deadline cannot be met."""
    available = days * minutes_per_day
    if available >= required_minutes:
        return True, [], []

    shortfall = required_minutes - available
    warnings = [
        f"The plan needs {required_minutes // 60}h {required_minutes % 60}m, "
        f"but there is only {available // 60}h {available % 60}m available."
    ]
    suggestions = [
        f"Increase study time to about {max(1, -(-required_minutes // max(1, days)))} minutes per day.",
        "Reduce the depth: switch from confident mastery to an overview of some topics.",
        "Drop topics: mark the least important ones as skippable.",
    ]
    logger.info("Infeasible plan: shortfall of %s minutes", shortfall)
    return False, warnings, suggestions


# ──────────────────────────────────────────────────────────────
# Plan builder (FR-6.1, FR-6.3, section 6.4)
# ──────────────────────────────────────────────────────────────

def build_plan(
    topics: list[TopicInput],
    *,
    start_day: date,
    days: int,
    minutes_per_day: int,
    speed: float | None = None,
    review_minutes: int = 15,
    today: date | None = None,
    mode: str | None = None,
    reserve_ratio: float | None = None,
    control_counts: dict[str, int] | None = None,
    next_day_followup: bool = False,
) -> PlanResult:
    """Build a day-by-day plan.

    Order within the plan: overdue reviews first (section 6.4 step 4), then new
    topics in topological order. A reserve of RESERVE_RATIO is held back so the
    deadline can absorb overruns (FR-6.3).

    When ``mode`` is given and no explicit ``speed``/``reserve_ratio`` is
    passed, the learning regime shapes the schedule instead of plain defaults.
    """
    today = today or start_day
    topic_map = {t.id: t for t in topics}

    # A caller-supplied speed/reserve always wins; otherwise fall back to the
    # mode's preset, then the global defaults.
    effective_speed = speed if speed is not None else MODE_SPEED.get(mode, 1.0)
    effective_reserve = (
        reserve_ratio if reserve_ratio is not None else MODE_RESERVE.get(mode, RESERVE_RATIO)
    )

    cycle = find_cycle(topic_map)
    warnings: list[str] = []
    if cycle:
        warnings.append(
            "Dependency cycle detected in topics: " + " -> ".join(cycle) + ". "
            "Order may not respect all prerequisites."
        )

    ordered = topological_order(topic_map, strength_lookup=lambda tid: (
        current_strength(topic_map[tid].state) if topic_map[tid].state else 0.0
    ))

    # Build the ordered work list: for each topic — due review first, then study,
    # then its control-assignment practice items (контрольные задания). Keeping
    # them in one list lets the day-capacity loop place everything the same way,
    # so the control assignments land on concrete days just like study items.
    control_counts = control_counts or {}
    work: list[PlanItem] = []

    for tid in ordered:
        topic = topic_map[tid]
        minutes = _estimate_minutes(topic, today)
        if minutes <= 0:
            continue
        adjusted = max(5, int(round(minutes * effective_speed)))
        st = topic.state
        is_due = st is not None and st.next_review_at is not None and st.next_review_at <= _to_datetime(today)
        if is_due:
            work.append(
                PlanItem(
                    topic_id=tid,
                    title=topic.title,
                    item_type=ITEM_REVIEW,
                    day=today,
                    planned_minutes=max(review_minutes, int(adjusted * 0.4)),
                    reason="подошёл срок повторения",
                )
            )
            adjusted = max(5, int(round(adjusted * 0.6)))
        if getattr(topic, "prior_strength", 0.0) > 0.0:
            study_reason = _PRIOR_REASON(getattr(topic, "prior_strength", 0.0))
        elif topic.prerequisites:
            study_reason = "порядок предпосылок"
        else:
            study_reason = "начальная тема"
        work.append(
            PlanItem(
                topic_id=tid,
                title=topic.title,
                item_type=ITEM_STUDY,
                day=today,
                planned_minutes=adjusted,
                reason=study_reason,
            )
        )
        # Контрольные задания по теме: по умолчанию 3 на тему (confident / deep).
        # Каждее — отдельный пункт плана с фиксированным слотом, чтобы в
        # расписании было видно, когда их решать.
        n_control = int(control_counts.get(tid, 0))
        for ci in range(n_control):
            work.append(
                PlanItem(
                    title=f"Контрольное задание {ci + 1} по теме «{topic.title}»",
                    item_type=ITEM_PRACTICE,
                    day=today,
                    planned_minutes=CONTROL_ASSIGNMENT_MINUTES,
                    topic_id=tid,
                    reason="контрольное задание по теме",
                )
            )

    total_needed = sum(i.planned_minutes for i in work)

    # The last two days are not available for new topics: one is the practice
    # day that unlocks the exam, the other is the exam itself. Very short goals
    # keep their study days and get those two days appended after the deadline;
    # the warning below says so instead of silently dropping the preparation.
    reserve_special = days > RESERVED_SPECIAL_DAYS
    study_days = max(1, days - RESERVED_SPECIAL_DAYS) if reserve_special else days
    study_capacity = study_days * minutes_per_day
    reserve = int(round(total_needed * effective_reserve))
    usable = max(0, study_capacity - reserve)
    capacity = days * minutes_per_day

    feasible, feas_warnings, suggestions = check_feasibility(
        total_needed, study_days, minutes_per_day
    )
    warnings.extend(feas_warnings)
    if not reserve_special:
        warnings.append(
            "Срок короткий: дни практики перед экзаменом и сам экзамен "
            "добавлены отдельно после последнего учебного дня."
        )

    items: list[PlanItem] = []
    # Reviews come first on each day (section 6.4 step 4).
    day_capacity = max(1, minutes_per_day)  # hard per-day ceiling
    day_index = 0
    cursor_day = start_day
    cursor_minutes = 0
    scheduled_total = 0

    def advance_day() -> None:
        nonlocal day_index, cursor_day, cursor_minutes
        day_index += 1
        cursor_day = start_day + timedelta(days=day_index)
        cursor_minutes = 0

    for item in work:
        # FR-6.3: the reserve is held back from the *total* budget, not per day.
        if (
            scheduled_total + item.planned_minutes > usable
            and day_index < study_days - 1
        ):
            warnings.append(
                f"The {effective_reserve:.0%} time reserve was reached; "
                f"'{item.title}' was deferred."
            )
            break
        if scheduled_total + item.planned_minutes > study_capacity:
            warnings.append(
                f"'{item.title}' did not fit into {study_days} study day(s) "
                "and was deferred."
            )
            break

        # Move to the next day when this item no longer fits in today's budget.
        if cursor_minutes + item.planned_minutes > day_capacity:
            if day_index < study_days - 1:
                advance_day()
            else:
                warnings.append(
                    f"'{item.title}' needs {item.planned_minutes} min but the "
                    f"last study day has only "
                    f"{day_capacity - cursor_minutes} min left."
                )
                break

        room_today = day_capacity - cursor_minutes
        if item.planned_minutes > room_today:
            # FR-6.4 / split oversized items across days.
            warnings.append(
                f"'{item.title}' was reduced from {item.planned_minutes} to "
                f"{room_today} min to fit the daily limit."
            )
            item.planned_minutes = max(5, room_today)

        item.day = cursor_day
        items.append(item)
        cursor_minutes += item.planned_minutes
        scheduled_total += item.planned_minutes

    # Закрепление на следующий день: после изучения темы на следующий день
    # ставится то же самое, но время в 2 раза меньше (не меньше 5 минут).
    # Не добавляется, если следующий день выходит за предельные дни плана
    # (от réserve под практику и экзамен).
    if next_day_followup:
        study_items_by_day: dict[date, list[PlanItem]] = {}
        for item in items:
            if item.item_type == ITEM_STUDY and item.topic_id:
                study_items_by_day.setdefault(item.day, []).append(item)

        last_plan_day = start_day + timedelta(days=study_days - 1)
        for study_day, study_items in study_items_by_day.items():
            follow_day = study_day + timedelta(days=1)
            if follow_day > last_plan_day:
                continue
            for study_item in study_items:
                follow_minutes = max(5, study_item.planned_minutes // 2)
                items.append(
                    PlanItem(
                        topic_id=study_item.topic_id,
                        title=study_item.title,
                        item_type=ITEM_REVIEW,
                        day=follow_day,
                        planned_minutes=follow_minutes,
                        reason="закрепление на следующий день (время в 2 раза меньше)",
                    )
                )

    # The two dedicated days are always present, even when the topic list is
    # short or every topic was already known. A plan without them would make the
    # exam look like an optional extra rather than the end of the route.
    practice_day = start_day + timedelta(days=study_days)
    exam_day = practice_day + timedelta(days=1)
    items.extend(
        [
            PlanItem(
                title=PRACTICE_DAY_TITLE,
                item_type=ITEM_PRACTICE,
                day=practice_day,
                planned_minutes=max(1, minutes_per_day),
                topic_id=None,
                reason=PRACTICE_DAY_REASON,
            ),
            PlanItem(
                title=EXAM_DAY_TITLE,
                item_type=ITEM_EXAM,
                day=exam_day,
                planned_minutes=max(1, minutes_per_day),
                topic_id=None,
                reason=EXAM_DAY_REASON,
            ),
        ]
    )

    days_map: dict[str, list[PlanItem]] = {}
    for item in items:
        days_map.setdefault(item.day.isoformat(), []).append(item)

    return PlanResult(
        items=items,
        days=days_map,
        total_minutes=sum(i.planned_minutes for i in items),
        available_minutes=capacity,
        feasible=feasible,
        warnings=warnings,
        suggestions=suggestions,
        speed=effective_speed,
    )


def _to_datetime(day: date):
    from datetime import datetime, timezone
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def soft_return_plan(
    topics: list[TopicInput],
    *,
    today: date,
    days: int,
    minutes_per_day: int,
    missed_days: int,
    mode: str | None = None,
    reserve_ratio: float | None = None,
    control_counts: dict[str, int] | None = None,
    next_day_followup: bool = False,
) -> PlanResult:
    """FR-10.5: 'I missed days' mode.

    Instead of piling up the whole backlog into a compressed schedule, this
    drops the oldest overdue reviews and keeps only the weakest topics, so the
    user returns gently.
    """
    warnings = [f"Catch-up mode: {missed_days} missed day(s) were compressed."]
    kept = topics[: max(1, len(topics) // 2)]
    dropped = len(topics) - len(kept)
    if dropped:
        warnings.append(f"{dropped} of the lowest-priority topics were postponed.")

    plan = build_plan(
        kept,
        start_day=today,
        days=days,
        minutes_per_day=minutes_per_day,
        mode=mode,
        reserve_ratio=reserve_ratio,
        control_counts=control_counts,
        next_day_followup=next_day_followup,
    )
    plan.warnings = warnings + plan.warnings
    return plan