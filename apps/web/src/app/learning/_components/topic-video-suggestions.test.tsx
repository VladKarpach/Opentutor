import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { render, screen } from "@/test-utils";

import { TopicVideoSuggestions } from "./topic-video-suggestions";

/**
 * Videos suggested from the learner's own channels.
 *
 * Two things have to be true at once, and they pull in opposite directions. The
 * system may only choose out of channels the learner picked, and every link it
 * shows must be one YouTube reported. It may not, however, attach anything by
 * itself: the offer is a proposal, and adding stays an explicit act.
 */

const { getVideoStatus, getTopicVideoCandidates, addTopicResource } = vi.hoisted(
  () => ({
    getVideoStatus: vi.fn(),
    getTopicVideoCandidates: vi.fn(),
    addTopicResource: vi.fn(),
  }),
);

vi.mock("@/lib/api", () => ({
  getVideoStatus,
  getTopicVideoCandidates,
}));

vi.mock("@/lib/api/learning", () => ({
  addTopicResource,
}));

vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const candidate = (over: Record<string, unknown> = {}) => ({
  video_id: "v1",
  title: "Метод Гаусса: прямой ход",
  url: "https://www.youtube.com/watch?v=v1",
  channel_id: "UC1",
  channel_title: "Канал",
  published_at: null,
  description: "",
  matched_terms: ["гаусса", "метод"],
  reason: "",
  ...over,
});

const discovery = (over: Record<string, unknown> = {}) => ({
  topic_id: "t1",
  candidates: [candidate()],
  report: { channels: 2, api_search: true, videos_seen: 10 },
  ...over,
});

describe("TopicVideoSuggestions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getVideoStatus.mockResolvedValue({ api_search: true, max_videos_per_channel: null, hint_ru: "" });
    getTopicVideoCandidates.mockResolvedValue(discovery());
    addTopicResource.mockResolvedValue({ id: "r1" });
  });

  it("offers a video with the words that matched", async () => {
    // "matched on производная" is what lets the learner judge the offer; a lesson
    // that shares nothing with the topic is not a lesson about it.
    render(<TopicVideoSuggestions topicId="t1" />);
    expect(await screen.findByText("Метод Гаусса: прямой ход")).toBeTruthy();
    expect(screen.getByText(/гаусса, метод/)).toBeTruthy();
  });

  it("attaches nothing on its own", async () => {
    render(<TopicVideoSuggestions topicId="t1" />);
    await screen.findByText("Метод Гаусса: прямой ход");
    expect(addTopicResource).not.toHaveBeenCalled();
  });

  it("adds a video only when asked", async () => {
    render(<TopicVideoSuggestions topicId="t1" />);
    fireEvent.click(await screen.findByText("videoSuggestions.add"));
    await waitFor(() =>
      expect(addTopicResource).toHaveBeenCalledWith("t1", {
        url: "https://www.youtube.com/watch?v=v1",
        title: "Метод Гаусса: прямой ход",
      }),
    );
  });

  it("says when no channels are set", async () => {
    // Different from "found nothing": one needs a channel, the other needs a
    // different channel.
    getTopicVideoCandidates.mockResolvedValue(
      discovery({ candidates: [], report: { channels: 0 } }),
    );
    render(<TopicVideoSuggestions topicId="t1" />);
    expect(await screen.findByText("videoSuggestions.noChannels")).toBeTruthy();
  });

  it("says when channels exist but nothing matched", async () => {
    getTopicVideoCandidates.mockResolvedValue(
      discovery({ candidates: [], report: { channels: 3, videos_seen: 45 } }),
    );
    render(<TopicVideoSuggestions topicId="t1" />);
    expect(await screen.findByText("videoSuggestions.noMatch")).toBeTruthy();
  });

  it("marks the shallow mode when no API key is set", async () => {
    // Without a key only the newest 15 videos per channel are reachable, and a
    // short list must not be presented as if the archive had been searched.
    getVideoStatus.mockResolvedValue({
      api_search: false,
      max_videos_per_channel: 15,
      hint_ru: "Ключ YouTube не задан",
    });
    render(<TopicVideoSuggestions topicId="t1" />);
    expect(await screen.findByText("videoSuggestions.fifteenOnly")).toBeTruthy();
  });

  it("hides itself when the lookup fails", async () => {
    // "Could not ask" must not read as "found nothing".
    getTopicVideoCandidates.mockRejectedValue(new Error("offline"));
    render(<TopicVideoSuggestions topicId="t1" />);
    await waitFor(() =>
      expect(screen.queryByTestId("topic-video-suggestions")).toBeNull(),
    );
  });

  it("shows the model's reason in place of the words for a cross-language pick", async () => {
    // A Russian topic against an English channel: YouTube finds it, and no word
    // of the query can appear in an English title. Without the reason the offer
    // would be an unexplained link, which is what the learner has no way to judge.
    getTopicVideoCandidates.mockResolvedValue(
      discovery({
        candidates: [
          candidate({
            title: "The essence of calculus",
            matched_terms: [],
            reason: "разбирает производную с нуля",
          }),
        ],
      }),
    );
    render(<TopicVideoSuggestions topicId="t1" />);
    expect(await screen.findByText(/разбирает производную с нуля/)).toBeTruthy();
  });

  it("says the archive search is unavailable when a key is set but refused", async () => {
    // The Data API allows 100 catalogue searches a day. Once that is spent the
    // channels were not consulted at all, and the newest 15 must not be passed
    // off as the result of a search.
    getTopicVideoCandidates.mockResolvedValue(
      discovery({
        candidates: [],
        report: { channels: 1, api_search: true, videos_seen: 15, search_failed: 1 },
      }),
    );
    render(<TopicVideoSuggestions topicId="t1" />);
    expect(await screen.findByText("videoSuggestions.searchUnavailable")).toBeTruthy();
    expect(screen.getByText("videoSuggestions.searchBlockedHint")).toBeTruthy();
  });

  it("does not claim the search is unavailable when it ran and found nothing", async () => {
    getTopicVideoCandidates.mockResolvedValue(
      discovery({
        candidates: [],
        report: { channels: 1, api_search: true, videos_seen: 15, search_failed: 0 },
      }),
    );
    render(<TopicVideoSuggestions topicId="t1" />);
    expect(await screen.findByText("videoSuggestions.noMatch")).toBeTruthy();
    expect(screen.queryByText("videoSuggestions.searchUnavailable")).toBeNull();
  });
});