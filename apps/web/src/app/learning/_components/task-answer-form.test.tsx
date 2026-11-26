import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent } from "@testing-library/react";
import { render, screen } from "@/test-utils";

import { TaskAnswerForm } from "./task-answer-form";

/**
 * The control that matches the task.
 *
 * Every format has to do two things: send an answer the grader can compare, and
 * refuse to send one before the learner has actually answered. The second is what
 * these mostly check — a submit button that is live on an empty task would let a
 * blank answer reach the server and be recorded as an attempt.
 */

const onAnswer = vi.fn();

// The form reads its labels through the locale context, and these tests are about
// the shape of the answer rather than about wording. Returning the key keeps a
// Russian label from silently becoming the thing under test.
vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => key,
}));

beforeEach(() => {
  vi.clearAllMocks();
});

function renderForm(format: Parameters<typeof TaskAnswerForm>[0]["format"], payload = {}) {
  return render(
    <TaskAnswerForm
      format={format}
      payload={payload}
      submitLabel="Отправить"
      onAnswer={onAnswer}
    />,
  );
}

describe("TaskAnswerForm", () => {
  it("asks for prose by default and sends it as text", () => {
    renderForm("text");
    fireEvent.change(screen.getByTestId("answer-text"), {
      target: { value: "Прямой ход приводит матрицу к ступенчатому виду" },
    });
    fireEvent.click(screen.getByText("Отправить"));
    expect(onAnswer).toHaveBeenCalledWith({
      text: "Прямой ход приводит матрицу к ступенчатому виду",
      value: null,
    });
  });

  it("will not send an empty prose answer", () => {
    renderForm("text");
    expect(screen.getByText("Отправить").closest("button")).toBeDisabled();
  });

  it("sends a number as a value the grader can compare", () => {
    renderForm("number", { unit: "сек", tolerance: 0.01 });
    fireEvent.change(screen.getByTestId("answer-number"), { target: { value: "2,5" } });
    fireEvent.click(screen.getByText("Отправить"));
    expect(onAnswer).toHaveBeenCalledWith({ text: "2,5", value: "2,5" });
  });

  it("shows the tolerance so the learner knows how precise to be", () => {
    // Rounding is right or wrong depending on a number they cannot see, so a
    // tolerance is shown rather than assumed. The wording comes from the locale
    // mock, so what is checked is that the note exists at all.
    const { container } = renderForm("number", { tolerance: 0.01 });
    expect(container.querySelector("p")?.textContent).toBeTruthy();
  });

  it("shows no tolerance note when the task declares none", () => {
    const { container } = renderForm("number", { tolerance: null });
    expect(container.querySelector("p")).toBeNull();
  });

  it("sends the chosen option as its index", () => {
    renderForm("single_choice", { options: ["прямой ход", "обратный ход"] });
    fireEvent.click(screen.getByTestId("answer-option-1"));
    fireEvent.click(screen.getByText("Отправить"));
    expect(onAnswer).toHaveBeenCalledWith({ text: "обратный ход", value: [1] });
  });

  it("will not send a single choice that was never made", () => {
    renderForm("single_choice", { options: ["a", "b"] });
    expect(screen.getByText("Отправить").closest("button")).toBeDisabled();
  });

  it("sends several options in a stable order", () => {
    renderForm("multi_choice", { options: ["a", "b", "c"] });
    fireEvent.click(screen.getByTestId("answer-option-2"));
    fireEvent.click(screen.getByTestId("answer-option-0"));
    fireEvent.click(screen.getByText("Отправить"));
    // Marked 2 then 0; sent 0 then 2, so a learner who ticks in any order produces
    // the same answer.
    expect(onAnswer).toHaveBeenCalledWith({ text: "a; c", value: [0, 2] });
  });

  it("sends true or false as a boolean", () => {
    renderForm("true_false", {});
    fireEvent.click(screen.getByTestId("answer-tf-true"));
    fireEvent.click(screen.getByText("Отправить"));
    // The label text comes from the locale mock; the value is the thing that has
    // to be a real boolean, because that is what the grader compares.
    expect(onAnswer.mock.calls[0][0].value).toBe(true);
  });

  it("reorders the steps and sends the resulting order", () => {
    renderForm("ordering", { items: ["переставить", "нормировать", "выбрать"] });
    fireEvent.click(screen.getAllByTestId("order-move-down")[0]);
    fireEvent.click(screen.getByText("Отправить"));
    expect(onAnswer.mock.calls[0][0].value).toEqual([1, 0, 2]);
  });

  it("sends the pairs that were joined", () => {
    renderForm("matching", {
      left: ["свободный член", "матрица"],
      right: ["b", "A"],
    });
    fireEvent.change(screen.getByTestId("answer-match-0"), { target: { value: "b" } });
    fireEvent.change(screen.getByTestId("answer-match-1"), { target: { value: "A" } });
    fireEvent.click(screen.getByText("Отправить"));
    expect(onAnswer).toHaveBeenCalledWith({
      text: "свободный член — b; матрица — A",
      value: { "свободный член": "b", матрица: "A" },
    });
  });

  it("will not send a matching with pairs left unjoined", () => {
    renderForm("matching", { left: ["a", "b"], right: ["1", "2"] });
    fireEvent.change(screen.getByTestId("answer-match-0"), { target: { value: "1" } });
    expect(screen.getByText("Отправить").closest("button")).toBeDisabled();
  });

  it("offers a code field with the starter code already in it", () => {
    renderForm("code", { starter_code: "def f(x):\n    pass\n", tests: [] });
    const field = screen.getByTestId("answer-code") as HTMLTextAreaElement;
    expect(field.value).toContain("def f(x):");
  });

  it("sends the code as the text to review", () => {
    renderForm("code", { starter_code: "", tests: [] });
    fireEvent.change(screen.getByTestId("answer-code"), {
      target: { value: "def f(x):\n    return 0" },
    });
    fireEvent.click(screen.getByText("Отправить"));
    const sent = onAnswer.mock.calls[0][0];
    expect(sent.text).toBe("def f(x):\n    return 0");
    // No run happened, so no run is claimed.
    expect(sent.runResults).toBeUndefined();
  });

  it("lists the checks so the learner can see what will be tried", () => {
    renderForm("code", {
      tests: [{ name: "ноль", call: "f(0)", expected: "0" }],
    });
    expect(screen.getByText("f(0) → 0")).toBeTruthy();
  });

  it("indents on Tab instead of leaving the field", () => {
    // A code editor that loses focus on Tab is unusable for anyone typing by touch.
    renderForm("code", { starter_code: "", tests: [] });
    const field = screen.getByTestId("answer-code") as HTMLTextAreaElement;
    fireEvent.change(field, { target: { value: "a" } });
    fireEvent.keyDown(field, { key: "Tab" });
    expect(field.value).toBe("a    ");
  });

  it("disables every control while an answer is being checked", () => {
    render(
      <TaskAnswerForm
        format="single_choice"
        payload={{ options: ["a", "b"] }}
        disabled
        submitLabel="Отправить"
        onAnswer={onAnswer}
      />,
    );
    // Blocking the whole form is what stops a second attempt being sent while the
    // first one is still being graded.
    expect(screen.getByTestId("answer-option-0")).toBeDisabled();
    expect(screen.getByText("Отправить").closest("button")).toBeDisabled();
  });
});