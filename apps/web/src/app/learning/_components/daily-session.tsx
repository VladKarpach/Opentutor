"use client";

/**
 * The guided daily session.
 *
 * The interesting part is not the three cards — it is the absence of choices.
 * The learner opens this page and sees: warm up on this topic, read this
 * explanation, solve this task. The backend has already decided what belongs to
 * today from the plan and the topic states.
 *
 * Progress is kept in localStorage per goal and day, so a refresh does not send
 * the learner back to the warm-up. Nothing else is persisted here: completing
 * the session is a server call that marks the finished topics' plan items done.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  BookOpen,
  Clock3,
  GraduationCap,
  ListChecks,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import { MarkdownRenderer } from "@/components/shared/markdown-renderer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  completeDailySession,
  explainTopic,
  prepareDailySession,
  type AnswerResult,
  type DailySession as DailySessionData,
  type DailyStep,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";

import { PracticePanel } from "./practice-panel";

const STEP_META: Record<
  string,
  { label: string; short: string; icon: typeof RotateCcw }
> = {
  review: { label: "Разминка", short: "Повторение", icon: RotateCcw },
  theory: { label: "Теория", short: "Теория", icon: BookOpen },
  practice: { label: "Практика", short: "Практика", icon: Sparkles },
  exam_prep: {
    label: "Практика для допуска",
    short: "Задания",
    icon: ListChecks,
  },
  exam: { label: "Экзамен", short: "Экзамен", icon: GraduationCap },
};

function progressKey(goalId: string, day: string): string {
  return `opentutor:daily:${goalId}:${day}`;
}

function readProgress(goalId: string, day: string): number | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(progressKey(goalId, day));
    if (!raw) return null;
    const value = Number.parseInt(raw, 10);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

function writeProgress(goalId: string, day: string, value: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(progressKey(goalId, day), String(value));
  } catch {
    // Private mode or a blocked storage API must not break the lesson.
  }
}

type PracticeScores = Record<string, number[]>;

function practiceKey(goalId: string, day: string): string {
  return `opentutor:daily:${goalId}:${day}:scores`;
}

function readPracticeScores(goalId: string, day: string): PracticeScores {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(practiceKey(goalId, day));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    const scores: PracticeScores = {};
    for (const [topicId, value] of Object.entries(parsed)) {
      if (!Array.isArray(value)) continue;
      const valid = value.filter(
        (item): item is number =>
          typeof item === "number" && Number.isFinite(item),
      );
      if (valid.length > 0) scores[topicId] = valid;
    }
    return scores;
  } catch {
    return {};
  }
}

function writePracticeScores(
  goalId: string,
  day: string,
  scores: PracticeScores,
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(practiceKey(goalId, day), JSON.stringify(scores));
  } catch {
    // The lesson must keep working when storage is unavailable.
  }
}

function creditedKey(goalId: string, day: string): string {
  return `opentutor:daily:${goalId}:${day}:credited`;
}

function readCreditedPractice(goalId: string, day: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(creditedKey(goalId, day));
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(
      parsed.filter((item): item is string => typeof item === "string"),
    );
  } catch {
    return new Set();
  }
}

function writeCreditedPractice(
  goalId: string,
  day: string,
  topics: Set<string>,
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      creditedKey(goalId, day),
      JSON.stringify([...topics]),
    );
  } catch {
    // The lesson must keep working when storage is unavailable.
  }
}

function formatDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

function scoreTone(score: number): string {
  if (score >= 0.8) return "text-success";
  if (score >= 0.5) return "text-warning";
  return "text-destructive";
}

export function DailySessionView({
  goalId,
  goalTitle,
  fresh = false,
  initialSession,
  embedded = false,
  onCompleted,
}: {
  goalId?: string;
  goalTitle?: string;
  /** Ignore the saved position, e.g. after taking a future day early. */
  fresh?: boolean;
  /** A session already prepared by the multi-goal "Сегодня" agenda. */
  initialSession?: DailySessionData;
  /** Render as one block inside the agenda instead of a full page. */
  embedded?: boolean;
  /** Called after this goal's session is closed, so the agenda can count it. */
  onCompleted?: (goalId: string) => void;
}) {
  const [session, setSession] = useState<DailySessionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const [finished, setFinished] = useState(false);
  const [restored, setRestored] = useState(false);
  const [completing, setCompleting] = useState(false);
  const scopeGoalId = session?.goal_id ?? initialSession?.goal_id ?? goalId ?? "";

  const [reviewText, setReviewText] = useState("");
  const [showReviewTheory, setShowReviewTheory] = useState(false);

  const theoryCache = useRef<Record<string, string>>({});
  const [theoryText, setTheoryText] = useState("");
  const [theoryLoading, setTheoryLoading] = useState(false);
  const [theoryError, setTheoryError] = useState<string | null>(null);
  const [theoryAttempted, setTheoryAttempted] = useState(false);

  const [practiceScores, setPracticeScores] = useState<PracticeScores>({});
  // A drill can already be finished from an earlier visit. That is a pass for
  // today even though this session has no fresh scores to average.
  const [creditedPractice, setCreditedPractice] = useState<Set<string>>(
    new Set(),
  );

  const practiceScoreFor = (topicId: string): number | null => {
    const scores = practiceScores[topicId] ?? [];
    if (scores.length === 0) return null;
    return scores.reduce((sum, value) => sum + value, 0) / scores.length;
  };
  const practiceCreditedFor = (topicId: string): boolean =>
    creditedPractice.has(topicId);
  const practiceAnsweredFor = (topicId: string): boolean =>
    (practiceScores[topicId]?.length ?? 0) > 0 ||
    practiceCreditedFor(topicId);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRestored(false);
    try {
      const data =
        initialSession ??
        (goalId ? await prepareDailySession(goalId) : null);
      if (!data) {
        throw new Error("Не выбрана цель для занятия.");
      }
      setSession(data);
      const steps = data.steps ?? [];
      const scope = data.goal_id;
      // Practice scores are the one piece of the session the backend does not
      // keep per day. Persisting them locally means a refresh after the last
      // task still knows whether the topic passed. A fresh pull starts clean.
      const savedScores: PracticeScores = fresh
        ? {}
        : readPracticeScores(scope, data.date);
      setPracticeScores(savedScores);
      setCreditedPractice(
        fresh ? new Set<string>() : readCreditedPractice(scope, data.date),
      );
      if (data.completed || steps.length === 0) {
        setFinished(true);
        setActive(0);
        writeProgress(scope, data.date, 0);
        onCompleted?.(scope);
      } else {
        const saved = fresh ? null : readProgress(scope, data.date);
        if (saved !== null && saved >= 0) {
          const bounded = Math.min(saved, steps.length);
          setActive(bounded === steps.length ? steps.length - 1 : bounded);
          setFinished(bounded === steps.length);
        } else {
          setActive(0);
          setFinished(false);
        }
      }
      setRestored(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Не удалось собрать занятие на сегодня.",
      );
    } finally {
      setLoading(false);
    }
  }, [goalId, fresh, initialSession, onCompleted]);

  useEffect(() => {
    void load();
  }, [load]);

  const steps = session?.steps ?? [];
  const step = steps[active] ?? null;
  const currentMeta = step ? STEP_META[step.kind] ?? STEP_META.theory : null;
  const isLast = steps.length > 0 && active >= steps.length - 1;

  // Persist progress only after the saved position has been restored, otherwise
  // the first render would overwrite yesterday's marker with zero.
  useEffect(() => {
    if (!session || !restored || !scopeGoalId) return;
    const value = finished ? steps.length : active;
    writeProgress(scopeGoalId, session.date, value);
  }, [session, restored, finished, active, steps.length, scopeGoalId]);

  // A different theory step starts from its saved explanation, or from a text
  // cached earlier in this visit. The cache matters when the learner goes back
  // from practice to re-read.
  useEffect(() => {
    if (!step || step.kind !== "theory" || !step.topic_id) return;
    const topicId = step.topic_id;
    const cached = theoryCache.current[topicId];
    setTheoryText(step.theory ?? cached ?? "");
    setTheoryError(null);
    setTheoryAttempted(false);
    setTheoryLoading(false);
  }, [step]);

  useEffect(() => {
    if (!step || step.kind !== "theory" || !step.topic_id || step.theory) return;
    const topicId = step.topic_id;
    if (theoryAttempted || theoryCache.current[topicId]) return;
    setTheoryAttempted(true);

    let cancelled = false;
    setTheoryLoading(true);
    setTheoryError(null);
    explainTopic(topicId, "short")
      .then((result) => {
        if (cancelled) return;
        theoryCache.current[topicId] = result.text;
        setTheoryText(result.text);
      })
      .catch((err) => {
        if (cancelled) return;
        setTheoryError(
          err instanceof Error
            ? err.message
            : "Не удалось подготовить объяснение.",
        );
      })
      .finally(() => {
        if (cancelled) return;
        setTheoryLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [step, theoryAttempted]);

  function retryTheory() {
    setTheoryError(null);
    setTheoryAttempted(false);
    setTheoryLoading(false);
  }

  function next() {
    if (isLast) {
      void finish();
      return;
    }
    setActive((value) => Math.min(value + 1, steps.length - 1));
  }

  function back() {
    setActive((value) => Math.max(0, value - 1));
  }

  function handlePracticeAnswer(topicId: string, result: AnswerResult) {
    // Scores are kept per topic: a day can contain two short lessons, and one
    // topic's correct answer must not hide another topic's failed practice.
    const next = {
      ...practiceScores,
      [topicId]: [...(practiceScores[topicId] ?? []), result.final_score],
    };
    setPracticeScores(next);
    if (session && scopeGoalId) {
      writePracticeScores(scopeGoalId, session.date, next);
    }
  }

  function handlePracticeCredited(topicId: string) {
    // The panel reports a finished series both when the learner just completed
    // it and when it was already complete before the session opened.
    setCreditedPractice((current) => {
      if (current.has(topicId)) return current;
      const next = new Set(current).add(topicId);
      if (session && scopeGoalId) {
        writeCreditedPractice(scopeGoalId, session.date, next);
      }
      return next;
    });
  }

  async function finish() {
    if (!session || completing) return;
    setCompleting(true);

    // Do not mark a topic done when its practice failed or was skipped. Leaving
    // it pending is what makes tomorrow repeat the topic and move the plan.
    const failedTopics = new Set<string>();
    for (const practiceStep of steps.filter(
      (item) => item.kind === "practice" && item.topic_id,
    )) {
      const topicId = practiceStep.topic_id as string;
      const score = practiceScoreFor(topicId);
      const credited = practiceCreditedFor(topicId);
      if (score === null ? !credited : score < 0.7) {
        failedTopics.add(topicId);
      }
    }
    const topicIds = Array.from(
      new Set(
        steps
          .filter(
            (item) =>
              item.kind !== "exam_prep" &&
              item.kind !== "exam" &&
              item.topic_id !== null,
          )
          .map((item) => item.topic_id as string)
          .filter((topicId) => !failedTopics.has(topicId)),
      ),
    );
    // The task-only day has no topic to name, so its plan row is closed by id.
    // The exam row is closed by the exam endpoint itself, once the exam ends.
    const itemIds = Array.from(
      new Set(
        steps
          .filter((item) => item.kind === "exam_prep" && item.item_id)
          .map((item) => item.item_id as string),
      ),
    );

    try {
      await completeDailySession(session.goal_id, {
        day: session.scheduled_for,
        topic_ids: topicIds,
        item_ids: itemIds,
      });
      setFinished(true);
      if (scopeGoalId) writeProgress(scopeGoalId, session.date, steps.length);
      onCompleted?.(session.goal_id);
    } catch (err) {
      toast.error("Не удалось сохранить итог занятия", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setCompleting(false);
    }
  }

  if (loading) {
    return (
      <div
        className={cn(
          embedded
            ? "space-y-3"
            : "mx-auto max-w-3xl space-y-4 px-4 py-10",
        )}
        aria-busy="true"
      >
        {!embedded && <div className="h-8 w-56 animate-pulse rounded-lg bg-muted" />}
        <div className="h-40 animate-pulse rounded-2xl bg-muted" />
        <div className="h-24 animate-pulse rounded-2xl bg-muted" />
      </div>
    );
  }

  if (error) {
    return (
      <div className={embedded ? "" : "mx-auto max-w-3xl px-4 py-10"}>
        <div className="card-shadow rounded-2xl border border-border bg-card p-6">
          <h1 className="text-lg font-semibold text-foreground">
            Не удалось собрать занятие
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">{error}</p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Button onClick={() => void load()}>Попробовать снова</Button>
            <Button asChild variant="outline">
              <Link href={scopeGoalId ? `/learning/goals/${scopeGoalId}` : "/learning"}>
                К цели
              </Link>
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (!session) return null;

  if (finished || session.completed || steps.length === 0) {
    const practiceResults = steps
      .filter((item) => item.kind === "practice" && item.topic_id)
      .map((item) => {
        const topicId = item.topic_id as string;
        return {
          score: practiceScoreFor(topicId),
          credited: practiceCreditedFor(topicId),
        };
      });
    const failedPractice = practiceResults.filter((item) =>
      item.score === null ? !item.credited : item.score < 0.7,
    );
    const passedPractice = practiceResults.filter((item) =>
      item.score === null ? item.credited : item.score >= 0.7,
    );
    const scoredPassed = passedPractice.filter(
      (item): item is { score: number; credited: boolean } =>
        item.score !== null,
    );
    const creditedOnly = passedPractice.filter((item) => item.score === null);
    const creditedNote =
      creditedOnly.length === 0
        ? ""
        : creditedOnly.length === 1
          ? " Ещё одна тема уже была зачтена ранее."
          : " Ещё несколько тем уже были зачтены ранее.";
    const noTopics = session.empty_reason === "no_topics";
    const goalHref = scopeGoalId ? `/learning/goals/${scopeGoalId}` : "/learning";
    const summary = (
      <>
        {session.shifted_days > 0 && (
          <div className="mt-4 rounded-xl border border-warning/30 bg-warning-muted/40 px-4 py-3 text-sm text-foreground">
            План сдвинут на {session.shifted_days} дн. Пропущенная тема
            осталась в расписании, а не потерялась.
          </div>
        )}

        {failedPractice.length > 0 && (
          <div className="mt-4 rounded-xl border border-warning/30 bg-warning-muted/40 px-4 py-3 text-sm text-foreground">
            {failedPractice.length === 1
              ? "Практика не зачтена, поэтому тема останется в плане. В следующем занятии она вернётся первой, и на повторение будет больше времени."
              : "Практика не зачтена по нескольким темам, поэтому они останутся в плане. В следующем занятии они вернутся первыми, и на повторение будет больше времени."}
          </div>
        )}

        {passedPractice.length > 0 && (
          <div className="mt-4 rounded-xl border border-success/30 bg-success-muted/40 px-4 py-3 text-sm text-foreground">
            {scoredPassed.length > 0
              ? scoredPassed.length === 1
                ? `Практика зачтена: ${Math.round(
                    scoredPassed[0].score * 100,
                  )}%.${creditedNote} Материал уйдёт в плановое повторение.`
                : `Практика зачтена по ${scoredPassed.length} темам.${creditedNote} Материал уйдёт в плановое повторение.`
              : "Практика уже была зачтена ранее. Материал уйдёт в плановое повторение."}
          </div>
        )}

        {session.next_session_at && (
          <p className="mt-4 text-xs text-muted-foreground">
            Следующее занятие: {formatDay(session.next_session_at)}.
          </p>
        )}
      </>
    );

    if (embedded) {
      return (
        <div className="rounded-2xl border border-success/30 bg-success-muted/25 p-5">
          <div className="flex items-center gap-2">
            <Sparkles className="size-5 text-success" aria-hidden="true" />
            <h3 className="text-sm font-semibold text-foreground">
              {noTopics ? "Пока нет тем" : "На сегодня всё"}
            </h3>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            {session.message ?? "Занятие закрыто. Следующее появится по плану."}
          </p>
          {summary}
          <Button asChild size="sm" variant="outline" className="mt-4">
            <Link href={goalHref}>К цели</Link>
          </Button>
        </div>
      );
    }

    return (
      <div className="min-h-screen bg-background">
        <div className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
          <Link
            href={goalHref}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← К цели
          </Link>

          <div className="card-shadow mt-4 rounded-2xl border border-border bg-card p-7">
            <div className="flex size-11 items-center justify-center rounded-full bg-success-muted text-success">
              <Sparkles className="size-5" aria-hidden="true" />
            </div>
            <h1 className="mt-4 text-2xl font-bold tracking-tight text-foreground">
              {noTopics ? "Пока нет тем" : "На сегодня всё"}
            </h1>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {session.message ??
                "Занятие закрыто. Следующее появится по плану."}
            </p>

            {summary}

            <div className="mt-6 flex flex-wrap gap-2">
              <Button asChild>
                <Link href={goalHref}>К цели</Link>
              </Button>
              <Button asChild variant="outline">
                <Link href={`${goalHref}?tab=plan`}>
                  {noTopics ? "Добавить темы" : "План и темы"}
                </Link>
              </Button>
              {!noTopics && (
                <Button asChild variant="ghost">
                  <Link href={`${goalHref}?tab=exam`}>Экзамен</Link>
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const currentPracticeScore =
    step?.kind === "practice" && step.topic_id
      ? practiceScoreFor(step.topic_id)
      : null;
  const currentPracticeFailed =
    currentPracticeScore !== null && currentPracticeScore < 0.7;
  const stepWord =
    steps.length === 1 ? "шаг" : steps.length < 5 ? "шага" : "шагов";
  const scheduledForFuture = session.scheduled_for !== session.date;
  const goalHref = scopeGoalId ? `/learning/goals/${scopeGoalId}` : "/learning";
  const stepHref = step
    ? step.kind === "exam"
      ? `${goalHref}?tab=exam`
      : step.kind === "exam_prep"
        ? `${goalHref}?tab=practice`
        : step.topic_id
          ? `${goalHref}/topic/${step.topic_id}`
          : `${goalHref}?tab=plan`
    : goalHref;
  const stepLinkLabel =
    step?.kind === "exam"
      ? "Открыть экзамен"
      : step?.kind === "exam_prep"
        ? "Все задания"
        : "Открыть тему";

  const progress = (
    <div className={cn("flex items-center gap-2", embedded ? "mb-1" : "mb-6")}>
      {steps.map((item, index) => {
        const meta = STEP_META[item.kind] ?? STEP_META.theory;
        const Icon = meta.icon;
        const done = index < active;
        const current = index === active;
        return (
          <div
            key={`${item.kind}-${item.topic_id ?? item.item_id ?? index}`}
            className="flex flex-1 items-center gap-2"
          >
            <div
              className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold",
                current
                  ? "border-brand bg-brand text-brand-foreground"
                  : done
                    ? "border-success bg-success text-white"
                    : "border-border bg-card text-muted-foreground",
              )}
              title={meta.label}
            >
              <Icon className="size-3.5" aria-hidden="true" />
            </div>
            <span
              className={cn(
                "hidden text-xs sm:block",
                current ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {meta.short}
            </span>
            {index < steps.length - 1 && (
              <div
                className={cn(
                  "h-px flex-1",
                  done ? "bg-success/50" : "bg-border",
                )}
              />
            )}
          </div>
        );
      })}
    </div>
  );

  const stepCard =
    step && currentMeta ? (
      <section className="card-shadow rounded-2xl border border-border bg-card p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{currentMeta.label}</Badge>
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <Clock3 className="size-3.5" aria-hidden="true" />
                {step.minutes} мин
              </span>
              {step.strength !== null && (
                <Badge variant="secondary">
                  сила {Math.round(step.strength * 100)}%
                </Badge>
              )}
            </div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-foreground">
              {step.title}
            </h2>
            {step.reason && (
              <p className="mt-1 text-xs text-muted-foreground">{step.reason}</p>
            )}
          </div>
          <Link href={stepHref} className="text-xs text-brand hover:underline">
            {stepLinkLabel}
          </Link>
        </div>

        <div className="mt-5">
          {step.kind === "review" && (
            <ReviewStep
              step={step}
              value={reviewText}
              onChange={setReviewText}
              showTheory={showReviewTheory}
              onToggleTheory={() => setShowReviewTheory((value) => !value)}
            />
          )}

          {step.kind === "theory" && (
            <TheoryStep
              step={step}
              text={theoryText}
              loading={theoryLoading}
              error={theoryError}
              onRetry={retryTheory}
              goalId={scopeGoalId}
            />
          )}

          {step.kind === "practice" && step.topic_id && (
            <div className="space-y-4">
              <PracticePanel
                goalId={session.goal_id}
                focusTopicId={step.topic_id}
                singleTopic
                onAnswer={(result) =>
                  handlePracticeAnswer(step.topic_id as string, result)
                }
                onTopicDone={() =>
                  handlePracticeCredited(step.topic_id as string)
                }
              />
              {currentPracticeScore !== null && (
                <div className="rounded-xl bg-muted/60 px-4 py-3 text-sm">
                  <span
                    className={cn(
                      "font-semibold",
                      scoreTone(currentPracticeScore),
                    )}
                  >
                    {Math.round(currentPracticeScore * 100)}%
                  </span>{" "}
                  {currentPracticeFailed ? (
                    <span className="text-muted-foreground">
                      Тема вернётся в ближайшем занятии — это нормальная часть
                      плана.
                    </span>
                  ) : (
                    <span className="text-muted-foreground">
                      Практика зачтена. Можно закрывать занятие.
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          {step.kind === "exam_prep" && (
            <div className="space-y-4">
              <p className="text-sm leading-relaxed text-foreground">
                Сегодня не читаем новую теорию: только задания по темам.
                Выполните серии — и экзамен откроется. Начните с первой темы,
                остальные доступны в списке.
              </p>
              <PracticePanel
                goalId={session.goal_id}
                focusTopicId={step.topic_id}
              />
              <p className="text-xs text-muted-foreground">
                Задания засчитываются в общий прогресс темы. Когда все серии
                пройдены, экзамен станет доступен.
              </p>
            </div>
          )}

          {step.kind === "exam" && <ExamStep goalId={session.goal_id} />}
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <Button
            variant="ghost"
            onClick={back}
            disabled={active === 0 || completing}
          >
            Назад
          </Button>
          <div className="flex flex-wrap items-center gap-2">
            {step.kind === "practice" &&
              step.topic_id &&
              !practiceAnsweredFor(step.topic_id) && (
                <span className="text-xs text-muted-foreground">
                  Если не успеваете, тема останется в плане.
                </span>
              )}
            <Button onClick={next} disabled={completing}>
              {completing
                ? "Сохраняем…"
                : isLast
                  ? "Завершить занятие"
                  : "Дальше"}
            </Button>
          </div>
        </div>
      </section>
    ) : null;

  const content = (
    <>
      {session.message && (
        <div
          className={cn(
            "rounded-xl border border-brand/20 bg-brand-muted/40 px-4 py-3 text-sm text-foreground",
            embedded ? "mb-3" : "mb-5",
          )}
        >
          {session.message}
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span>
          {steps.length} {stepWord} · около {session.total_minutes} мин
        </span>
        <span>·</span>
        <span>
          {scheduledForFuture
            ? `занятие со ${formatDay(session.scheduled_for)}`
            : "сегодня по плану"}
        </span>
      </div>

      {progress}
      {stepCard}

      {!embedded && (
        <p className="mt-4 text-center text-xs text-muted-foreground">
          Занятие собрано из вашего плана: повторение, теория и практика, а в
          конце маршрута — отдельные дни заданий и экзамена. Если сегодня не
          получится закончить, тема и даты сдвинутся на следующий раз.
        </p>
      )}
    </>
  );

  if (embedded) {
    return <div className="space-y-4">{content}</div>;
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 md:px-10">
        <Link
          href={goalHref}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← К цели
        </Link>

        <header className="mt-3 mb-5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Ежедневный режим</Badge>
            <span className="text-xs text-muted-foreground">
              {formatDay(session.date)}
            </span>
          </div>
          <h1 className="mt-2 text-2xl font-bold tracking-tight text-foreground">
            {goalTitle || session.goal_title}
          </h1>
        </header>

        {content}
      </div>
    </div>
  );
}

function ExamStep({ goalId }: { goalId: string }) {
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-brand/25 bg-brand-muted/35 px-4 py-4">
        <p className="text-sm font-medium text-foreground">
          Сегодня по плану — итоговый экзамен.
        </p>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
          Практика для допуска уже пройдена. Экзамен можно пройти здесь или
          открыть вкладку «Экзамен» и вернуться к занятию позже.
        </p>
      </div>
      <Button asChild>
        <Link href={`/learning/goals/${goalId}?tab=exam`}>
          Начать экзамен
        </Link>
      </Button>
      <p className="text-xs text-muted-foreground">
        После завершения экзамена день автоматически отметится выполненным.
      </p>
    </div>
  );
}

function ReviewStep({
  step,
  value,
  onChange,
  showTheory,
  onToggleTheory,
}: {
  step: DailyStep;
  value: string;
  onChange: (value: string) => void;
  showTheory: boolean;
  onToggleTheory: () => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-sm leading-relaxed text-foreground">
        Закройте конспект и вспомните 2–3 главные мысли по теме. Можно записать
        коротко — это только для вас.
      </p>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={4}
        placeholder="Что помните о теме?"
        className="w-full resize-y rounded-xl border border-border bg-background px-3.5 py-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground focus:border-brand focus:ring-2 focus:ring-brand/20"
        data-testid="daily-review-recall"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={onToggleTheory}>
          {showTheory ? "Скрыть конспект" : "Показать конспект"}
        </Button>
        {step.has_explanation ? (
          <span className="text-xs text-muted-foreground">
            Конспект сохранён — можно подсмотреть после попытки.
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            Конспекта пока нет. На шаге теории объясним тему заново.
          </span>
        )}
      </div>

      {showTheory && (
        <div className="rounded-xl border border-border bg-muted/30 px-4 py-3">
          {step.theory ? (
            <MarkdownRenderer
              content={step.theory}
              className="text-sm leading-relaxed text-foreground"
            />
          ) : step.description ? (
            <p className="text-sm leading-relaxed text-foreground">
              {step.description}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Сохранённого объяснения нет. Переходите к теории — оно появится там.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function TheoryStep({
  step,
  text,
  loading,
  error,
  onRetry,
  goalId,
}: {
  step: DailyStep;
  text: string;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  goalId: string;
}) {
  if (loading) {
    return (
      <div className="space-y-3" aria-busy="true">
        <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-4 w-full animate-pulse rounded bg-muted" />
        <div className="h-4 w-5/6 animate-pulse rounded bg-muted" />
        <p className="text-xs text-muted-foreground">
          Готовим объяснение по вашей теме…
        </p>
      </div>
    );
  }

  if (text) {
    return (
      <div className="space-y-3">
        <MarkdownRenderer
          content={text}
          className="text-sm leading-relaxed text-foreground"
        />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {step.description && (
        <p className="text-sm leading-relaxed text-foreground">
          {step.description}
        </p>
      )}
      <div className="rounded-xl border border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
        <p>
          Объяснение сейчас не удалось получить. Можно прочитать сохранённые
          материалы темы или попробовать ещё раз — занятие от этого не сломается.
        </p>
        {error && (
          <p className="mt-1 text-xs text-muted-foreground/80">{error}</p>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={onRetry}>
          Попробовать ещё раз
        </Button>
        {step.topic_id && (
          <Button asChild size="sm" variant="ghost">
            <Link href={`/learning/goals/${goalId}/topic/${step.topic_id}`}>
              Открыть тему целиком
            </Link>
          </Button>
        )}
      </div>
    </div>
  );
}
