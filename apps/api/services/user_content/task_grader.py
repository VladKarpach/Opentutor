"""AI grading of learner answers to their own uploaded tasks.

The grader compares the learner's submitted answer against the task text
extracted from the uploaded file and returns a verdict (correct / partial /
incorrect) plus feedback. When the verdict is not fully correct it also
generates a remedial (similar) task for practicing the missed concept.

All LLM access goes through ``services.llm.router.get_llm_client()``.
"""

from __future__ import annotations

import json
import logging

from services.llm.router import get_llm_client

logger = logging.getLogger(__name__)

_GRADE_SYSTEM = (
    "You are a strict but fair teacher. Grade the learner's answer against the "
    "task below. Output valid JSON only."
)

_GRADE_PROMPT = """Task (from the uploaded file):
{task_content}

Learner's answer:
{user_answer}

Grade the answer and return JSON only:
{{
  "verdict": "correct" | "partial" | "incorrect",
  "score": 0-100,
  "feedback": "short explanation of what is right and what is missing",
  "error_category": "category of the mistake, or null if fully correct"
}}

Rules:
- "correct" = the answer fully solves the task (score >= 90).
- "partial" = the answer shows understanding but is incomplete or has mistakes (score 40-89).
- "incorrect" = the answer is wrong or missing the core point (score 0-39).
- Be lenient on formatting; judge substance.
"""

_REMEDIAL_SYSTEM = (
    "You design remedial practice tasks. Output valid JSON only."
)

_REMEDIAL_PROMPT = """The learner got this task wrong or only partly right.

Original task:
{task_content}

Learner's answer:
{user_answer}

Grading feedback:
{feedback}

Error category: {error_category}

Generate a SIMPLER remedial task that:
1. Tests the EXACT SAME core concept that was missed
2. Removes traps, distractors, and unnecessary complexity
3. Uses simpler numbers or a clearer framing
4. Is solvable in a short answer

Return JSON only:
{{
  "question": "...",
  "correct_answer": "...",
  "explanation": "...",
  "core_concept": "name of the concept being retrained"
}}"""


def _extract_json_object(text: str) -> dict:
    """Extract the first balanced JSON object from mixed LLM output."""
    start = text.find("{")
    if start == -1:
        return {"verdict": "incorrect", "score": 0, "feedback": text, "error_category": None}
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    pass
    return {"verdict": "incorrect", "score": 0, "feedback": text, "error_category": None}


async def grade_submission(task_content: str, user_answer: str) -> dict:
    """Grade a learner's answer against a task.

    Returns: {"verdict", "score", "feedback", "error_category"}
    """
    client = get_llm_client()
    user_message = _GRADE_PROMPT.format(
        task_content=task_content[:6000],
        user_answer=user_answer[:4000],
    )
    try:
        content, _usage = await client.chat(_GRADE_SYSTEM, user_message)
    except Exception:
        logger.exception("Task grading LLM call failed")
        return {
            "verdict": "incorrect",
            "score": 0,
            "feedback": "Не удалось автоматически проверить ответ (LLM недоступен).",
            "error_category": None,
        }
    parsed = _extract_json_object(content)
    parsed.setdefault("verdict", "incorrect")
    parsed.setdefault("score", 0)
    parsed.setdefault("feedback", "")
    parsed.setdefault("error_category", None)
    return parsed


async def generate_remedial_task(
    task_content: str,
    user_answer: str,
    feedback: str,
    error_category: str | None,
) -> dict | None:
    """Generate a simpler remedial task for practicing the missed concept.

    Returns None if generation fails.
    """
    client = get_llm_client()
    user_message = _REMEDIAL_PROMPT.format(
        task_content=task_content[:6000],
        user_answer=user_answer[:4000],
        feedback=feedback or "",
        error_category=error_category or "не определена",
    )
    try:
        content, _usage = await client.chat(_REMEDIAL_SYSTEM, user_message)
    except Exception:
        logger.exception("Remedial task generation LLM call failed")
        return None
    parsed = _extract_json_object(content)
    if not parsed.get("question"):
        return None
    return parsed
