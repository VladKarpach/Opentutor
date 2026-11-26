import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent } from "@testing-library/react";
import { render, screen, waitFor } from "@/test-utils";

import { TopicNotes } from "./topic-notes";

/**
 * Notes follow the goal's topic, not the course's chapter tree.
 *
 * The note is a summary of the explanation the learner already read, so it must
 * never be generated for a topic that has no explanation — that is how the wrong
 * subject ended up in a note.
 */

const getTopicNotes = vi.fn();
const writeTopicNote = vi.fn();
const toastError = vi.fn();

vi.mock("@/lib/api/learning", () => ({
  getTopicNotes: (...a: unknown[]) => getTopicNotes(...a),
  writeTopicNote: (...a: unknown[]) => writeTopicNote(...a),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a) },
}));

// The real translator interpolates {placeholders}; returning bare keys would make
// the component's .replace() a no-op and hide a wrong count.
const TRANSLATIONS: Record<string, string> = {
  "notes.showMore": "notes.showMore({count})",
};

vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => TRANSLATIONS[key] ?? key,
}));

vi.mock("@/components/shared/markdown-renderer", () => ({
  MarkdownRenderer: ({ content }: { content: string }) => (
    <div data-testid="markdown">{content}</div>
  ),
}));

const topic = (
  id: string,
  title: string,
  over: Record<string, unknown> = {},
) => ({
  topic_id: id,
  topic_title: title,
  goal_id: "goal-1",
  goal_topic: "Метод Гаусса",
  course_id: "course-1",
  order_index: 0,
  explanation_chars: 900,
  note_source: "explanation" as const,
  has_note: false,
  note_markdown: null,
  ...over,
});

const TOPICS = [
  topic("t1", "Основные понятия"),
  topic("t2", "Прямой ход метода", { has_note: true, note_markdown: "# Прямой ход" }),
];

/**
 * The active topic's name also appears on the empty-state badge, so plain text
 * queries are ambiguous. The list entry is a button.
 */
const listItem = (name: string) => screen.findByRole("button", { name });

describe("TopicNotes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTopicNotes.mockResolvedValue({ topics: TOPICS });
    writeTopicNote.mockResolvedValue({
      topic_id: "t1",
      topic_title: "Основные понятия",
      goal_id: "goal-1",
      markdown: "# Сводка",
      cached: false,
    });
  });

  it("lists only topics that have an explanation", async () => {
    render(<TopicNotes courseId="course-1" />);
    await waitFor(() => expect(getTopicNotes).toHaveBeenCalled());
    expect(getTopicNotes).toHaveBeenCalledWith({ courseId: "course-1", goalId: undefined });
    // Topics with no explanation never arrive, so there is nothing to filter out.
    expect(await listItem("Основные понятия")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Прямой ход метода" })).toBeTruthy();
  });

  it("asks for every goal when the scope is the whole picture", async () => {
    render(<TopicNotes showAllGoals />);
    await waitFor(() =>
      expect(getTopicNotes).toHaveBeenCalledWith({}),
    );
  });

  it("shows a saved note as soon as its topic is picked", async () => {
    render(<TopicNotes courseId="course-1" />);
    await listItem("Основные понятия");
    fireEvent.click(screen.getByRole("button", { name: "Прямой ход метода" }));
    expect(await screen.findByTestId("markdown")).toHaveProperty(
      "textContent",
      "# Прямой ход",
    );
  });

  it("offers to write a note for a topic that has none", async () => {
    render(<TopicNotes courseId="course-1" />);
    await listItem("Основные понятия");
    expect(screen.getByText("notes.notWritten")).toBeTruthy();
    fireEvent.click(screen.getAllByText("notes.write")[0]);
    await waitFor(() => expect(writeTopicNote).toHaveBeenCalledWith("t1"));
  });

  it("offers a rewrite once a note exists", async () => {
    render(<TopicNotes courseId="course-1" />);
    await listItem("Основные понятия");
    fireEvent.click(screen.getByRole("button", { name: "Прямой ход метода" }));
    expect(await screen.findByText("notes.rewrite")).toBeTruthy();
  });

  it("shows the note the model returned", async () => {
    writeTopicNote.mockResolvedValue({
      topic_id: "t1",
      topic_title: "Основные понятия",
      goal_id: "goal-1",
      markdown: "# Новая сводка",
      cached: false,
    });
    render(<TopicNotes courseId="course-1" />);
    await listItem("Основные понятия");
    fireEvent.click(screen.getAllByText("notes.write")[0]);
    expect(await screen.findByTestId("markdown")).toHaveProperty(
      "textContent",
      "# Новая сводка",
    );
  });

  it("reports a refusal instead of pretending it worked", async () => {
    writeTopicNote.mockRejectedValue(
      new Error("У темы пока нет объяснения — не из чего составить конспект"),
    );
    render(<TopicNotes courseId="course-1" />);
    await listItem("Основные понятия");
    fireEvent.click(screen.getAllByText("notes.write")[0]);
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.queryByTestId("markdown")).toBeNull();
  });

  it("points at the explanation when no topic can be summarised", async () => {
    // The index lists only topics that have an explanation, so an empty list means
    // there is no explanation to write from — naming that cause is the whole point.
    getTopicNotes.mockResolvedValue({ topics: [] });
    render(<TopicNotes courseId="course-1" />);
    expect(await screen.findByText("notes.noExplanation")).toBeTruthy();
  });

  it("groups topics by goal across the whole picture", async () => {
    getTopicNotes.mockResolvedValue({
      topics: [
        topic("t1", "Тема из первой цели", { goal_topic: "Первая цель" }),
        topic("t2", "Тема из второй цели", { goal_topic: "Вторая цель" }),
      ],
    });
    render(<TopicNotes showAllGoals />);
    expect(await screen.findByText("Первая цель")).toBeTruthy();
    expect(screen.getByText("Вторая цель")).toBeTruthy();
  });

  it("hides the write action when the model is unavailable", async () => {
    render(<TopicNotes courseId="course-1" aiActionsEnabled={false} />);
    await listItem("Основные понятия");
    expect(screen.queryByText("notes.write")).toBeNull();
  });

  it("renders nothing while it loads", () => {
    getTopicNotes.mockReturnValue(new Promise(() => {}));
    const { container } = render(<TopicNotes courseId="course-1" />);
    expect(container.textContent).toBe("");
  });

  it("survives a failed index request", async () => {
    getTopicNotes.mockRejectedValue(new Error("offline"));
    render(<TopicNotes courseId="course-1" />);
    expect(await screen.findByText("notes.noExplanation")).toBeTruthy();
  });
});

