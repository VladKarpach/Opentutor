"""What the learner is asked to produce, and how an answer to it is checked.

Two ideas are kept apart on purpose, because they answer different questions and
conflating them is what makes task generators drift:

* ``question_type`` is the *pedagogical intent* — find the error, compare two
  approaches, apply it to a situation. That has always existed and does not change
  with this module.
* ``format`` is *how the learner replies* — a couple of sentences, a piece of
  code, a number, a choice from a list.

A "find the error" task can be plain text or code. A "write the function" task is
code whichever way it is phrased. Keeping them apart means the generator can vary
the reply format without touching the pedagogy, and the grader can be chosen by
format alone.

Most formats are decided here, in Python, from the stored payload — not by a
model. A multiple-choice answer is either one of the offered options or it is not,
and asking a model whether it is would only add a way to be wrong, cost, and
variance between repeats. The two formats that genuinely need judgement stay with
the model: free text (``text``) and code (``code``).

The correctness rule per format is intentionally forgiving in one direction only:
whitespace, letter case and surrounding punctuation never decide a wrong answer to
be right, but they never decide a right answer to be wrong either.
"""

from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Formats ───────────────────────────────────────────────────────────────

FORMAT_TEXT = "text"
FORMAT_CODE = "code"
FORMAT_NUMBER = "number"
FORMAT_SINGLE_CHOICE = "single_choice"
FORMAT_MULTI_CHOICE = "multi_choice"
FORMAT_TRUE_FALSE = "true_false"
FORMAT_ORDERING = "ordering"
FORMAT_MATCHING = "matching"

FORMATS = (
    FORMAT_TEXT,
    FORMAT_CODE,
    FORMAT_NUMBER,
    FORMAT_SINGLE_CHOICE,
    FORMAT_MULTI_CHOICE,
    FORMAT_TRUE_FALSE,
    FORMAT_ORDERING,
    FORMAT_MATCHING,
)

DEFAULT_FORMAT = FORMAT_TEXT

#: Formats the model is asked to judge rather than this module. Everything else is
#: decided by comparing values, which is both faster and reproducible.
JUDGED_FORMATS = frozenset({FORMAT_TEXT, FORMAT_CODE})

#: Formats the learner answers by choosing, not by writing. They get the same
#: controls in every position they appear in.
CHOICE_FORMATS = frozenset({FORMAT_SINGLE_CHOICE, FORMAT_MULTI_CHOICE, FORMAT_TRUE_FALSE})

# Column widths below are deliberate: `question_type` is String(30) and
# `practice_problems.question_type` is String(20), so format names must stay short
# enough for both to remain a possible future home.
_MAX_CHOICES = 8
_MAX_ORDER_ITEMS = 10
_MAX_MATCH_PAIRS = 8
_MAX_CODE_CHARS = 20000

_NUMERIC_RE = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?")


class FormatError(ValueError):
    """The stored payload does not describe a task of the claimed format."""


@dataclass
class FormatGrade:
    """Outcome of checking one answer against a stored payload.

    ``expected`` is what a wrong answer should be compared against. It is returned
    even when the learner has not earned it: the hint system and the review screen
    both need to show the right answer, and withholding it would make a wrong
    answer unfixable without spending a hint.
    """

    correct: bool
    score: float
    detail: str = ""
    expected: str | None = None
    error_types: list[str] = field(default_factory=list)


# ── Normalisation ─────────────────────────────────────────────────────────


def _clean(text: object) -> str:
    return (text or "").strip() if isinstance(text, str) else ""


def _option_key(index: int) -> str:
    return chr(ord("a") + index)


def _normalise_choice_list(raw: object, *, label: str) -> list[str]:
    if isinstance(raw, dict):
        values = [raw[key] for key in sorted(raw)]
    elif isinstance(raw, list):
        values = raw
    else:
        raise FormatError(f"{label}: ожидался список вариантов")
    options = [_clean(value) for value in values]
    if len(options) < 2:
        raise FormatError(f"{label}: нужно минимум два варианта")
    if len(options) > _MAX_CHOICES:
        options = options[:_MAX_CHOICES]
    if any(not option for option in options):
        raise FormatError(f"{label}: пустой вариант ответа")
    return options


