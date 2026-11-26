"""Adaptive learning engine — implements TZ sections 6.1-6.7.

Layers, following TZ section 7 ("planning module: deterministic scheduling,
repetition and graph algorithms"):

- ``repetition``  — intervals and strength decay (section 6.3)
- ``scheduling``  — plan construction, topological order, speed (sections 6.4-6.5)
- ``diagnostic``  — hidden level probing, uncertainty tracking (section 6.1)
- ``evaluation``  — answer grading and error typing (sections 6.2, 6.6)
- ``forecast``   — readiness prediction (section 6.7)

Everything here is deterministic. LLM calls live in the agent layer above.
"""

from services.adaptive_learning.repetition import (  # noqa: F401
    BASE_INTERVALS,
    ReviewDecision,
    TopicReviewState,
    apply_answer,
    current_strength,
    decay_strength,
    due_topics,
    next_interval_days,
    weakest_topics,
)
from services.adaptive_learning.diagnostic import (  # noqa: F401
    MAX_REPLIES,
    MIN_REPLIES,
    DiagnosticOutcome,
    DiagnosticSession,
    DiagnosticTurn,
    SubtopicEstimate,
    apply_correction,
    describe_band,
)
from services.adaptive_learning.forecast import (  # noqa: F401
    ForecastInput,
    ForecastResult,
    forecast_readiness,
    plan_review_day,
    required_speed,
)
from services.adaptive_learning.answer_scoring import (  # noqa: F401
    CRITERIA,
    ERROR_TYPES,
    GradeResult,
    hint_penalty,
    is_stable,
    score_answer,
    summarise_errors,
)
from services.adaptive_learning.scheduling import (  # noqa: F401
    ITEM_EXAM,
    ITEM_PRACTICE,
    ITEM_REVIEW,
    ITEM_STUDY,
    PlanItem,
    PlanResult,
    TopicInput,
    build_plan,
    check_feasibility,
    find_cycle,
    soft_return_plan,
    speed_coefficient,
    topological_order,
)

__all__ = [
    "BASE_INTERVALS",
    "ReviewDecision",
    "TopicReviewState",
    "apply_answer",
    "current_strength",
    "decay_strength",
    "due_topics",
    "next_interval_days",
    "weakest_topics",
    "MAX_REPLIES",
    "MIN_REPLIES",
    "DiagnosticOutcome",
    "DiagnosticSession",
    "DiagnosticTurn",
    "SubtopicEstimate",
    "apply_correction",
    "describe_band",
    "CRITERIA",
    "ERROR_TYPES",
    "GradeResult",
    "hint_penalty",
    "is_stable",
    "score_answer",
    "summarise_errors",
    "ForecastInput",
    "ForecastResult",
    "forecast_readiness",
    "plan_review_day",
    "required_speed",
    "ITEM_EXAM",
    "ITEM_PRACTICE",
    "ITEM_REVIEW",
    "ITEM_STUDY",
    "PlanItem",
    "PlanResult",
    "TopicInput",
    "build_plan",
    "check_feasibility",
    "find_cycle",
    "soft_return_plan",
    "speed_coefficient",
    "topological_order",
]