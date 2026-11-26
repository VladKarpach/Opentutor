import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent } from "@testing-library/react";
import { render, screen, waitFor } from "@/test-utils";

import { CourseGoalsPanel } from "./course-goals-panel";

/**
 * Goals have no top-level nav entry any more — the course is where a goal belongs
 * and where it is reached from. That makes this panel the only way into the full
 * goal list, so the link has to exist and has to be shown only when there is
 * something to list.
 */

const mockPush = vi.fn();
const listCourseGoals = vi.fn();
const createGoal = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

vi.mock("@/lib/api/learning", () => ({
  listCourseGoals: (...a: unknown[]) => listCourseGoals(...a),
  createGoal: (...a: unknown[]) => createGoal(...a),
  attachGoalToCourse: vi.fn(),
  deleteGoal: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const goal = (id: string, topic: string, over: Record<string, unknown> = {}) => ({
  id,
  topic,
  status: "active",
  depth: "confident",
  topic_count: 4,
  topics_done: 0,
  progress_percent: 0,
  exam_status: "none",
  exam_score: null,
  exam_passed: false,
  current_topic_title: null,
  minutes_per_day: 45,
  days_left: 10,
  clarification_needed: false,
  created_at: "2026-01-05T00:00:00Z",
  completed_at: null,
  course_id: "course-1",
  course_name: "Алгебра",
  ...over,
});

// The panel reads data.goals, so the mock has to be the envelope, not a bare array.
const courseGoals = (goals: unknown[]) => ({ goals, count: goals.length });

describe("CourseGoalsPanel entry to the goal list", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listCourseGoals.mockResolvedValue(courseGoals([goal("g1", "Метод Гаусса")]));
    createGoal.mockResolvedValue(goal("g2", "Новая тема"));
  });

  it("links to the full goal list when the course has goals", async () => {
    render(<CourseGoalsPanel courseId="course-1" courseName="Алгебра" />);
    await screen.findByText("Метод Гаусса");
    fireEvent.click(screen.getByRole("button", { name: "Все цели" }));
    expect(mockPush).toHaveBeenCalledWith("/learning");
  });

  it("hides the link when the course has no goals", async () => {
    listCourseGoals.mockResolvedValue(courseGoals([]));
    render(<CourseGoalsPanel courseId="course-1" courseName="Алгебра" />);
    await waitFor(() => expect(listCourseGoals).toHaveBeenCalled());
    // With nothing to list, the link would land on an empty page.
    expect(screen.queryByRole("button", { name: "Все цели" })).toBeNull();
  });

  it("keeps creating a goal in the course", async () => {
    render(<CourseGoalsPanel courseId="course-1" courseName="Алгебра" />);
    await screen.findByText("Метод Гаусса");
    fireEvent.click(screen.getByRole("button", { name: "Новая цель" }));
    expect(screen.getByLabelText("Тема новой цели")).toBeTruthy();
  });

  it("still opens a goal directly", async () => {
    render(<CourseGoalsPanel courseId="course-1" courseName="Алгебра" />);
    // The row is covered by an absolutely positioned link, so the title itself is
    // a sibling span rather than the anchor text.
    const link = await screen.findByRole("link", {
      name: 'Открыть цель «Метод Гаусса»',
    });
    expect(link.getAttribute("href")).toBe("/learning/goals/g1");
  });
});

describe("CourseGoalsPanel progress", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createGoal.mockResolvedValue(goal("g2", "Новая тема"));
  });

  it("shows the topic count, not a progress bar", async () => {
    // Progress was asked to be removed: the row states what there is, not how far
    // along it is.
    listCourseGoals.mockResolvedValue(
      courseGoals([
        goal("g1", "Метод Гаусса", {
          topic_count: 10,
          topics_done: 5,
          progress_percent: 50,
          current_topic_title: "Обратный ход метода Гаусса",
        }),
      ]),
    );
    render(<CourseGoalsPanel courseId="course-1" courseName="Алгебра" />);
    expect(await screen.findByText("тем: 10")).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/^сейчас:/)).toBeNull();
  });

  it("marks a passed exam", async () => {
    listCourseGoals.mockResolvedValue(
      courseGoals([
        goal("g1", "Метод Гаусса", {
          topic_count: 10,
          exam_passed: true,
          exam_status: "completed",
          exam_score: 0.82,
        }),
      ]),
    );
    render(<CourseGoalsPanel courseId="course-1" courseName="Алгебра" />);
    expect(await screen.findByText("экзамен сдан")).toBeTruthy();
  });

  it("says a goal has no topics built", async () => {
    listCourseGoals.mockResolvedValue(
      courseGoals([goal("g1", "Метод Гаусса", { topic_count: 0 })]),
    );
    render(<CourseGoalsPanel courseId="course-1" courseName="Алгебра" />);
    expect(await screen.findByText("темы не построены")).toBeTruthy();
  });
});