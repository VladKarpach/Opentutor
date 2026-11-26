import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { render, screen } from "@/test-utils";

import { CodeTaskDialog } from "./code-task-dialog";
import type { AnswerResult, Question } from "@/lib/api/learning";

/**
 * The code practice window.
 *
 * What is under test is the rule the window exists to enforce: a solution whose
 * checks have not all passed cannot be submitted, and a submitted solution that
 * the server accepts marks the task solved. Running the code itself is Pyodide's
 * job and is stubbed here.
 */

const { runTests, submitAnswer } = vi.hoisted(() => ({
  runTests: vi.fn(),
  submitAnswer: vi.fn(),
}));

vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("./python-runner", () => ({
  usePythonRunner: () => ({ state: { status: "ready" }, runTests }),
}));

vi.mock("@/lib/api/learning", () => ({ submitAnswer }));

const QUESTION: Question = {
  id: "q1",
  topic_id: "t1",
  topic_title: "Тема",
  question_type: "apply_situation",
  format: "code",
  text: "Напишите функцию f.",
  criteria: null,
  hints: [],
  difficulty: 0.5,
  source_label: null,
  payload: {
    language: "python",
    starter_code: "def f(x):\n    pass\n",
    tests: [{ name: "zero", call: "f(0)", expected: "0" }],
  },
};

function renderDialog(onSolved = vi.fn()) {
  render(
    <CodeTaskDialog
      open
      onOpenChange={() => {}}
      question={QUESTION}
      onSolved={onSolved}
    />,
  );
  return onSolved;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CodeTaskDialog", () => {
  it("shows the task and the starter code", () => {
    renderDialog();
    expect(screen.getByText("Напишите функцию f.")).toBeInTheDocument();
    expect(screen.getByTestId("code-task-editor")).toHaveValue(
      "def f(x):\n    pass\n",
    );
  });

  it("refuses to submit while a check is failing and asks for a fix", async () => {
    runTests.mockResolvedValue({
      results: [{ name: "zero", passed: false, detail: "f(0) → 1" }],
      output: "",
    });
    renderDialog();

    fireEvent.click(screen.getByTestId("code-task-check"));

    await waitFor(() =>
      expect(screen.getByTestId("code-task-retry")).toBeInTheDocument(),
    );
    // The whole point: a failing run does not travel to the server as an answer.
    expect(screen.queryByTestId("code-task-submit")).toBeNull();
    expect(submitAnswer).not.toHaveBeenCalled();
  });

  it("allows submission after every check passes, and reports the run", async () => {
    runTests.mockResolvedValue({
      results: [{ name: "zero", passed: true, detail: "0" }],
      output: "",
    });
    submitAnswer.mockResolvedValue({
      id: "a1",
      question_id: "q1",
      raw_score: 1,
      final_score: 1,
      hint_penalty: 0,
      feedback: "верно",
      error_types: [],
      expected_answer: null,
      interval_days: 1,
      next_review_at: null,
      checked_without_model: false,
    } as unknown as AnswerResult);

    const onSolved = renderDialog();
    fireEvent.click(screen.getByTestId("code-task-check"));

    await waitFor(() =>
      expect(screen.getByTestId("code-task-passed")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("code-task-submit"));

    await waitFor(() => expect(submitAnswer).toHaveBeenCalledTimes(1));
    expect(submitAnswer).toHaveBeenCalledWith("q1", {
      text: "def f(x):\n    pass\n",
      value: null,
      run_results: [{ name: "zero", passed: true }],
    });
    await waitFor(() => expect(onSolved).toHaveBeenCalled());
  });

  it("treats a rejected solution as unsolved and offers another attempt", async () => {
    runTests.mockResolvedValue({
      results: [{ name: "zero", passed: true, detail: "0" }],
      output: "",
    });
    submitAnswer.mockResolvedValue({
      id: "a1",
      question_id: "q1",
      raw_score: 0.5,
      final_score: 0.5,
      hint_penalty: 0,
      feedback: "код работает, но не решает задачу",
      error_types: [],
      expected_answer: null,
      interval_days: 1,
      next_review_at: null,
      checked_without_model: false,
    } as unknown as AnswerResult);

    const onSolved = renderDialog();
    fireEvent.click(screen.getByTestId("code-task-check"));
    await waitFor(() =>
      expect(screen.getByTestId("code-task-passed")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("code-task-submit"));

    await waitFor(() => expect(screen.getByTestId("code-task-grade")).toBeInTheDocument());
    expect(onSolved).not.toHaveBeenCalled();
    expect(screen.getByTestId("code-task-fix")).toBeInTheDocument();
  });
});
