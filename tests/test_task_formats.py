"""Reply formats: what can be stored, and what the grader says about an answer.

Two things have to hold for a format to be safe to offer:

* a payload that describes the task must survive normalisation, and a payload that
  does not must be refused rather than repaired into something the grader would
  then judge wrongly;
* the verdict must be right for both a correct and an incorrect answer.

The second matters more than it looks. A task that can be stored but not judged
correctly is worse than one that was never offered, because the learner is shown a
verdict and has no way to tell it was produced by a broken comparison.

No database and no model are involved: every one of these is decided by comparing
values in this module, which is the point of keeping them out of the model.
"""

import pytest

from services.adaptive_learning import answer_scoring as scoring
from services.adaptive_learning import task_formats as tf


# ── The two formats the model is responsible for ──────────────────────────


@pytest.mark.parametrize("fmt", [tf.FORMAT_TEXT, tf.FORMAT_CODE])
def test_the_model_formats_are_not_decided_here(fmt):
    # Returning None rather than a guess is what keeps the model in charge of the
    # two formats where only judgement can decide.
    assert tf.grade(fmt, tf.normalise_payload(fmt, {}), "любой ответ") is None


# ── number ────────────────────────────────────────────────────────────────


@pytest.fixture
def number():
    return tf.normalise_payload(tf.FORMAT_NUMBER, {"expected": 0.3, "unit": "сек"})


def test_number_accepts_the_exact_value(number):
    assert tf.grade(tf.FORMAT_NUMBER, number, "0.3").correct


def test_a_russian_decimal_comma_is_the_same_number(number):
    # 0,3 and 0.3 are the same value typed two ways. Typography must not decide.
    assert tf.grade(tf.FORMAT_NUMBER, number, "0,3").correct


def test_number_rejects_a_value_outside_the_tolerance(number):
    assert not tf.grade(tf.FORMAT_NUMBER, number, "0.31").correct


def test_number_without_a_number_is_incomplete(number):
    assert "incomplete" in tf.grade(tf.FORMAT_NUMBER, number, "не знаю").error_types


def test_number_names_the_difference_it_found(number):
    grade = tf.grade(tf.FORMAT_NUMBER, number, "0.31")
    assert "0.31" in grade.detail
    assert "0.3" in grade.detail


def test_number_shows_the_right_answer_with_its_unit(number):
    assert tf.grade(tf.FORMAT_NUMBER, number, "0.31").expected == "0.3 сек"


def test_a_tolerance_is_invented_when_the_generator_omits_it():
    # Without one, 0.30000000000000004 would be judged wrong against 0.3 — the
    # learner did the arithmetic correctly.
    payload = tf.normalise_payload(tf.FORMAT_NUMBER, {"expected": 1 / 3})
    assert tf.grade(tf.FORMAT_NUMBER, payload, str(1 / 3)).correct


# ── single choice ─────────────────────────────────────────────────────────


@pytest.fixture
def single():
    return tf.normalise_payload(
        tf.FORMAT_SINGLE_CHOICE,
        {"options": ["прямой ход", "обратный ход", "свободный член"], "correct": 0},
    )


def test_single_choice_accepts_the_index(single):
    assert tf.grade(tf.FORMAT_SINGLE_CHOICE, single, [0]).correct


def test_single_choice_accepts_the_option_text(single):
    # The answer endpoint is public; a caller should not have to know that indices
    # are what gets stored.
    assert tf.grade(tf.FORMAT_SINGLE_CHOICE, single, "прямой ход").correct


def test_single_choice_rejects_another_option(single):
    grade = tf.grade(tf.FORMAT_SINGLE_CHOICE, single, [1])
    assert not grade.correct
    assert grade.expected == "прямой ход"


def test_single_choice_with_nothing_chosen_is_incomplete(single):
    assert "incomplete" in tf.grade(tf.FORMAT_SINGLE_CHOICE, single, None).error_types


# ── multi choice ──────────────────────────────────────────────────────────


@pytest.fixture
def multi():
    return tf.normalise_payload(
        tf.FORMAT_MULTI_CHOICE, {"options": ["a", "b", "c", "d"], "correct": [0, 2]}
    )


def test_multi_choice_accepts_exactly_the_right_set(multi):
    assert tf.grade(tf.FORMAT_MULTI_CHOICE, multi, [0, 2]).correct


def test_multi_choice_does_not_care_about_the_order_it_was_marked_in(multi):
    assert tf.grade(tf.FORMAT_MULTI_CHOICE, multi, [2, 0]).correct


def test_multi_choice_rejects_an_extra_option(multi):
    grade = tf.grade(tf.FORMAT_MULTI_CHOICE, multi, [0, 1, 2])
    assert not grade.correct
    assert "лишнее" in grade.detail


def test_multi_choice_rejects_a_missing_option(multi):
    grade = tf.grade(tf.FORMAT_MULTI_CHOICE, multi, [0])
    assert not grade.correct
    assert "не отмечено" in grade.detail