describe("TopicNotes and the material a note may come from", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists only topics that have an explanation", async () => {
    // The note condenses the explanation and carries nothing else, so a topic
    // without one has no material and is not a row the learner could open. The
    // backend drops such topics; this pins that the list stays a list of what can
    // actually be written.
    getTopicNotes.mockResolvedValue({
      topics: [
        topic("t1", "Тема с объяснением", {
          explanation_chars: 900,
          note_source: "explanation",
        }),
      ],
    });
    render(<TopicNotes courseId="course-1" />);
    expect(await listItem("Тема с объяснением")).toBeTruthy();
    expect(screen.getAllByText("notes.write").length).toBeGreaterThan(0);
  });

  it("writes the note without a warning about its source", async () => {
    // There used to be a banner saying the note was thin because it came from a
    // title alone. That case cannot reach the list now, so the banner is gone
    // rather than kept for a situation that cannot occur.
    getTopicNotes.mockResolvedValue({
      topics: [
        topic("t1", "Тема с объяснением", {
          explanation_chars: 900,
          note_source: "explanation",
          has_note: true,
          note_markdown: "## Вопрос\n\nОтвет",
        }),
      ],
    });
    const { container } = render(<TopicNotes courseId="course-1" />);
    await listItem("Тема с объяснением");
    expect(await screen.findByTestId("markdown")).toBeTruthy();
    expect(container.querySelector(".bg-warning-muted")).toBeNull();
  });
});

it("scrolls the note instead of showing all of it at once", async () => {
    getTopicNotes.mockResolvedValue({
      topics: [
        topic("t1", "С конспектом", {
          has_note: true,
          note_markdown: "# Конспект\n\n" + "абзац\n\n".repeat(80),
        }),
      ],
    });
    const { container } = render(<TopicNotes courseId="course-1" />);
    await listItem("С конспектом");
    // A definite height plus overflow: with only a max-height on a flex child the
    // column grows to the note's full length and nothing scrolls. The topic list
    // scrolls too, so this is matched on the scrollbar class it alone carries.
    // Waited for rather than asserted straight away — the note body renders a tick
    // after the list entry appears, and the race only showed under load.
    const scroller = await waitFor(() => {
      const found = container.querySelector(".scrollbar-thin");
      expect(found).toBeTruthy();
      return found as HTMLElement;
    });
    expect(scroller.className).toContain("overflow-y-auto");
    expect(scroller.className).toContain("h-[60vh]");
  });

describe("TopicNotes long list", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const many = Array.from({ length: 40 }, (_, i) =>
      topic(`t${i}`, `Тема ${i + 1}`),
    );
    getTopicNotes.mockResolvedValue({ topics: many });
  });

  it("lists 15 topics to begin with", async () => {
    render(<TopicNotes courseId="course-1" />);
    await listItem("Тема 1");
    expect(screen.getByText("Тема 15")).toBeTruthy();
    // The rest wait behind a control rather than pushing the note out of view.
    expect(screen.queryByText("Тема 16")).toBeNull();
  });

  it("offers to show the rest", async () => {
    render(<TopicNotes courseId="course-1" />);
    await listItem("Тема 1");
    fireEvent.click(screen.getByText("notes.showMore(25)"));
    expect(await screen.findByText("Тема 40")).toBeTruthy();
    expect(screen.queryByText("notes.showMore(25)")).toBeNull();
  });

  it("can fold the list back down", async () => {
    render(<TopicNotes courseId="course-1" />);
    await listItem("Тема 1");
    fireEvent.click(screen.getByText("notes.showMore(25)"));
    await screen.findByText("Тема 40");
    fireEvent.click(screen.getByText("notes.showLess"));
    expect(await screen.findByText("Тема 15")).toBeTruthy();
    expect(screen.queryByText("Тема 16")).toBeNull();
  });

  it("shows no control when everything fits", async () => {
    getTopicNotes.mockResolvedValue({
      topics: [topic("t1", "Одна тема")],
    });
    render(<TopicNotes courseId="course-1" />);
    await listItem("Одна тема");
    expect(screen.queryByText(/notes\.showMore/)).toBeNull();
    expect(screen.queryByText("notes.showLess")).toBeNull();
  });

  it("counts the remainder across goals, not per group", async () => {
    getTopicNotes.mockResolvedValue({
      topics: [
        ...Array.from({ length: 10 }, (_, i) =>
          topic(`a${i}`, `Первая ${i}`, { goal_topic: "Цель A" })),
        ...Array.from({ length: 10 }, (_, i) =>
          topic(`b${i}`, `Вторая ${i}`, { goal_topic: "Цель B" })),
      ],
    });
    render(<TopicNotes showAllGoals />);
    await listItem("Первая 0");
    // 20 topics in all, so 5 are held back — not 5 per goal.
    expect(screen.getByText("notes.showMore(5)")).toBeTruthy();
  });
});