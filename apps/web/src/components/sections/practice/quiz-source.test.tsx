import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { QuizView } from "./quiz-view";
import { extractQuiz, listProblems } from "@/lib/api";

/**
 * The quiz is built from a topic of the learner's own goals.
 *
 * It used to be built from the course's chapter tree with nothing chosen, which
 * produced questions about whatever the source document happened to contain. These
 * tests pin the two halves of the fix: the learner can say which goal and which
 * topic, and what they said is what gets sent.
 */

// vi.mock factories are hoisted above module-level consts, so the scope the
// picker answers with has to be hoisted with them.
const { SCOPE } = vi.hoisted(() => ({
  SCOPE: {
    goal_id: "goal-1",
    topic_id: "topic-2",
    topic_title: "Интегралы",
    exam_passed: false,
    topics_total: 2,
    topics_open: 1,
    topics: [
      { topic_id: "topic-1", topic_title: "Производная", order_index: 0, in_scope: false },
      { topic_id: "topic-2", topic_title: "Интегралы", order_index: 1, in_scope: true },
    ],
    goals: [
      { goal_id: "goal-1", goal_topic: "Математика", status: "active", topics_total: 2 },
      { goal_id: "goal-2", goal_topic: "Физика", status: "active", topics_total: 4 },
    ],
  },
}));

vi.mock("@/lib/api", () => ({
  listProblems: vi.fn().mockResolvedValue([]),
  extractQuiz: vi.fn().mockResolvedValue({ problems_created: 5 }),
  submitAnswer: vi.fn().mockResolvedValue({
    is_correct: true,
    correct_answer: "b",
    explanation: "",
    prerequisite_gaps: [],
  }),
}));

vi.mock("@/lib/api/practice", () => ({
  getFlashcardScope: vi.fn().mockResolvedValue(SCOPE),
}));

vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/store/workspace", () => ({
  useWorkspaceStore: Object.assign(
    (selector: (s: unknown) => unknown) =>
      selector({ sectionRefreshKey: { practice: 0 }, spaceLayout: { mode: undefined } }),
    { getState: () => ({ spaceLayout: { mode: undefined } }) },
  ),
}));

vi.mock("@/lib/block-system/feature-unlock", () => ({
  updateUnlockContext: vi.fn(),
  getUnlockContext: vi.fn(() => ({})),
}));

vi.mock("@/components/shared/ai-feature-blocked", () => ({
  AiFeatureBlocked: () => <div data-testid="ai-blocked" />,
}));

// Stable across renders on purpose. The component's fetch callback is keyed on
// this object's `load`, so a fresh object each render re-runs the load effect
// forever and the panel never leaves its skeleton.
const { PERSISTENCE } = vi.hoisted(() => ({
  PERSISTENCE: { save: vi.fn(), load: vi.fn(() => null), clear: vi.fn() },
}));

vi.mock("./use-quiz-persistence", () => ({
  useQuizPersistence: () => PERSISTENCE,
}));

describe("QuizView source of material", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProblems).mockResolvedValue([]);
  });

  it("asks for the goal's current topic when neither is named", async () => {
    // No topic_id and no goal_id: the server resolves the goal's own current
    // topic, which is the same rule the cards follow.
    render(<QuizView courseId="c1" />);
    fireEvent.click(await screen.findByText("quiz.generate"));
    await waitFor(() => expect(extractQuiz).toHaveBeenCalled());
    const args = vi.mocked(extractQuiz).mock.calls[0];
    expect(args[4]).toBeUndefined();
    expect(args[5]).toBeUndefined();
  });

  it("sends the topic the learner picked", async () => {
    render(<QuizView courseId="c1" />);
    const picker = await screen.findByLabelText("topic.pickerLabel");
    fireEvent.change(picker, { target: { value: "topic-1" } });
    fireEvent.click(screen.getByText("quiz.generate"));
    await waitFor(() => expect(extractQuiz).toHaveBeenCalled());
    expect(vi.mocked(extractQuiz).mock.calls[0][5]).toBe("topic-1");
  });

  it("sends the goal the learner picked", async () => {
    render(<QuizView courseId="c1" />);
    fireEvent.change(await screen.findByLabelText("topic.goalLabel"), {
      target: { value: "goal-2" },
    });
    fireEvent.click(screen.getByText("quiz.generate"));
    await waitFor(() => expect(extractQuiz).toHaveBeenCalled());
    expect(vi.mocked(extractQuiz).mock.calls[0][4]).toBe("goal-2");
  });

  it("forgets the topic when the goal changes", async () => {
    // A topic id belongs to one goal. Sending the old one with the new goal is
    // either ignored or, worse, matched to an unrelated topic.
    render(<QuizView courseId="c1" />);
    fireEvent.change(await screen.findByLabelText("topic.pickerLabel"), {
      target: { value: "topic-1" },
    });
    fireEvent.change(screen.getByLabelText("topic.goalLabel"), {
      target: { value: "goal-2" },
    });
    fireEvent.click(screen.getByText("quiz.generate"));
    await waitFor(() => expect(extractQuiz).toHaveBeenCalled());
    expect(vi.mocked(extractQuiz).mock.calls[0][5]).toBeUndefined();
  });
});