def normalise_payload(fmt: str, payload: object) -> dict:
    """Return a payload that is guaranteed to describe a task of ``fmt``.

    Raises ``FormatError`` when it cannot. A task whose payload does not survive
    this is not repaired into something the grader would then judge wrongly — the
    caller falls back to plain text, where a model reads the task and decides.
    """
    data = payload if isinstance(payload, dict) else {}

    if fmt == FORMAT_TEXT:
        return {}

    if fmt == FORMAT_CODE:
        return _normalise_code(data)

    if fmt == FORMAT_NUMBER:
        return _normalise_number(data)

    if fmt == FORMAT_SINGLE_CHOICE:
        return _normalise_single(data)

    if fmt == FORMAT_MULTI_CHOICE:
        return _normalise_multi(data)

    if fmt == FORMAT_TRUE_FALSE:
        return _normalise_true_false(data)

    if fmt == FORMAT_ORDERING:
        return _normalise_ordering(data)

    if fmt == FORMAT_MATCHING:
        return _normalise_matching(data)

    raise FormatError(f"неизвестный формат: {fmt!r}")


def _normalise_code(data: dict) -> dict:
    language = _clean(data.get("language")) or "python"
    starter = data.get("starter_code")
    starter = starter if isinstance(starter, str) else ""
    starter = starter[:_MAX_CODE_CHARS]
    tests = data.get("tests")
    clean_tests: list[dict] = []
    if isinstance(tests, list):
        for item in tests[:12]:
            if not isinstance(item, dict):
                continue
            name = _clean(item.get("name")) or f"проверка {len(clean_tests) + 1}"
            call = _clean(item.get("call"))
            expected = item.get("expected")
            if expected is None:
                expected_text = ""
            elif isinstance(expected, str):
                expected_text = expected
            else:
                expected_text = str(expected)
            if not call:
                continue
            clean_tests.append(
                {"name": name[:120], "call": call[:400], "expected": expected_text[:400]}
            )
    solution = data.get("solution")
    return {
        "language": language[:20],
        "starter_code": starter,
        "tests": clean_tests,
        "solution": solution[:_MAX_CODE_CHARS] if isinstance(solution, str) else "",
    }


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        found = _NUMERIC_RE.search(value)
        if found:
            try:
                return float(found.group(0).replace(",", "."))
            except ValueError:
                return None
    return None


def _normalise_number(data: dict) -> dict:
    expected = _as_float(data.get("expected"))
    if expected is None:
        raise FormatError("number: не задано ожидаемое значение")
    tolerance = _as_float(data.get("tolerance"))
    if tolerance is None:
        # Without an explicit tolerance a plain equality test would fail a correct
        # answer like 0.30000000000000004 instead of 0.3. A relative fallback keeps
        # large answers from demanding absurd precision.
        tolerance = max(abs(expected) * 1e-6, 1e-9)
    if tolerance <= 0:
        tolerance = max(abs(expected) * 1e-6, 1e-9)
    unit = _clean(data.get("unit"))
    return {
        "expected": expected,
        "tolerance": tolerance,
        "unit": unit[:30],
        "hint_text": _clean(data.get("hint_text"))[:200],
    }


def _normalise_single(data: dict) -> dict:
    options = _normalise_choice_list(data.get("options"), label="single_choice")
    correct = data.get("correct")
    if isinstance(correct, int) and not isinstance(correct, bool):
        index = correct
    else:
        text = _clean(correct).lower()
        index = next(
            (i for i, option in enumerate(options) if option.lower() == text), -1
        )
        if index < 0 and text in {str(i) for i in range(len(options))}:
            index = int(text)
    if not 0 <= index < len(options):
        raise FormatError("single_choice: верный вариант не найден среди вариантов")
    return {
        "options": options,
        "correct_index": index,
        "explanation": _clean(data.get("explanation"))[:1000],
    }


def _normalise_multi(data: dict) -> dict:
    options = _normalise_choice_list(data.get("options"), label="multi_choice")
    raw = data.get("correct") or data.get("correct_indices") or []
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = [raw]
    indices: list[int] = []
    for value in raw if isinstance(raw, list) else []:
        index = value if isinstance(value, int) and not isinstance(value, bool) else None
        if index is None:
            text = _clean(value).lower()
            index = next(
                (i for i, option in enumerate(options) if option.lower() == text), None
            )
            if index is None and text in {str(i) for i in range(len(options))}:
                index = int(text)
        if isinstance(index, int) and 0 <= index < len(options) and index not in indices:
            indices.append(index)
    if not indices:
        raise FormatError("multi_choice: не отмечено ни одного верного варианта")
    return {
        "options": options,
        "correct_indices": sorted(indices),
        "explanation": _clean(data.get("explanation"))[:1000],
    }


