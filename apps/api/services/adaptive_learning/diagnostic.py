"""Hidden level diagnostic — TZ section 6.1 and FR-4.

The TZ is explicit that this must feel like a conversation, not an intake form:

    FR-4.1: the AI holds a 4-8 reply dialogue and NEVER asks direct questions
            about the learner's level ("what is your level", "rate yourself").

What is deterministic here, and what is left to the LLM:

- deterministic (this module): which subtopic to probe next, how to fold an
  answer into the estimate, when the dialogue is finished, and the summary that
  is shown back to the learner (FR-4.5);
- LLM (agent layer above): the actual wording of each turn and the grading of
  the learner's reply against the criteria fixed with the question.

Section 6.1 algorithm:

1. per subtopic keep an estimate ``p`` (starts at 0.5) and an uncertainty ``u``
2. each step pick the subtopic with the largest ``u``, with a question whose
   difficulty is close to the current ``p``
3. after an answer: ``p <- p + alpha * (score - p)``, uncertainty shrinks
4. finish when uncertainty falls below a threshold or the reply budget is spent
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# FR-4.1: 4-8 replies.
MIN_REPLIES = 4
MAX_REPLIES = 8

# Section 6.1 step 2: p starts at 0.5.
INITIAL_ESTIMATE = 0.5
INITIAL_UNCERTAINTY = 1.0

# Section 6.1 step 4: p <- p + alpha * (score - p).
DEFAULT_ALPHA = 0.3
# Difficulty is spread with a little noise so the same topic is not asked
# identically every time.
DEFAULT_ALPHA_JITTER = 0.1

# Stop once the aggregate uncertainty drops below this (section 6.1 step 5).
UNCERTAINTY_FLOOR = 0.15
UNCERTAINTY_DECAY = 0.6

# FR-4.3: question difficulty is chosen close to the current estimate.
DIFFICULTY_TOLERANCE = 0.25


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class SubtopicEstimate:
    """Section 6.1 step 2: p and u for one subtopic."""

    id: str
    title: str
    estimate: float = INITIAL_ESTIMATE
    uncertainty: float = INITIAL_UNCERTAINTY
    asked: int = 0
    answered: int = 0
    last_score: float | None = None

    def difficulty(self) -> float:
        """FR-4.3: target difficulty near the current estimate, nudged upward."""
        return _clamp(self.estimate, 0.0, 1.0)


@dataclass
class DiagnosticTurn:
    """One step of the dialogue."""

    subtopic_id: str
    subtopic_title: str
    turn_index: int
    target_difficulty: float
    reason: str


@dataclass
class DiagnosticOutcome:
    """Result of a finished dialogue."""

    estimates: list[dict]
    turns: list[DiagnosticTurn]
    finished_reason: str
    aggregate_uncertainty: float
    started_at: datetime
    completed_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "estimates": self.estimates,
            "turns": [
                {
                    "subtopic_id": t.subtopic_id,
                    "subtopic_title": t.subtopic_title,
                    "turn_index": t.turn_index,
                    "target_difficulty": round(t.target_difficulty, 3),
                    "reason": t.reason,
                }
                for t in self.turns
            ],
            "finished_reason": self.finished_reason,
            "aggregate_uncertainty": round(self.aggregate_uncertainty, 3),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class DiagnosticSession:
    """Stateful driver for the hidden diagnostic dialogue."""

    def __init__(
        self,
        subtopics: list[dict],
        *,
        max_replies: int = MAX_REPLIES,
        min_replies: int = MIN_REPLIES,
        alpha: float = DEFAULT_ALPHA,
        random_state: random.Random | None = None,
    ):
        if not subtopics:
            raise ValueError("A diagnostic needs at least one subtopic")
        self._rng = random_state or random.Random()
        self.alpha = alpha
        self.max_replies = max(min_replies, max_replies)
        self.min_replies = min_replies
        self.started_at = datetime.now(timezone.utc)
        self.turns: list[DiagnosticTurn] = []
        # Total turns asked across the whole dialogue, including turns asked
        # before this instance was restored from a snapshot. `self.turns` only
        # holds what this instance produced, so the reply budget must not be
        # derived from its length or a restored session would restart its count.
        self.asked_total = 0
        self._estimates = [
            SubtopicEstimate(id=str(s.get("id", i)), title=str(s.get("title", f"Topic {i}")))
            for i, s in enumerate(subtopics)
        ]
        self._finished_reason = ""

    # ── introspection ────────────────────────────────────────

    @property
    def reply_count(self) -> int:
        return self.asked_total

    @property
    def finished(self) -> bool:
        return bool(self._finished_reason)

    def estimates(self) -> list[SubtopicEstimate]:
        return list(self._estimates)

    def aggregate_uncertainty(self) -> float:
        if not self._estimates:
            return 0.0
        return sum(e.uncertainty for e in self._estimates) / len(self._estimates)

    # ── section 6.1 step 2-3 ────────────────────────────────

    def next_turn(self) -> DiagnosticTurn | None:
        """Pick the subtopic with the largest uncertainty and a matching difficulty."""
        if self.finished:
            return None
        if self.reply_count >= self.max_replies:
            self._finished_reason = "reply_budget_exhausted"
            return None
        if self.reply_count >= self.min_replies and self.aggregate_uncertainty() <= UNCERTAINTY_FLOOR:
            self._finished_reason = "uncertainty_below_threshold"
            return None

        # Fewest probes first, then highest uncertainty (section 6.1 step 3).
        target = min(
            self._estimates,
            key=lambda e: (e.asked, -e.uncertainty),
        )
        turn_index = self.asked_total
        turn = DiagnosticTurn(
            subtopic_id=target.id,
            subtopic_title=target.title,
            turn_index=turn_index,
            target_difficulty=target.difficulty(),
            reason=(
                "least probed subtopic"
                if target.asked == min(e.asked for e in self._estimates)
                else "highest uncertainty"
            ),
        )
        self.turns.append(turn)
        self.asked_total += 1
        target.asked += 1
        return turn

    # ── section 6.1 step 4 ──────────────────────────────────

    def record_answer(
        self,
        subtopic_id: str,
        score: float,
        *,
        finished: bool = False,
    ) -> DiagnosticOutcome:
        """Fold one graded answer into the estimate.

        ``p <- p + alpha * (score - p)``, with a small randomised alpha so the
        dialogue does not feel mechanical.
        """
        score = _clamp(score, 0.0, 1.0)
        target = next((e for e in self._estimates if e.id == subtopic_id), None)
        if target is None:
            raise KeyError(f"Unknown subtopic: {subtopic_id}")

        alpha = _clamp(
            self.alpha + self._rng.uniform(-DEFAULT_ALPHA_JITTER, DEFAULT_ALPHA_JITTER),
            0.05,
            1.0,
        )
        target.estimate = _clamp(target.estimate + alpha * (score - target.estimate), 0.0, 1.0)
        target.uncertainty = _clamp(target.uncertainty * UNCERTAINTY_DECAY, 0.0, 1.0)
        target.answered += 1
        target.last_score = score

        if finished and not self._finished_reason:
            self._finished_reason = "cancelled_by_learner"

        return self.summary()

    def finish(self, reason: str = "completed") -> DiagnosticOutcome:
        if not self._finished_reason:
            self._finished_reason = reason
        return self.summary()

    # ── persistence ─────────────────────────────────────────

    def snapshot(self) -> dict:
        """Serialise the estimator state so a dialogue can be resumed.

        Replaying the transcript would be fragile: the RNG jitter and the turn
        ordering both depend on state that is not in the transcript. Storing the
        estimates directly makes resumption exact.
        """
        return {
            "max_replies": self.max_replies,
            "min_replies": self.min_replies,
            "alpha": self.alpha,
            "asked_total": self.asked_total,
            "finished_reason": self._finished_reason,
            "estimates": {
                e.id: {
                    "title": e.title,
                    "estimate": e.estimate,
                    "uncertainty": e.uncertainty,
                    "asked": e.asked,
                    "answered": e.answered,
                    "last_score": e.last_score,
                }
                for e in self._estimates
            },
        }

    @classmethod
    def restore(cls, subtopics: list[dict], snapshot: dict | None) -> "DiagnosticSession":
        """Rebuild a session from a stored snapshot.

        With no snapshot this is a fresh session over ``subtopics``.
        """
        snapshot = snapshot or {}
        session = cls(
            subtopics,
            max_replies=int(snapshot.get("max_replies") or MAX_REPLIES),
            min_replies=int(snapshot.get("min_replies") or MIN_REPLIES),
            alpha=float(snapshot.get("alpha") or DEFAULT_ALPHA),
        )
        stored = snapshot.get("estimates") or {}
        for estimate in session._estimates:
            saved = stored.get(estimate.id)
            if not saved:
                continue
            estimate.estimate = _clamp(float(saved.get("estimate", INITIAL_ESTIMATE)), 0.0, 1.0)
            estimate.uncertainty = _clamp(float(saved.get("uncertainty", INITIAL_UNCERTAINTY)), 0.0, 1.0)
            estimate.asked = int(saved.get("asked", 0))
            estimate.answered = int(saved.get("answered", 0))
            estimate.last_score = saved.get("last_score")
        session.asked_total = int(snapshot.get("asked_total", 0))
        if snapshot.get("finished_reason"):
            session._finished_reason = str(snapshot["finished_reason"])
        return session

    # ── FR-4.5 summary ──────────────────────────────────────

    def summary(self) -> DiagnosticOutcome:
        """The estimate shown back to the learner, who may correct it (FR-4.5)."""
        return DiagnosticOutcome(
            estimates=[
                {
                    "id": e.id,
                    "title": e.title,
                    "estimate": round(e.estimate, 3),
                    "uncertainty": round(e.uncertainty, 3),
                    "probes": e.answered,
                    "band": describe_band(e.estimate),
                }
                for e in self._estimates
            ],
            turns=list(self.turns),
            finished_reason=self._finished_reason or "in_progress",
            aggregate_uncertainty=self.aggregate_uncertainty(),
            started_at=self.started_at,
            completed_at=datetime.now(timezone.utc) if self.finished else None,
        )


def describe_band(estimate: float) -> str:
    """Plain-language band for FR-4.5 ("here is what I understood about your level")."""
    if estimate < 0.35:
        return "not_familiar"
    if estimate < 0.55:
        return "basic_familiarity"
    if estimate < 0.75:
        return "working_knowledge"
    if estimate < 0.9:
        return "solid"
    return "strong"


def apply_correction(
    subtopics: list[dict],
    corrections: dict[str, float],
) -> list[dict]:
    """FR-4.5: the learner may correct the estimate directly."""
    result = []
    for i, sub in enumerate(subtopics):
        sid = str(sub.get("id", i))
        estimate = corrections.get(sid, INITIAL_ESTIMATE)
        result.append({**sub, "estimate": _clamp(estimate, 0.0, 1.0)})
    return result