import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

import { render, screen } from "@/test-utils";
import { LocaleProvider } from "@/lib/i18n-context";

import { ComprehensionCheck } from "./comprehension-check";
import type {
  ComprehensionLatest,
  ComprehensionTurn,
} from "@/lib/api/learning";

/**
 * The comprehension card.
 *
 * What is under test is the shape of the dialogue: a free-recall answer gets a
 * diagnosis and a focused re-teach, not a generic grade; the check question then
 * ends with a summary and the three actions. The model is stubbed — the card
 * only renders the verdict the server computed.
 */

const { startComprehension, answerComprehension, sendMessage, requestChatOpen } =
  vi.hoisted(() => ({
    startComprehension: vi.fn(),
    answerComprehension: vi.fn(),
    sendMessage: vi.fn(),
    requestChatOpen: vi.fn(),
  }));

vi.mock("@/lib/api/learning", () => ({
  startComprehension,
  answerComprehension,
}));

vi.mock("@/lib/chat-events", () => ({ requestChatOpen }));

vi.mock("@/store/chat", () => ({
  useChatStore: (selector: (state: { sendMessage: typeof sendMessage }) => unknown) =>
    selector({ sendMessage }),
}));

const START = {
  session_id: "s1",
  topic_id: "t1",
  status: "in_progress",
  step: 0,
  max_steps: 4,
  question:
    "Объясни своими словами главную идею темы «Предел функции». 3–5 предложений.",
  resumed: false,
};

const RETEACH_TURN: ComprehensionTurn = {
  session_id: "s1",
  topic_id: "t1",
  status: "in_progress",
  step: 1,
  max_steps: 4,
  score: 0.57,
  verdict: {
    summary: "Ты понял идею приближения, но смешиваешь предел со значением в точке.",
    score: 0.57,
    units: [
      {
        id: "u1",
        label: "Предел — это поведение рядом с точкой",
        status: "understood",
        evidence: "функция приближается к числу",
        why: "Верно.",
        source_quote: "предел — это про поведение рядом с точкой",
      },
      {
        id: "u2",
        label: "Разрыв и предел",
        status: "misconception",
        evidence: "если функции нет в точке, то и предела нет",
        why: "Смешаны значение и поведение рядом с точкой.",
        source_quote: "функция может быть не определена там, но предел существует",
      },
    ],
    gaps: [
      {
        unit_id: "u2",
        label: "Разрыв и предел",
        kind: "misconception",
        why: "Значение в точке и предел — разные вещи.",
        evidence: "если функции нет в точке, то и предела нет",
        source_quote: "функция может быть не определена там, но предел существует",
        consequence: "Предел у (x²-1)/(x-1) при x → 1 был бы объявлен несуществующим.",
      },
    ],
    target_unit_id: "u2",
    target_status: "misconception",
    next_review_at: null,
    interval_days: null,
  },
  next: {
    kind: "reteach",
    question: "Может ли предел существовать там, где функция не определена?",
    reteach: {
      unit_id: "u2",
      title: "Значение в точке и предел",
      why_wrong: "Ты считаешь, что отсутствие значения отменяет предел.",
      explanation:
        "Предел отвечает на вопрос, к какому числу приближается функция. " +
        "Значение в самой точке — отдельный вопрос. У f(x) = (x² - 1)/(x - 1) " +
        "при x = 1 значения нет, но рядом функция приближается к 2.",
      example: "Предел равен 2, хотя f(1) не существует.",
      source_quote: "функция может быть не определена там, но предел существует",
    },
  },
};

const FINAL_TURN: ComprehensionTurn = {
  session_id: "s1",
  topic_id: "t1",
  status: "passed",
  step: 2,
  max_steps: 4,
  score: 1,
  verdict: {
    summary: "Теперь верно: предел и значение в точке больше не смешиваются.",
    score: 1,
    units: [
      {
        id: "u1",
        label: "Предел — это поведение рядом с точкой",
        status: "understood",
        evidence: "функция приближается к числу",
        why: "Верно.",
        source_quote: "предел — это про поведение рядом с точкой",
      },
      {
        id: "u2",
        label: "Разрыв и предел",
        status: "understood",
        evidence: "функция может не иметь значения, но предел есть",
        why: "Теперь различает.",
        source_quote: "функция может быть не определена там, но предел существует",
      },
    ],
    gaps: [],
    target_unit_id: "u2",
    target_status: "understood",
    next_review_at: "2026-10-22T10:00:00+00:00",
    interval_days: 2,
  },
  next: { kind: "done", question: null, reteach: null },
};

const LATEST: ComprehensionLatest = {
  checked: true,
  has_explanation: true,
  session_id: "s0",
  status: "passed",
  score: 0.8,
  summary: "Понято главное, осталась одна деталь.",
  gaps: [
    {
      unit_id: "u2",
      label: "Разрыв и предел",
      kind: "partial",
      why: "Не хватает связи.",
      evidence: "не уверен",
      source_quote: "",
      consequence: "",
    },
  ],
  next_review_at: "2026-10-22T10:00:00+00:00",
  interval_days: 2,
  completed_at: "2026-10-20T10:00:00+00:00",
};