def _normalise_true_false(data: dict) -> dict:
    raw = data.get("correct")
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"true", "да", "верно", "истина", "1"}:
            truth = True
        elif value in {"false", "нет", "неверно", "ложь", "0"}:
            truth = False
        else:
            raise FormatError("true_false: не удалось разобрать верный ответ")
    elif isinstance(raw, bool):
        truth = raw
    else:
        raise FormatError("true_false: не задан верный ответ")
    return {
        "statement": _clean(data.get("statement"))[:1000],
        "correct": truth,
        "explanation": _clean(data.get("explanation"))[:1000],
    }


def _normalise_ordering(data: dict) -> dict:
    items = data.get("items")
    if not isinstance(items, list):
        raise FormatError("ordering: не задан список шагов")
    steps = [_clean(item) for item in items]
    steps = [step for step in steps if step]
    if len(steps) < 2:
        raise FormatError("ordering: нужно минимум два шага")
    steps = steps[:_MAX_ORDER_ITEMS]

    # The reference order may arrive as indices or as the step texts themselves.
    # Indices are what a model writes reliably — reproducing a list of sentences
    # verbatim invites a typo that would silently make the answer unmatchable —
    # and the texts are what a human or an older payload may hold. Both are
    # accepted and stored as the step texts, because that is what the grader
    # compares against.
    raw = data.get("solution")
    solution: list[str] = []
    if isinstance(raw, list) and raw:
        if all(isinstance(x, int) and not isinstance(x, bool) for x in raw):
            for value in raw:
                if 0 <= value < len(steps):
                    solution.append(steps[value])
        else:
            wanted = [_clean(x).casefold() for x in raw]
            # Match positionally on case-insensitive text; a near miss is dropped
            # rather than guessed at.
            for value in wanted:
                found = next((s for s in steps if s.casefold() == value), "")
                if found:
                    solution.append(found)

    # An incomplete or unusable reference falls back to the given order, which is
    # the only order the task can plausibly have meant.
    if len(solution) != len(steps) or len(set(solution)) != len(solution):
        solution = list(steps)

    return {
        "items": steps,
        "solution": solution,
        "explanation": _clean(data.get("explanation"))[:1000],
    }


def _normalise_matching(data: dict) -> dict:
    pairs = data.get("pairs")
    if not isinstance(pairs, list):
        raise FormatError("matching: не заданы пары")
    left: list[str] = []
    right: list[str] = []
    for pair in pairs[:_MAX_MATCH_PAIRS]:
        if isinstance(pair, dict):
            a, b = _clean(pair.get("left")), _clean(pair.get("right"))
        elif isinstance(pair, (list, tuple)) and len(pair) == 2:
            a, b = _clean(pair[0]), _clean(pair[1])
        else:
            continue
        if a and b:
            left.append(a[:200])
            right.append(b[:200])
    if len(left) < 2:
        raise FormatError("matching: нужно минимум две пары")
    if len(set(right)) != len(right):
        # A repeated right-hand value makes the pairing ambiguous, not harder.
        raise FormatError("matching: правая колонка содержит повтор")

    # The right column is stored rotated away from the left. A matching task whose
    # answers line up can be completed by counting rows, which is not the skill it
    # is meant to test. The offset comes from the content rather than from a random
    # number so that re-normalising the same payload always yields the same task.
    offset = zlib.crc32("|".join(left).encode("utf-8")) % len(right)
    if offset == 0:
        offset = 1

    return {
        "left": left,
        "right": right[offset:] + right[:offset],
        # The pairing is kept against the original right-hand values, so grading
        # still works after the display rotation.
        "solution": dict(zip(left, right)),
        "explanation": _clean(data.get("explanation"))[:1000],
    }


# ── Grading ───────────────────────────────────────────────────────────────


def _equal_text(left: str, right: str) -> bool:
    """Compare two short answers without letting typography decide the result."""
    def fold(value: str) -> str:
        value = value.strip().strip("\"'«»")
        value = value.replace(",", ".")  # Russian decimal comma
        return re.sub(r"\s+", " ", value).casefold()

    return fold(left) == fold(right) and bool(fold(left))


