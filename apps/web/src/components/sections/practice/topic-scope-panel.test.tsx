import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent } from "@testing-library/react";
import { render, screen, waitFor } from "@/test-utils";

import { TopicScopePanel } from "./topic-scope-panel";

/**
 * The one control that decides what cards are written from: which topic.
 *
 * It was a panel showing the goal's current topic, a progress badge and a plan
 * hint, with the picker buried underneath — the request was simply "pick a topic
 * and generate", so that is all it does now. The picker must stay available in
 * every state, including before any cards exist and after the exam is passed.
 */

const getFlashcardScope = vi.fn();

vi.mock("@/lib/api/practice", () => ({
  getFlashcardScope: (...a: unknown[]) => getFlashcardScope(...a),
}));

vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => key,
}));

const TOPICS = [
  { topic_id: "t1", topic_title: "Производная", order_index: 0, in_scope: false },
  { topic_id: "t2", topic_title: "Интегралы", order_index: 1, in_scope: true },
];

const GOALS = [
  { goal_id: "goal-1", goal_topic: "Математика", status: "active", topics_total: 2 },
  { goal_id: "goal-2", goal_topic: "Физика", status: "active", topics_total: 4 },
];

const scope = (over: Record<string, unknown> = {}) => ({
  goal_id: "goal-1",
  topic_id: "t2",
  topic_title: "Интегралы",
  exam_passed: false,
  topics_total: 2,
  topics_open: 1,
  topics: TOPICS,
  goals: GOALS,
  ...over,
});

describe("TopicScopePanel goal picker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getFlashcardScope.mockResolvedValue(scope());
  });

  it("offers every goal of the course, not just the one it would guess", async () => {
    // The goal decides which topics there are. Guessing it is what produced cards
    // about a subject the learner was not working on.
    render(
      <TopicScopePanel
        courseId="c1"
        onTopicChange={vi.fn()}
        onGoalChange={vi.fn()}
      />,
    );
    const picker = await screen.findByLabelText("topic.goalLabel");
    expect(picker).toBeTruthy();
    expect(screen.getByRole("option", { name: /^Математика/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /^Физика/ })).toBeTruthy();
  });

  it("reports the goal the learner chose", async () => {
    const onGoalChange = vi.fn();
    render(
      <TopicScopePanel
        courseId="c1"
        onTopicChange={vi.fn()}
        onGoalChange={onGoalChange}
      />,
    );
    fireEvent.change(await screen.findByLabelText("topic.goalLabel"), {
      target: { value: "goal-2" },
    });
    expect(onGoalChange).toHaveBeenCalledWith("goal-2");
  });

  it("marks a goal whose topics have not been made yet", async () => {
    getFlashcardScope.mockResolvedValue(
      scope({
        goals: [
          { goal_id: "goal-1", goal_topic: "Математика", status: "active", topics_total: 0 },
          GOALS[1],
        ],
      }),
    );
    render(
      <TopicScopePanel
        courseId="c1"
        onTopicChange={vi.fn()}
        onGoalChange={vi.fn()}
      />,
    );
    // useT is mocked to echo the key, so the suffix arrives untranslated here.
    expect(await screen.findByText(/topic\.goalNoTopics/)).toBeTruthy();
  });

  it("hides the picker when the course has a single goal", async () => {
    // One goal leaves nothing to choose, and an empty-looking select is noise.
    getFlashcardScope.mockResolvedValue(scope({ goals: [GOALS[0]] }));
    render(
      <TopicScopePanel
        courseId="c1"
        onTopicChange={vi.fn()}
        onGoalChange={vi.fn()}
      />,
    );
    await screen.findByLabelText("topic.pickerLabel");
    expect(screen.queryByLabelText("topic.goalLabel")).toBeNull();
  });

  it("hides the picker when the caller did not ask for a goal choice", async () => {
    // A select that cannot report a change would be inert, so it is not drawn.
    render(<TopicScopePanel courseId="c1" onTopicChange={vi.fn()} />);
    await screen.findByLabelText("topic.pickerLabel");
    expect(screen.queryByLabelText("topic.goalLabel")).toBeNull();
  });
});