# ── true / false ──────────────────────────────────────────────────────────


def test_true_false_accepts_the_agreeing_answer():
    payload = tf.normalise_payload(tf.FORMAT_TRUE_FALSE, {"correct": True})
    assert tf.grade(tf.FORMAT_TRUE_FALSE, payload, True).correct
    assert tf.grade(tf.FORMAT_TRUE_FALSE, payload, "верно").correct


def test_true_false_rejects_the_other_one():
    payload = tf.normalise_payload(tf.FORMAT_TRUE_FALSE, {"correct": True})
    assert not tf.grade(tf.FORMAT_TRUE_FALSE, payload, "нет").correct


def test_true_false_reads_a_russian_wording_of_the_key():
    assert tf.normalise_payload(tf.FORMAT_TRUE_FALSE, {"correct": "неверно"})["correct"] is False


def test_true_false_with_nothing_chosen_is_incomplete():
    payload = tf.normalise_payload(tf.FORMAT_TRUE_FALSE, {"correct": True})
    assert "incomplete" in tf.grade(tf.FORMAT_TRUE_FALSE, payload, None).error_types


# ── ordering ──────────────────────────────────────────────────────────────


@pytest.fixture
def ordering():
    return tf.normalise_payload(
        tf.FORMAT_ORDERING,
        {
            "items": ["переставить строки", "нормировать", "выбрать ведущий"],
            "solution": [1, 2, 0],
        },
    )


def test_ordering_accepts_the_right_sequence(ordering):
    assert tf.grade(tf.FORMAT_ORDERING, ordering, [1, 2, 0]).correct


def test_ordering_accepts_the_steps_named_in_full(ordering):
    given = ["нормировать", "выбрать ведущий", "переставить строки"]
    assert tf.grade(tf.FORMAT_ORDERING, ordering, given).correct


def test_ordering_reads_a_reference_given_as_text_not_indices():
    # A human-written payload will hold the steps themselves. Both must work.
    payload = tf.normalise_payload(
        tf.FORMAT_ORDERING,
        {
            "items": ["переставить строки", "нормировать"],
            "solution": ["нормировать", "переставить строки"],
        },
    )
    assert payload["solution"] == ["нормировать", "переставить строки"]


def test_ordering_rejects_a_shuffled_sequence(ordering):
    assert not tf.grade(tf.FORMAT_ORDERING, ordering, [0, 1, 2]).correct


def test_ordering_rejects_the_same_step_twice(ordering):
    assert not tf.grade(tf.FORMAT_ORDERING, ordering, [1, 1, 0]).correct


def test_ordering_rejects_an_incomplete_list(ordering):
    assert "incomplete" in tf.grade(tf.FORMAT_ORDERING, ordering, [1, 2]).error_types


def test_ordering_points_at_the_first_step_that_is_wrong(ordering):
    # "Which step did I get wrong" is only useful if the answer names it.
    assert "шага 1" in tf.grade(tf.FORMAT_ORDERING, ordering, [0, 1, 2]).detail


# ── matching ──────────────────────────────────────────────────────────────


@pytest.fixture
def matching():
    return tf.normalise_payload(
        tf.FORMAT_MATCHING,
        {"pairs": [["свободный член", "b"], ["матрица", "A"], ["норма", "‖·‖"]]},
    )


def test_matching_accepts_the_right_pairs(matching):
    pairs = {"свободный член": "b", "матрица": "A", "норма": "‖·‖"}
    assert tf.grade(tf.FORMAT_MATCHING, matching, pairs).correct


def test_matching_accepts_pairs_as_a_list(matching):
    pairs = [["свободный член", "b"], ["матрица", "A"], ["норма", "‖·‖"]]
    assert tf.grade(tf.FORMAT_MATCHING, matching, pairs).correct


def test_matching_names_the_pairs_that_are_wrong(matching):
    pairs = {"свободный член": "b", "матрица": "b", "норма": "‖·‖"}
    grade = tf.grade(tf.FORMAT_MATCHING, matching, pairs)
    assert not grade.correct
    assert "матрица" in grade.detail


def test_matching_with_nothing_connected_is_wrong(matching):
    assert not tf.grade(tf.FORMAT_MATCHING, matching, {}).correct


def test_a_matching_task_cannot_be_answered_by_counting_rows(matching):
    # If the right column lined up with the left, the task would test counting.
    assert matching["right"] != matching["left"]


def test_a_repeated_right_hand_value_is_refused():
    # A value appearing twice on the right makes the pairing ambiguous rather than
    # harder, so the task is not offered at all.
    with pytest.raises(tf.FormatError):
        tf.normalise_payload(
            tf.FORMAT_MATCHING, {"pairs": [["a", "same"], ["b", "same"]]}
        )


# ── code payloads ─────────────────────────────────────────────────────────