def grade(fmt: str, payload: object, answer: object) -> FormatGrade | None:
    """Check one answer. ``None`` means "this format needs the model".

    The caller must have normalised the payload first. Returning ``None`` rather
    than a guess keeps the model in charge of the two formats where its judgement
    is the only source of truth.
    """
    if fmt in JUDGED_FORMATS:
        return None

    data = payload if isinstance(payload, dict) else {}

    if fmt == FORMAT_NUMBER:
        return _grade_number(data, answer)
    if fmt == FORMAT_SINGLE_CHOICE:
        return _grade_single(data, answer)
    if fmt == FORMAT_MULTI_CHOICE:
        return _grade_multi(data, answer)
    if fmt == FORMAT_TRUE_FALSE:
        return _grade_true_false(data, answer)
    if fmt == FORMAT_ORDERING:
        return _grade_ordering(data, answer)
    if fmt == FORMAT_MATCHING:
        return _grade_matching(data, answer)

    raise FormatError(f"неизвестный формат: {fmt!r}")


def _as_index_list(answer: object, count: int) -> list[int]:
    if isinstance(answer, (int, float)) and not isinstance(answer, bool):
        value = int(answer)
        return [value] if 0 <= value < count else []
    if isinstance(answer, str):
        found = _NUMERIC_RE.search(answer)
        if found:
            value = int(float(found.group(0)))
            return [value] if 0 <= value < count else []
    if isinstance(answer, (list, tuple)):
        return [int(x) for x in answer if isinstance(x, int) and 0 <= x < count]
    return []


def _grade_number(data: dict, answer: object) -> FormatGrade:
    expected = float(data.get("expected", 0.0))
    tolerance = float(data.get("tolerance") or 0.0)
    unit = _clean(data.get("unit"))
    wanted = f"{expected:g}{(' ' + unit) if unit else ''}"

    given = _as_float(answer)
    if given is None:
        return FormatGrade(
            False,
            0.0,
            "В ответе не найдено число.",
            wanted,
            ["incomplete"],
        )

    difference = abs(given - expected)
    # Tolerance is treated as relative above 1 so that an answer in the hundreds
    # is not held to more decimals than the learner could reasonably type.
    allowance = tolerance if abs(expected) <= 1 else max(tolerance, abs(expected) * 1e-6)
    if difference <= allowance:
        return FormatGrade(
            True,
            1.0,
            f"Верно: {given:g}.",
            wanted,
        )
    return FormatGrade(
        False,
        0.0,
        f"Получилось {given:g}, а нужно {wanted} — расхождение {difference:g}.",
        wanted,
        ["computational" if data.get("unit") else "wrong_application"],
    )


def _grade_single(data: dict, answer: object) -> FormatGrade:
    options: list[str] = list(data.get("options") or [])
    correct_index = int(data.get("correct_index", 0))
    wanted = options[correct_index] if 0 <= correct_index < len(options) else ""

    chosen = _as_index_list(answer, len(options))
    if not chosen:
        # Accept the option's own text too: the API is public and a caller should
        # not have to know that indices are what is stored.
        text = _clean(answer).casefold()
        chosen = [i for i, option in enumerate(options) if option.casefold() == text]
    if not chosen:
        return FormatGrade(False, 0.0, "Вариант не выбран.", wanted, ["incomplete"])

    picked = chosen[0]
    if picked == correct_index:
        return FormatGrade(True, 1.0, "Верно.", wanted)
    return FormatGrade(
        False,
        0.0,
        f"Неверно. {_clean(options[picked])} — не правильный вариант.",
        wanted,
        ["concept_confusion"],
    )


def _grade_multi(data: dict, answer: object) -> FormatGrade:
    options: list[str] = list(data.get("options") or [])
    correct = set(int(i) for i in (data.get("correct_indices") or []))
    wanted = ", ".join(options[i] for i in sorted(correct) if i < len(options))

    chosen = set(_as_index_list(answer, len(options)))
    if not chosen:
        text = answer if isinstance(answer, list) else [_clean(answer)]
        folded = {str(item).casefold() for item in text}
        chosen = {i for i, option in enumerate(options) if option.casefold() in folded}
    if not chosen:
        return FormatGrade(False, 0.0, "Ничего не отмечено.", wanted, ["incomplete"])

    extra = chosen - correct
    missed = correct - chosen
    if not extra and not missed:
        return FormatGrade(True, 1.0, "Верно: отмечены все и только верные варианты.", wanted)

    parts = []
    if missed:
        parts.append("не отмечено: " + ", ".join(options[i] for i in sorted(missed)))
    if extra:
        parts.append(
            "отмечено лишнее: " + ", ".join(options[i] for i in sorted(extra))
        )
    return FormatGrade(
        False, 0.0, "Неверно — " + "; ".join(parts) + ".", wanted, ["incomplete"]
    )


