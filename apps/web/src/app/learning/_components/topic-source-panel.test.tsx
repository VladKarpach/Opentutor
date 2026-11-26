import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { render, screen } from "@/test-utils";

import { TopicSourcePanel } from "./topic-source-panel";

/**
 * Two things a topic screen has to make visible, and one thing it must not do.
 *
 * The source is the learner's own literature — it is what the explanation is
 * written from, so it has to be shown and pinnable when the automatic match is
 * wrong. The videos are links the learner supplied: there is deliberately no
 * search and no generation behind them, because a model asked for a video URL
 * returns a well-formed address for a video that does not exist.
 */

const {
  getTopicMaterial,
  listTopicResources,
  setTopicSource,
  addTopicResource,
  deleteTopicResource,
} = vi.hoisted(() => ({
  getTopicMaterial: vi.fn(),
  listTopicResources: vi.fn(),
  setTopicSource: vi.fn(),
  addTopicResource: vi.fn(),
  deleteTopicResource: vi.fn(),
}));

vi.mock("@/lib/api/learning", () => ({
  getTopicMaterial,
  listTopicResources,
  setTopicSource,
  addTopicResource,
  deleteTopicResource,
}));

vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const PASSAGE = {
  node_id: "n1",
  title: "Метод Гаусса",
  source_file: "учебник.pdf",
  level: 1,
  matched_terms: ["гаусс", "метод"],
  excerpt: "Метод Гаусса прямой ход приведение к ступенчатому виду.",
};

const material = (over: Record<string, unknown> = {}) => ({
  topic_id: "t1",
  course_id: "c1",
  source_node_id: null,
  course_has_material: true,
  automatic: [PASSAGE],
  source_label: "учебник.pdf",
  ...over,
});

describe("TopicSourcePanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTopicMaterial.mockResolvedValue(material());
    listTopicResources.mockResolvedValue([]);
    setTopicSource.mockResolvedValue({});
  });

  it("shows the passage the explanation will be written from", async () => {
    render(<TopicSourcePanel topicId="t1" />);
    expect(await screen.findByText("Метод Гаусса")).toBeTruthy();
    // The file it came from: FR-3.4 has the answer name its source.
    expect(screen.getByText("учебник.pdf")).toBeTruthy();
  });

  it("shows the words that caused the automatic match", async () => {
    // A learner cannot judge a source they did not choose without seeing why
    // the matcher offered it — "гаусс, метод" is checkable, a bare excerpt is not.
    render(<TopicSourcePanel topicId="t1" />);
    expect(await screen.findByText("topicSource.matchedTerms")).toBeTruthy();
  });

  it("pins a passage when the automatic match is wrong", async () => {
    render(<TopicSourcePanel topicId="t1" />);
    const use = await screen.findByText("topicSource.use");
    fireEvent.click(use);
    await waitFor(() => expect(setTopicSource).toHaveBeenCalledWith("t1", "n1"));
  });

  it("unpins the current passage when it is clicked again", async () => {
    getTopicMaterial.mockResolvedValue(material({ source_node_id: "n1" }));
    render(<TopicSourcePanel topicId="t1" />);
    const pinned = await screen.findByText("topicSource.pinnedBadge");
    fireEvent.click(pinned);
    // An empty id is how the client says "go back to matching".
    await waitFor(() => expect(setTopicSource).toHaveBeenCalledWith("t1", null));
  });

  it("says the course has no material, rather than showing an empty list", async () => {
    // "Nothing matched" and "nothing uploaded" look identical and call for
    // opposite actions, so the two states get different sentences.
    getTopicMaterial.mockResolvedValue(
      material({ course_has_material: false, automatic: [] }),
    );
    render(<TopicSourcePanel topicId="t1" />);
    expect(await screen.findByText("topicSource.noMaterial")).toBeTruthy();
  });

  it("says nothing matched when there is material but no passage fits", async () => {
    getTopicMaterial.mockResolvedValue(
      material({ course_has_material: true, automatic: [] }),
    );
    render(<TopicSourcePanel topicId="t1" />);
    expect(await screen.findByText("topicSource.noMatch")).toBeTruthy();
  });

  it("adds a link the learner pasted", async () => {
    addTopicResource.mockResolvedValue({ id: "r1" });
    render(<TopicSourcePanel topicId="t1" />);
    const url = await screen.findByLabelText("topicSource.videoUrl");
    fireEvent.change(url, { target: { value: "https://youtu.be/abc" } });
    fireEvent.click(screen.getByText("topicSource.addVideo"));
    await waitFor(() =>
      expect(addTopicResource).toHaveBeenCalledWith("t1", {
        url: "https://youtu.be/abc",
        title: undefined,
      }),
    );
  });

  it("lists links that were already saved", async () => {
    listTopicResources.mockResolvedValue([
      {
        id: "r1",
        topic_id: "t1",
        kind: "video",
        url: "https://youtu.be/abc",
        title: "Лекция 3",
        note: null,
      },
    ]);
    render(<TopicSourcePanel topicId="t1" />);
    expect(await screen.findByText("Лекция 3")).toBeTruthy();
  });

  it("removes a link", async () => {
    deleteTopicResource.mockResolvedValue(undefined);
    listTopicResources.mockResolvedValue([
      {
        id: "r1",
        topic_id: "t1",
        kind: "video",
        url: "https://youtu.be/abc",
        title: "Лекция 3",
        note: null,
      },
    ]);
    render(<TopicSourcePanel topicId="t1" />);
    fireEvent.click(await screen.findByLabelText("topicSource.removeVideo"));
    await waitFor(() => expect(deleteTopicResource).toHaveBeenCalledWith("t1", "r1"));
  });

  it("does not offer to add an empty link", async () => {
    render(<TopicSourcePanel topicId="t1" />);
    const button = await screen.findByText("topicSource.addVideo");
    expect(button.closest("button")?.disabled).toBe(true);
  });

  it("hides the panel's content when the request fails", async () => {
    // A failed lookup must not read as "this course has no literature".
    getTopicMaterial.mockRejectedValue(new Error("offline"));
    listTopicResources.mockRejectedValue(new Error("offline"));
    render(<TopicSourcePanel topicId="t1" />);
    await waitFor(() =>
      expect(screen.queryByText("topicSource.noMaterial")).toBeNull(),
    );
    expect(screen.queryByText("topicSource.noMatch")).toBeNull();
  });
});