def test_a_code_payload_keeps_starter_tests_and_solution():
    payload = tf.normalise_payload(
        tf.FORMAT_CODE,
        {
            "starter_code": "def f(x):\n    pass\n",
            "tests": [{"name": "ноль", "call": "f(0)", "expected": "0"}],
            "solution": "def f(x):\n    return 0\n",
        },
    )
    assert payload["starter_code"].startswith("def f")
    assert len(payload["tests"]) == 1
    assert "return 0" in payload["solution"]


def test_a_test_without_a_call_is_dropped():
    # A test that cannot be invoked would show the learner a check that never runs.
    payload = tf.normalise_payload(
        tf.FORMAT_CODE, {"tests": [{"name": "без вызова", "expected": "1"}]}
    )
    assert payload["tests"] == []


# ── payloads that cannot describe the task ────────────────────────────────


@pytest.mark.parametrize(
    "fmt,payload,why",
    [
        (tf.FORMAT_SINGLE_CHOICE, {"options": ["a"], "correct": 0}, "one option"),
        (tf.FORMAT_SINGLE_CHOICE, {"options": ["a", "b"], "correct": 7}, "index outside"),
        (tf.FORMAT_SINGLE_CHOICE, {"options": ["a", "b"]}, "no correct option"),
        (tf.FORMAT_MULTI_CHOICE, {"options": ["a", "b"], "correct": []}, "nothing correct"),
        (tf.FORMAT_NUMBER, {"expected": "не число"}, "value is not a number"),
        (tf.FORMAT_NUMBER, {}, "no expected value"),
        (tf.FORMAT_TRUE_FALSE, {"correct": "может быть"}, "not a verdict"),
        (tf.FORMAT_TRUE_FALSE, {}, "no verdict"),
        (tf.FORMAT_ORDERING, {"items": ["один"]}, "one step"),
        (tf.FORMAT_ORDERING, {}, "no steps"),
        (tf.FORMAT_MATCHING, {"pairs": [["a", "b"]]}, "one pair"),
    ],
)
def test_a_payload_that_cannot_describe_the_task_is_refused(fmt, payload, why):
    with pytest.raises(tf.FormatError):
        tf.normalise_payload(fmt, payload)


def test_an_unknown_format_is_refused():
    with pytest.raises(tf.FormatError):
        tf.normalise_payload("не_существует", {})


def test_an_unusable_payload_degrades_to_text():
    # A stored task must never be unanswerable because its payload is odd. Text
    # always grades: the model reads the task and decides.
    fmt, payload = tf.fallback_format(tf.FORMAT_SINGLE_CHOICE, {"options": []})
    assert fmt == tf.FORMAT_TEXT
    assert payload == {}


def test_text_passes_through_the_fallback_unchanged():
    fmt, payload = tf.fallback_format(tf.FORMAT_TEXT, {"что угодно": 1})
    assert fmt == tf.FORMAT_TEXT
    assert payload == {}


# ── how the learner's confidence is weighed ───────────────────────────────


ALL_CORRECT = {"correctness": 1.0, "completeness": 1.0, "understanding": 1.0}


def test_a_confident_right_answer_is_taken_at_face_value():
    result = scoring.score_answer(ALL_CORRECT, confidence="confident")
    assert result.final_score == 1.0
    assert result.mastery_score == 1.0


def test_a_guessed_right_answer_still_shows_as_a_full_answer():
    # What the learner achieved is not discounted back at them. Only what the
    # memory system learns from it is.
    assert scoring.score_answer(ALL_CORRECT, confidence="guessing").final_score == 1.0


def test_a_guessed_right_answer_is_weaker_evidence():
    assert scoring.score_answer(ALL_CORRECT, confidence="guessing").mastery_score == 0.7


def test_a_guess_never_counts_as_a_failure():
    # Below this the topic would be sent back for a one-day retry. A learner who
    # guessed right has not failed anything.
    assert scoring.score_answer(ALL_CORRECT, confidence="guessing").mastery_score > 0.5


def test_uncertainty_sits_between_certainty_and_a_guess():
    confident = scoring.score_answer(ALL_CORRECT, confidence="confident").mastery_score
    unsure = scoring.score_answer(ALL_CORRECT, confidence="unsure").mastery_score
    guessing = scoring.score_answer(ALL_CORRECT, confidence="guessing").mastery_score
    assert confident > unsure > guessing


def test_not_declaring_a_confidence_costs_nothing():
    # Forcing the declaration would punish a learner for skipping one control.
    assert scoring.score_answer(ALL_CORRECT, confidence=None).mastery_score == 1.0


def test_an_unrecognised_confidence_is_kept_verbatim():
    # A typo in the client's vocabulary should be visible in the stored data
    # rather than quietly becoming full credit.
    label, weight = scoring.confidence_factor("опечатка")
    assert label == "опечатка"
    assert weight == 1.0


def test_an_unrecognised_confidence_is_reported():
    warnings = scoring.score_answer(ALL_CORRECT, confidence="опечатка").warnings
    assert any("confidence" in warning for warning in warnings)


def test_a_confidence_value_is_matched_regardless_of_case():
    assert scoring.confidence_factor("Уверенно") == ("уверенно", 1.0)