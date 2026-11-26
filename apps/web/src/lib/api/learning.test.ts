/**
 * Tests for the adaptive learning API client.
 *
 * These pin the URL shapes and request bodies, so a backend route rename
 * surfaces here instead of as a 404 at runtime in the UI.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const requestMock = vi.fn();
const requestBlobMock = vi.fn();

vi.mock("./client", () => ({
  request: (path: string, options?: RequestInit) => requestMock(path, options),
  requestBlob: (path: string, options?: RequestInit) => requestBlobMock(path, options),
}));

import {
  activateGoal,
  addTopics,
  buildPlan,
  catchUpPlan,
  clarifyGoal,
  completeDailySession,
  correctDiagnostic,
  createGoal,
  deleteGoal,
  downloadPlanIcs,
  explainTopic,
  prepareDailySession,
  generateQuestion,
  getAllocation,
  getAnswerHistory,
  getDiagnosticState,
  getNextReviewQuestion,
  getPlan,
  getProfile,
  getProgress,
  getTopic,
  getTopicGraph,
  listGoals,
  planExam,
  replyToDiagnostic,
  startDiagnostic,
  submitAnswer,
  updatePlanItem,
  updateProfile,
  updateTopic,
} from "./learning";

beforeEach(() => {
  requestMock.mockReset();
  requestMock.mockResolvedValue({});
  requestBlobMock.mockReset();
  requestBlobMock.mockResolvedValue({
    blob: new Blob(),
    fileName: null,
    contentType: "",
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function pathOf(index = 0): string {
  return requestMock.mock.calls[index][0] as string;
}

function methodOf(index = 0): string | undefined {
  return requestMock.mock.calls[index][1]?.method;
}

function bodyOf(index = 0): Record<string, unknown> {
  return JSON.parse(requestMock.mock.calls[index][1].body as string);
}

describe("goals", () => {
  it("creates a goal with a relative deadline", () => {
    createGoal({ topic: "Статистика", days: 14, minutes_per_day: 45, depth: "confident" });

    expect(pathOf()).toBe("/learning/goals");
    expect(methodOf()).toBe("POST");
    expect(bodyOf()).toEqual({
      topic: "Статистика",
      minutes_per_day: 45,
      depth: "confident",
      days: 14,
    });
  });

  it("omits days when no deadline is given", () => {
    createGoal({ topic: "Без срока" });
    expect(bodyOf().days).toBeUndefined();
  });

  it("filters the list by status", () => {
    listGoals("paused");
    expect(pathOf()).toBe("/learning/goals?status=paused");
  });

  it("omits the filter when no status is given", () => {
    listGoals();
    expect(pathOf()).toBe("/learning/goals");
  });

  it("sends a DELETE without a body", () => {
    deleteGoal("g1");
    expect(methodOf()).toBe("DELETE");
    expect(requestMock.mock.calls[0][1].body).toBeUndefined();
  });

  it("posts the clarification check", () => {
    clarifyGoal("g1");
    expect(pathOf()).toBe("/learning/goals/g1/clarify");
    expect(methodOf()).toBe("POST");
  });

  it("selects the goal the daily mode should open", () => {
    activateGoal("g1");
    expect(pathOf()).toBe("/learning/goals/g1/activate");
    expect(methodOf()).toBe("POST");
  });
});

describe("topics", () => {
  it("adds several topics at once", () => {
    addTopics("g1", ["A", "B"]);
    expect(pathOf()).toBe("/learning/goals/g1/topics");
    expect(bodyOf()).toEqual({ topics: ["A", "B"] });
  });

  it("fetches a single topic", () => {
    getTopic("t1");
    expect(pathOf()).toBe("/learning/topics/t1");
  });

  it("marks a topic as known", () => {
    updateTopic("t1", { manual_status: "known" });
    expect(methodOf()).toBe("PATCH");
    expect(pathOf()).toBe("/learning/topics/t1");
    expect(bodyOf()).toEqual({ manual_status: "known" });
  });

  it("passes style and regenerate to the explain endpoint", async () => {
    await explainTopic("t1", "beginner", true);
    expect(pathOf()).toBe("/learning/topics/t1/explain");
    expect(bodyOf()).toEqual({ style: "beginner", regenerate: true, language: "ru" });
  });

  it("defaults to a short explanation without regeneration", async () => {
    await explainTopic("t1");
    expect(bodyOf()).toEqual({ style: "short", regenerate: false, language: "ru" });
  });
});

describe("plan", () => {
  it("reads the plan", () => {
    getPlan("g1");
    expect(pathOf()).toBe("/learning/goals/g1/plan");
    expect(methodOf()).toBeUndefined();
  });

  it("posts options when replanning", () => {
    buildPlan("g1", { days: 7, minutes_per_day: 60, speed: 1.2 });
    expect(methodOf()).toBe("POST");
    expect(bodyOf()).toEqual({ days: 7, minutes_per_day: 60, speed: 1.2 });
  });

  it("sends an empty body when replanning with defaults", () => {
    buildPlan("g1");
    expect(bodyOf()).toEqual({});
  });

  it("uses the catch-up route", () => {
    catchUpPlan("g1", { days: 5 });
    expect(pathOf()).toBe("/learning/goals/g1/plan/catch-up");
  });

  it("patches a single item", () => {
    updatePlanItem("g1", "i1", { status: "done" });
    expect(methodOf()).toBe("PATCH");
    expect(pathOf()).toBe("/learning/goals/g1/plan/i1");
    expect(bodyOf()).toEqual({ status: "done" });
  });
});

describe("daily session", () => {
  it("prepares the session with a POST", () => {
    prepareDailySession("g1");
    expect(pathOf()).toBe("/learning/goals/g1/daily/prepare");
    expect(methodOf()).toBe("POST");
  });

  it("completes the named topics on the scheduled day", () => {
    completeDailySession("g1", {
      day: "2026-10-25",
      topic_ids: ["t1", "t2"],
    });
    expect(pathOf()).toBe("/learning/goals/g1/daily/complete");
    expect(methodOf()).toBe("POST");
    expect(bodyOf()).toEqual({
      day: "2026-10-25",
      topic_ids: ["t1", "t2"],
    });
  });
});

describe("allocation", () => {
  it("repeats goal_id for each goal", () => {
    getAllocation(["a", "b"], 120);
    const path = pathOf();
    expect(path).toContain("goal_id=a");
    expect(path).toContain("goal_id=b");
    expect(path).toContain("minutes_per_day=120");
  });

  it("escapes ids containing reserved characters", () => {
    getAllocation(["a b", "c/d"], 60);
    const path = pathOf();
    expect(path).toContain("goal_id=a%20b");
    expect(path).toContain("goal_id=c%2Fd");
  });
});

describe("diagnostic", () => {
  it("starts with a reply budget", () => {
    startDiagnostic("g1", { max_replies: 6 });
    expect(pathOf()).toBe("/learning/goals/g1/diagnostic/start");
    expect(bodyOf()).toEqual({ max_replies: 6 });
  });

  it("posts a reply to the session", () => {
    replyToDiagnostic("g1", "s1", "мой ответ");
    expect(pathOf()).toBe("/learning/goals/g1/diagnostic/s1/reply");
    expect(bodyOf()).toEqual({ text: "мой ответ" });
  });

  it("reads the session state", () => {
    getDiagnosticState("g1", "s1");
    expect(pathOf()).toBe("/learning/goals/g1/diagnostic/s1");
  });

  it("patches the estimates", () => {
    correctDiagnostic("g1", "s1", { t1: 0.9 });
    expect(methodOf()).toBe("PATCH");
    expect(bodyOf()).toEqual({ estimates: { t1: 0.9 } });
  });
});

describe("questions and answers", () => {
  it("submits confidence, hints and timing", () => {
    submitAnswer("q1", {
      text: "ответ",
      confidence: "guessing",
      hints_used: 2,
      answer_time_ms: 5000,
    });
    expect(pathOf()).toBe("/learning/questions/q1/answer");
    expect(bodyOf()).toEqual({
      text: "ответ",
      confidence: "guessing",
      hints_used: 2,
      answer_time_ms: 5000,
      language: "ru",
    });
  });

  it("asks for a coding task in the interface language", async () => {
    await generateQuestion("t1", { format: "code" });
    expect(pathOf()).toBe("/learning/topics/t1/question");
    expect(bodyOf()).toEqual({ language: "ru", format: "code" });
  });

  it("fetches the review question via POST", () => {
    getNextReviewQuestion("g1");
    expect(methodOf()).toBe("POST");
    expect(pathOf()).toBe("/learning/goals/g1/review");
  });

  it("posts the exam composition request", () => {
    planExam("g1");
    expect(pathOf()).toBe("/learning/goals/g1/exam");
    expect(methodOf()).toBe("POST");
  });
});

describe("progress and history", () => {
  it("reads progress", () => {
    getProgress("g1");
    expect(pathOf()).toBe("/learning/goals/g1/progress");
  });

  it("serialises history filters into the query string", () => {
    getAnswerHistory("g1", {
      topic_id: "t1",
      min_score: 0.5,
      error_type: "incomplete",
      limit: 10,
    });
    const path = pathOf();
    expect(path).toContain("topic_id=t1");
    expect(path).toContain("min_score=0.5");
    expect(path).toContain("error_type=incomplete");
    expect(path).toContain("limit=10");
  });

  it("omits the query string when no filters are given", () => {
    getAnswerHistory("g1");
    expect(pathOf()).toBe("/learning/goals/g1/answers");
  });

  it("skips undefined filters but keeps false", () => {
    getAnswerHistory("g1", { min_score: undefined, disputed: false });
    const path = pathOf();
    expect(path).not.toContain("min_score");
    expect(path).toContain("disputed=false");
  });
});

describe("export", () => {
  it("falls back to a default filename", async () => {
    requestBlobMock.mockResolvedValue({
      blob: new Blob(["data"]),
      fileName: null,
      contentType: "text/calendar",
    });
    const { fileName } = await downloadPlanIcs("g1");
    expect(fileName).toBe("opentutor-plan-g1.ics");
    expect(requestBlobMock.mock.calls[0][0]).toBe("/learning/goals/g1/export.ics");
  });

  it("uses the filename the server suggests", async () => {
    requestBlobMock.mockResolvedValue({
      blob: new Blob(["data"]),
      fileName: "plan.ics",
      contentType: "text/calendar",
    });
    const { fileName } = await downloadPlanIcs("g1");
    expect(fileName).toBe("plan.ics");
  });
});

describe("profile and graph", () => {
  it("reads and patches the profile", () => {
    getProfile();
    expect(pathOf()).toBe("/learning/profile");

    updateProfile({ quiet_hours_start: 23 });
    expect(methodOf(1)).toBe("PATCH");
    expect(bodyOf(1)).toEqual({ quiet_hours_start: 23 });
  });

  it("reads the dependency graph", () => {
    getTopicGraph("g1");
    expect(pathOf()).toBe("/learning/goals/g1/graph");
  });
});