def _grade_true_false(data: dict, answer: object) -> FormatGrade:
    truth = bool(data.get("correct"))
    wanted = "верно" if truth else "неверно"

    value: bool | None = None
    if isinstance(answer, bool):
        value = answer
    elif isinstance(answer, str):
        folded = answer.strip().casefold()
        if folded in {"true", "да", "верно", "истина"}:
            value = True
        elif folded in {"false", "нет", "неверно", "ложь"}:
            value = False
    if value is None:
        return FormatGrade(False, 0.0, "Нужен ответ «верно» или «неверно».", wanted, ["incomplete"])
    if value == truth:
        return FormatGrade(True, 1.0, "Верно.", wanted)
    return FormatGrade(False, 0.0, "Неверно.", wanted, ["concept_confusion"])


def _grade_ordering(data: dict, answer: object) -> FormatGrade:
    items: list[str] = list(data.get("items") or [])
    solution: list[str] = list(data.get("solution") or items)
    wanted = " → ".join(solution)

    if not isinstance(answer, (list, tuple)) or len(answer) != len(items):
        return FormatGrade(
            False, 0.0, "Нужно расставить все шаги.", wanted, ["incomplete"]
        )

    given: list[str] = []
    for value in answer:
        index = value if isinstance(value, int) and not isinstance(value, bool) else None
        if index is None:
            folded = _clean(value).casefold()
            index = next(
                (i for i, item in enumerate(items) if item.casefold() == folded), None
            )
        if index is None or not 0 <= index < len(items):
            return FormatGrade(
                False, 0.0, "В списке есть шаг, которого нет в задании.", wanted, ["incomplete"]
            )
        given.append(items[index])

    if len(set(given)) != len(given):
        return FormatGrade(False, 0.0, "Один и тот же шаг повторяется.", wanted, ["incomplete"])

    if given == solution:
        return FormatGrade(True, 1.0, "Верно: порядок правильный.", wanted)

    first_wrong = next(
        (i for i, (a, b) in enumerate(zip(given, solution)) if a != b), 0
    )
    return FormatGrade(
        False,
        0.0,
        f"Порядок нарушен начиная с шага {first_wrong + 1}.",
        wanted,
        ["missing_step"],
    )


def _grade_matching(data: dict, answer: object) -> FormatGrade:
    solution: dict[str, str] = dict(data.get("solution") or {})
    left: list[str] = list(data.get("left") or [])
    if not solution:
        return FormatGrade(False, 0.0, "Нет эталонных пар.", "", ["incomplete"])

    mapping: dict[str, str] = {}
    if isinstance(answer, dict):
        for key, value in answer.items():
            k, v = _clean(key), _clean(value)
            if k and v:
                mapping[k] = v
    elif isinstance(answer, (list, tuple)):
        for pair in answer:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                k, v = _clean(pair[0]), _clean(pair[1])
                if k and v:
                    mapping[k] = v

    if not mapping:
        return FormatGrade(
            False,
            0.0,
            "Ни одной пары не соединено.",
            "; ".join(f"{a} — {b}" for a, b in solution.items()),
            ["incomplete"],
        )

    right = [solution.get(item, "") for item in left]
    wrong = [a for a in left if mapping.get(a) != solution.get(a)]
    if not wrong:
        return FormatGrade(
            True,
            1.0,
            "Верно: все пары соединены правильно.",
            "; ".join(f"{a} — {b}" for a, b in solution.items()),
        )
    return FormatGrade(
        False,
        0.0,
        "Неверные пары: " + ", ".join(wrong[:4]) + ".",
        "; ".join(f"{a} — {b}" for a, b in solution.items()),
        ["concept_confusion"],
    )


def fallback_format(fmt: str, payload: object) -> tuple[str, dict]:
    """Return a format/payload pair that will actually grade, degrading to text.

    A stored task must never be unanswerable because its payload is odd. Plain
    text always grades: the model reads the task and decides.
    """
    if fmt == DEFAULT_FORMAT:
        return DEFAULT_FORMAT, {}
    try:
        return fmt, normalise_payload(fmt, payload)
    except FormatError as exc:
        logger.info("Payload for format %r unusable (%s); falling back to text", fmt, exc)
        return DEFAULT_FORMAT, {}