describe("TopicScopePanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getFlashcardScope.mockResolvedValue(scope());
  });

  it("offers every topic of the goal, not only the current one", async () => {
    render(<TopicScopePanel courseId="c1" onTopicChange={vi.fn()} />);
    const picker = await screen.findByLabelText("topic.pickerLabel");
    // The goal's own rule would serve only "Интегралы"; an earlier topic has to be
    // reachable on request.
    expect(screen.getByRole("option", { name: "Производная" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Интегралы" })).toBeTruthy();
    expect(picker).toBeTruthy();
  });

  it("defaults to following the goal's own rule", async () => {
    render(<TopicScopePanel courseId="c1" onTopicChange={vi.fn()} />);
    const picker = (await screen.findByLabelText(
      "topic.pickerLabel",
    )) as HTMLSelectElement;
    expect(picker.value).toBe("");
  });

  it("reports the topic the learner chose", async () => {
    const onChange = vi.fn();
    render(<TopicScopePanel courseId="c1" onTopicChange={onChange} />);
    fireEvent.change(await screen.findByLabelText("topic.pickerLabel"), {
      target: { value: "t1" },
    });
    expect(onChange).toHaveBeenCalledWith("t1");
  });

  it("returns to following the plan when the choice is cleared", async () => {
    const onChange = vi.fn();
    render(
      <TopicScopePanel courseId="c1" selectedTopicId="t1" onTopicChange={onChange} />,
    );
    fireEvent.change(await screen.findByLabelText("topic.pickerLabel"), {
      target: { value: "" },
    });
    expect(onChange).toHaveBeenCalledWith(undefined);
  });

  it("shows the chosen topic rather than the plan's", async () => {
    render(
      <TopicScopePanel courseId="c1" selectedTopicId="t1" onTopicChange={vi.fn()} />,
    );
    const picker = (await screen.findByLabelText(
      "topic.pickerLabel",
    )) as HTMLSelectElement;
    expect(picker.value).toBe("t1");
  });

  it("keeps the picker after the exam is passed", async () => {
    // The passed-exam state used to be a separate screen with no picker at all,
    // which hid the control exactly when every topic became available.
    getFlashcardScope.mockResolvedValue(
      scope({ exam_passed: true, topics_open: 2, topics_total: 2 }),
    );
    render(<TopicScopePanel courseId="c1" onTopicChange={vi.fn()} />);
    expect(await screen.findByLabelText("topic.pickerLabel")).toBeTruthy();
    expect(screen.getByText("topic.examPassedHint")).toBeTruthy();
  });

  it("shows the picker with a single topic too", async () => {
    getFlashcardScope.mockResolvedValue(
      scope({ topics: [TOPICS[1]], topics_total: 1, topics_open: 1 }),
    );
    render(<TopicScopePanel courseId="c1" onTopicChange={vi.fn()} />);
    expect(await screen.findByLabelText("topic.pickerLabel")).toBeTruthy();
  });

  it("disables the picker when the caller does not want one", async () => {
    render(<TopicScopePanel courseId="c1" />);
    const picker = (await screen.findByLabelText(
      "topic.pickerLabel",
    )) as HTMLSelectElement;
    expect(picker.disabled).toBe(true);
  });

  it("says a goal is needed when the course has none", async () => {
    getFlashcardScope.mockResolvedValue(
      scope({ goal_id: null, topic_id: null, topic_title: null, topics: [] }),
    );
    render(<TopicScopePanel courseId="c1" onTopicChange={vi.fn()} />);
    expect(await screen.findByText("topic.needGoal")).toBeTruthy();
    expect(screen.queryByLabelText("topic.pickerLabel")).toBeNull();
  });

  it("renders nothing while it loads", () => {
    getFlashcardScope.mockReturnValue(new Promise(() => {}));
    const { container } = render(<TopicScopePanel courseId="c1" onTopicChange={vi.fn()} />);
    expect(container.textContent).toBe("");
  });

  it("stays out of the way when the request fails", async () => {
    // A failed request is not the same as having no goal.
    getFlashcardScope.mockRejectedValue(new Error("offline"));
    const { container } = render(<TopicScopePanel courseId="c1" onTopicChange={vi.fn()} />);
    await waitFor(() => expect(getFlashcardScope).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("passes an explicit goal through", async () => {
    render(<TopicScopePanel courseId="c1" goalId="goal-9" onTopicChange={vi.fn()} />);
    await waitFor(() =>
      expect(getFlashcardScope).toHaveBeenCalledWith("c1", "goal-9"),
    );
  });
});