function renderCard(overrides: Partial<Parameters<typeof ComprehensionCheck>[0]> = {}) {
  return render(
    <LocaleProvider>
      <ComprehensionCheck
        topicId="t1"
        topicTitle="Предел функции"
        goalId="g1"
        courseId="c1"
        hasExplanation
        latest={null}
        {...overrides}
      />
    </LocaleProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

// The dialogue tests type two full answers and wait for an animated turn. On a
// loaded machine that can take longer than Vitest's 5s default, which would tear
// the component down mid-test and look like a missing button.
describe("ComprehensionCheck", () => {
  it("stays disabled until an explanation exists", () => {
    renderCard({ hasExplanation: false });

    expect(screen.getByTestId("comprehension-start")).toBeDisabled();
    expect(
      screen.getByText("Сначала прочитайте объяснение темы — проверять пока нечего."),
    ).toBeInTheDocument();
  });

  it("turns a free-recall answer into a focused re-teach and then a final summary", async () => {
    startComprehension.mockResolvedValue(START);
    answerComprehension
      .mockResolvedValueOnce(RETEACH_TURN)
      .mockResolvedValueOnce(FINAL_TURN);
    const onLatestChange = vi.fn();
    const { user } = renderCard({ onLatestChange });

    await user.click(screen.getByTestId("comprehension-start"));
    await waitFor(() =>
      expect(screen.getByText(/Объясни своими словами/)).toBeInTheDocument(),
    );

    await user.type(
      screen.getByTestId("comprehension-answer"),
      "Предел показывает, к какому значению функция приближается. " +
        "Но если функции нет в точке, то и предела нет.",
    );
    await user.click(screen.getByTestId("comprehension-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("comprehension-verdict")).toBeInTheDocument(),
    );
    expect(screen.getByText("Что понято верно")).toBeInTheDocument();
    expect(screen.getByText("Где расхождение")).toBeInTheDocument();
    expect(screen.getByTestId("comprehension-reteach")).toBeInTheDocument();
    expect(screen.getByText("Разберём только это")).toBeInTheDocument();
    expect(screen.getByText("Как на самом деле:")).toBeInTheDocument();
    expect(
      screen.getByText(/Предел у \(x²-1\)\/\(x-1\)/),
    ).toBeInTheDocument();

    await user.type(
      screen.getByTestId("comprehension-answer"),
      "Да: функция может не иметь значения в точке, но предел существует.",
    );
    await user.click(screen.getByTestId("comprehension-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("comprehension-final")).toBeInTheDocument(),
    );
    expect(screen.getByText("Понимание: 100%")).toBeInTheDocument();
    expect(screen.getByText("Теперь верно")).toBeInTheDocument();
    expect(screen.getByText("Вернёмся к этому через 2 дня.")).toBeInTheDocument();
    expect(screen.getByText("К практике")).toBeInTheDocument();
    expect(screen.getByText("Спросить ИИ")).toBeInTheDocument();
    expect(screen.getByText("Пройти ещё раз")).toBeInTheDocument();
    expect(onLatestChange).toHaveBeenCalledWith(
      expect.objectContaining({ checked: true, score: 1, status: "passed" }),
    );

    expect(startComprehension).toHaveBeenCalledTimes(1);
    expect(answerComprehension).toHaveBeenCalledTimes(2);
  });

  it("shows the last score and offers another run", async () => {
    startComprehension.mockResolvedValue(START);
    const { user } = renderCard({ latest: LATEST });

    expect(screen.getByText("Понимание: 80%")).toBeInTheDocument();
    expect(screen.getByText("Осталось разобраться: Разрыв и предел.")).toBeInTheDocument();

    await user.click(screen.getByTestId("comprehension-start"));
    await waitFor(() => expect(startComprehension).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByText(/Объясни своими словами/)).toBeInTheDocument(),
    );
  });

  it("explains a saved weak spot instead of only naming it", () => {
    renderCard({ latest: LATEST });

    // The label alone told the learner nothing about what to revisit; the saved
    // result now carries the diagnosis as well.
    expect(screen.getByText(/Почему это ошибка/)).toBeInTheDocument();
    expect(screen.getByText(/Не хватает связи/)).toBeInTheDocument();
  });

  it("asks the AI about the weak spot from the final summary", async () => {
    startComprehension.mockResolvedValue(START);
    answerComprehension.mockResolvedValueOnce(FINAL_TURN);
    const { user } = renderCard();

    await user.click(screen.getByTestId("comprehension-start"));
    await waitFor(() =>
      expect(screen.getByText(/Объясни своими словами/)).toBeInTheDocument(),
    );
    await user.type(screen.getByTestId("comprehension-answer"), "Предел — это про приближение.");
    await user.click(screen.getByTestId("comprehension-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("comprehension-final")).toBeInTheDocument(),
    );
    await user.click(screen.getByText("Спросить ИИ"));

    expect(requestChatOpen).toHaveBeenCalledWith("c1");
    expect(sendMessage).toHaveBeenCalledWith(
      "c1",
      expect.stringContaining("Предел функции"),
    );
  });
}, 20000);
