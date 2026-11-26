import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent } from "@testing-library/react";
import { render, screen, waitFor } from "@/test-utils";

/**
 * The home page is the single entry point now.
 *
 * It used to stack a dozen dashboard panels over the course list, which made it
 * read as a different product from /learning — both showed courses, and the panel
 * the planner is actually built from sat eighth. These tests lock the new shape so
 * the removed panels cannot quietly come back, and so the learner picture stays on
 * the page at all.
 */

const mockPush = vi.fn();
const mockReplace = vi.fn();
const getLearnerContext = vi.fn();
// The goal index is what /learning renders from. Watching it tells us whether the
// home page duplicated the goal list, which a text search cannot: the sidebar
// legitimately links to /learning, so "Все цели" is present either way.
const getLearningIndex = vi.fn();

const COURSES = [
  {
    id: "course-1",
    name: "Линейная алгебра",
    description: "Метод Гаусса и разложения",
    created_at: "2026-01-05T00:00:00Z",
    updated_at: "2026-01-05T00:00:00Z",
  },
];

let courseState = { courses: COURSES as unknown[], loading: false, error: null };

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => key,
  useTF: () => (key: string) => key,
  useLocale: () => ({ locale: "ru", setLocale: vi.fn() }),
}));

vi.mock("@/store/course", () => ({
  useCourseStore: Object.assign(
    () => courseState,
    { getState: () => ({ fetchCourses: vi.fn().mockResolvedValue(undefined) }) },
  ),
}));

vi.mock("@/lib/api", () => ({
  getHealthStatus: vi.fn().mockResolvedValue({ deployment_mode: "single_user" }),
}));

vi.mock("@/lib/api/learning", () => ({
  getLearnerContext: (...args: unknown[]) => getLearnerContext(...args),
  getLearningIndex: (...args: unknown[]) => getLearningIndex(...args),
}));

vi.mock("@/lib/study-notifications", () => ({
  initStudyNotifications: () => () => {},
}));

vi.mock("@/lib/cache", () => ({
  ttlCache: { get: () => null, set: vi.fn() },
}));

// The drawer pulls in the whole chat stack; the drawer itself is the course
// page's job. Here only the presence of the entry point matters.
vi.mock("@/components/chat/chat-drawer", () => ({
  ChatDrawer: () => <div data-testid="chat-drawer" />,
}));
vi.mock("@/components/chat/chat-fab", () => ({
  ChatFab: ({ open }: { open: boolean }) => (
    <button type="button" data-testid="chat-fab" aria-expanded={open}>
      chat
    </button>
  ),
}));

async function renderHome() {
  const DashboardPage = (await import("./page")).default;
  return render(<DashboardPage />);
}

describe("home page structure", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // The setup redirect keys off this flag, and jsdom keeps localStorage between
    // tests in a file, so an earlier test would suppress a later one's redirect.
    window.localStorage.clear();
    courseState = { courses: COURSES as unknown[], loading: false, error: null };
    getLearnerContext.mockResolvedValue({
      goals_seen: 2,
      answers_total: 12,
      known: ["Метод Гаусса"],
      in_progress: ["Определители"],
      weak: ["Собственные значения"],
      error_profile: {},
      summary: "сильное: метод Гаусса",
    });
  });

  it("leads with what the system knows about the learner", async () => {
    await renderHome();
    expect(await screen.findByText("Что уже известно о вас")).toBeTruthy();
  });

  it("offers a way to ask the AI", async () => {
    await renderHome();
    expect(await screen.findByText("Спросить у ИИ")).toBeTruthy();
    expect(screen.getByText("Задать вопрос")).toBeTruthy();
  });

  it("mounts the chat drawer for the single course", async () => {
    await renderHome();
    await waitFor(() => expect(screen.getByTestId("chat-drawer")).toBeTruthy());
  });

  it("creates a goal, not just a course", async () => {
    await renderHome();
    const goalButton = await screen.findByText("Новая цель");
    goalButton.click();
    expect(mockPush).toHaveBeenCalledWith("/learning/new");
  });

  it("lists courses so they can be opened", async () => {
    await renderHome();
    const course = await screen.findByText("Линейная алгебра");
    course.closest("button")?.click();
    expect(mockPush).toHaveBeenCalledWith("/course/course-1");
  });

  it("does not repeat the goal list — that lives at /learning", async () => {
    // Goals are grouped by course on /learning only. Showing them here too was
    // one of the two duplicate course lists. The check is on the data, not on the
    // presence of a word.
    await renderHome();
    await screen.findByText("Что уже известно о вас");
    expect(getLearningIndex).not.toHaveBeenCalled();
  });

  it("offers no goals entry in the sidebar", async () => {
    // A goal belongs to a course, so the way in is the course. A top-level menu
    // item sat next to the courses it duplicated.
    await renderHome();
    await screen.findByText("Что уже известно о вас");
    expect(screen.queryByRole("button", { name: "Все цели" })).toBeNull();
    expect(mockPush).not.toHaveBeenCalledWith("/learning");
  });

  it("still offers settings in the sidebar", async () => {
    await renderHome();
    await screen.findByText("Что уже известно о вас");
    fireEvent.click(screen.getByRole("button", { name: "nav.settings" }));
    expect(mockPush).toHaveBeenCalledWith("/settings");
  });

  it.each([
    "Недельная статистика",
    "Мастерство по курсам",
    "Плотность знаний",
    "Инсайты агента",
    "Ждут утверждения",
    "Ритм обучения",
  ])("no longer renders the %s panel", async (panel) => {
    await renderHome();
    await screen.findByText("Что уже известно о вас");
    expect(screen.queryByText(panel)).toBeNull();
  });

  it("does not bounce the learner into a course page", async () => {
    // One course and no goals used to redirect away from the home page, which
    // made the same address show different things depending on state.
    await renderHome();
    await waitFor(() => expect(getLearnerContext).toHaveBeenCalled());
    expect(mockReplace).not.toHaveBeenCalledWith("/setup");
  });

  it("hides the AI block when there is no subject to ask about", async () => {
    courseState = { courses: [], loading: false, error: null };
    const { unmount } = await renderHome();
    unmount();
    await renderHome();
    await waitFor(() => expect(mockReplace).toHaveBeenCalled());
    expect(screen.queryByText("Спросить у ИИ")).toBeNull();
  });
});

describe("home page with several courses", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    courseState = {
      courses: [
        { id: "course-1", name: "Алгебра", created_at: "2026-01-05T00:00:00Z" },
        { id: "course-2", name: "Анализ", created_at: "2026-01-05T00:00:00Z" },
      ] as unknown[],
      loading: false,
      error: null,
    };
    getLearnerContext.mockResolvedValue({
      goals_seen: 1,
      answers_total: 4,
      known: [],
      in_progress: [],
      weak: [],
      error_profile: {},
      summary: "",
    });
  });

  it("asks which subject the question is about", async () => {
    await renderHome();
    await screen.findByText("Спросить у ИИ");
    // Chat is course-scoped, so with more than one course the learner has to
    // choose rather than the page guessing. The name also appears on the course
    // card, so the chip is selected by role.
    const ask = screen.getByText("Задать вопрос") as HTMLButtonElement;
    expect(ask.disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Анализ" })).toBeTruthy();
  });

  it("enables asking once a subject is chosen", async () => {
    await renderHome();
    await screen.findByText("Спросить у ИИ");
    fireEvent.click(screen.getByRole("button", { name: "Анализ" }));
    const ask = screen.getByText("Задать вопрос") as HTMLButtonElement;
    await waitFor(() => expect(ask.disabled).toBe(false));
  });
});