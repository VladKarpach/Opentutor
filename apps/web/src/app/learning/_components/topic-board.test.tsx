import { describe, it, expect, vi, beforeEach } from "vitest";
// fireEvent is not re-exported from @/test-utils; it comes from the library.
import { fireEvent } from "@testing-library/react";
import { render, screen, waitFor } from "@/test-utils";

import { TopicBoard } from "./topic-board";

/**
 * Regeneration safety.
 *
 * `generateTopics` replaces the goal's topic set, and the delete cascades to
 * every topic state, plan item, drill, question and answer. That button is
 * therefore offered *only* when the goal has no topics — the empty branch. This
 * pins that the destructive path has no entry point, so mounting this panel
 * cannot hand a learner a one-click way to lose their history.
 */

const { generateTopics, listTopics } = vi.hoisted(() => ({
  generateTopics: vi.fn(),
  listTopics: vi.fn(),
}));

vi.mock("@/lib/api/learning", () => ({
  listTopics,
  generateTopics,
  updateTopic: vi.fn(),
  addTopics: vi.fn(),
}));

const TOPIC = {
  id: "t1",
  title: "Первая тема",
  description: "d",
  order_index: 0,
  estimated_minutes: 30,
  manual_status: null,
  status: "weak",
  strength: 0.4,
  next_review_at: null,
};

describe("TopicBoard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    generateTopics.mockResolvedValue([]);
  });

  it("offers no regeneration once the goal has topics", async () => {
    // The safety property: replacing topics would delete the history behind
    // them, so there must be no control that does it.
    listTopics.mockResolvedValue([TOPIC]);
    render(<TopicBoard goalId="goal-1" />);
    await screen.findByText("Первая тема");

    expect(screen.queryByRole("button", { name: /разбить/i })).toBeNull();
  });

  it("offers it when the goal has none, where nothing can be lost", async () => {
    listTopics.mockResolvedValue([]);
    render(<TopicBoard goalId="goal-1" />);

    // findByRole waits for the empty branch on its own; an extra waitFor on the
    // fetch call only races it.
    const button = await screen.findByRole("button", { name: /разбить/i });
    fireEvent.click(button);

    await waitFor(() => expect(generateTopics).toHaveBeenCalledWith("goal-1"));
  });

  it("shows the topic's strength and status", async () => {
    listTopics.mockResolvedValue([TOPIC]);
    render(<TopicBoard goalId="goal-1" />);
    await screen.findByText("Первая тема");

    // FR-9.1: the repetition engine's numbers are visible per topic.
    expect(screen.getByRole("meter", { name: /сила знаний/i })).toBeTruthy();
    expect(screen.getByText(/слабое место/i)).toBeTruthy();
  });
});