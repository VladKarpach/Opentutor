"use client";

/**
 * Practice as a series per subtopic — FR-8.
 *
 * The previous version pulled one question at a time from the spaced-repetition
 * queue, so "2-3 tasks per topic" was not a thing you could actually do. This
 * works through a topic's own series: 2-3 tasks ordered easiest first, so the
 * difficulty visibly climbs and completing a series is what opens the exam.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { MarkdownRenderer } from "@/components/shared/markdown-renderer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  disputeAnswer,
  generateQuestion,
  getGoalSchedule,
  getHint,
  prepareDrill,
  submitAnswer,
  type AnswerResult,
  type Question,
  type ScheduledTopic,
} from "@/lib/api/learning";
import { useT } from "@/lib/i18n-context";
import { cn } from "@/lib/utils";

import { CodeTaskDialog } from "./code-task-dialog";
import {
  TaskAnswerForm,
  type AnswerShape,
  type TaskFormat,
} from "./task-answer-form";

const CRITERIA_LABEL: Record<string, string> = {
  correctness: "Правильность",
  completeness: "Полнота",
  understanding: "Понимание",
};

const ERROR_LABEL: Record<string, string> = {
  concept_confusion: "путаете понятия",
  missing_step: "пропущен шаг",
  wrong_application: "неверное применение",
  incomplete: "ответ неполный",
};

function scoreTone(score: number): string {
  if (score >= 0.8) return "text-success";
  if (score >= 0.5) return "text-warning";
  return "text-destructive";
}

export function PracticePanel({
  goalId,
  focusTopicId,
  onTopicDone,
  onAnswer,
  singleTopic = false,
}: {
  goalId: string;
  /** Topic the schedule asked us to start with. */
  focusTopicId?: string | null;
  /** Lets the parent refresh the gate after a series completes. */
  onTopicDone?: () => void;
  /** Lets the daily session react to the first graded answer. */
  onAnswer?: (result: AnswerResult) => void;
  /**
   * Hide the topic picker. The guided session already chose the topic; falling
   * back to "choose any topic" when a series cannot be prepared would break the
   * one-decision flow.
   */
  singleTopic?: boolean;
}) {
  const t = useT();
  const [topics, setTopics] = useState<ScheduledTopic[]>([]);
  const [activeId, setActiveId] = useState<string | null>(focusTopicId ?? null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [answered, setAnswered] = useState<Set<string>>(new Set());
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  // Start in the preparing state for a guided single-topic panel: topics load
  // one render before the effect asks for the drill, and showing "no tasks"
  // during that frame would be a lie.
  const [preparing, setPreparing] = useState<string | null>(
    singleTopic ? focusTopicId ?? null : null,
  );
  // The guided session shows one topic; once its series is over there is
  // nothing left to pick, so show a clean completion state instead of reopening
  // the first task or falling back to the multi-topic picker.
  const [seriesFinished, setSeriesFinished] = useState(false);
  // A failed series preparation used to send the learner to the theory page.
  // A standalone generated task keeps the practice step a practice step.
  const [soloTask, setSoloTask] = useState(false);
  // The effect below runs again when the schedule finishes loading. Remember
  // which focus topic it already opened so a slow drill is not requested twice
  // and a missing topic does not leave the "preparing" state forever.
  const openedFor = useRef<string | null>(null);

  const [hintsUsed, setHintsUsed] = useState(0);
  const [hint, setHint] = useState<string | null>(null);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [busy, setBusy] = useState(false);

  // The code practice window. `codeTask` is whatever is open in it — a coding
  // task from the series, or an extra one generated from the topic picker.
  const [codeOpen, setCodeOpen] = useState(false);
  const [codeTask, setCodeTask] = useState<Question | null>(null);
  const [codeLoading, setCodeLoading] = useState(false);

  const loadTopics = useCallback(async () => {
    try {
      const schedule = await getGoalSchedule(goalId);
      setTopics(schedule.topics);
      return schedule.topics;
    } catch (error) {
      toast.error("Не удалось загрузить темы", {
        description: error instanceof Error ? error.message : undefined,
      });
      return [];
    } finally {
      setLoading(false);
    }
  }, [goalId]);

  useEffect(() => {
    void loadTopics();
  }, [loadTopics]);

  const active = useMemo(
    () => topics.find((t) => t.topic_id === activeId) ?? null,
    [topics, activeId],
  );

  // Open the requested topic's series as soon as it is known. If the schedule
  // does not contain that topic (or fails to load), still give the practice step
  // a task via the standalone fallback instead of showing a spinner forever.
  useEffect(() => {
    if (!focusTopicId || questions.length > 0) return;
    if (openedFor.current === focusTopicId) return;
    if (!topics.length) {
      if (loading) return;
      openedFor.current = focusTopicId;
      void generateSoloTask(focusTopicId, true);
      return;
    }
    const target = topics.find((t) => t.topic_id === focusTopicId);
    openedFor.current = focusTopicId;
    if (target) void openSeries(target);
    else void generateSoloTask(focusTopicId, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusTopicId, topics, questions.length, loading]);

  /**
   * Ask for one task immediately when the full series cannot be prepared.
   *
   * The guided session must stay a practice step even when drill generation
   * fails. This task is graded like any other; it is simply not part of the
   * topic's 2-3 task series.
   */
  async function generateSoloTask(topicId: string, quiet = false) {
    setActiveId(topicId);
    setPreparing(topicId);
    setSoloTask(false);
    setSeriesFinished(false);
    setResult(null);
    setHint(null);
    setHintsUsed(0);
    setIndex(0);
    try {
      const task = await generateQuestion(topicId, { format: "text" });
      setQuestions([task]);
      setAnswered(new Set());
      setSoloTask(true);
    } catch (error) {
      setQuestions([]);
      if (!quiet) {
        toast.error("Не удалось получить задание", {
          description: error instanceof Error ? error.message : undefined,
        });
      }
    } finally {
      setPreparing(null);
    }
  }

  async function openSeries(topic: ScheduledTopic) {
    setActiveId(topic.topic_id);
    setSeriesFinished(false);
    setSoloTask(false);
    setResult(null);
    setHint(null);
    setHintsUsed(0);
    setIndex(0);

    // A completed series needs no network round-trip. Asking the prepare
    // endpoint again used to surface a provider error above a card that was
    // already saying "series finished".
    if (topic.drill.done) {
      setQuestions([]);
      setSeriesFinished(true);
      setPreparing(null);
      // The daily session counts a previously completed series as today's
      // practice; the schedule page simply refreshes its gate.
      onTopicDone?.();
      return;
    }

    let series = topic.drill;
    if (!topic.drill.prepared) {
      setPreparing(topic.topic_id);
      try {
        const created = await prepareDrill(topic.topic_id);
        if (created.questions.length === 0) {
          await generateSoloTask(topic.topic_id, true);
          return;
        }
        setQuestions(created.questions);
        setAnswered(new Set(created.drill.answered_question_ids));
        // Land on the first unanswered task, so a resumed series does not
        // restart from the beginning.
        const firstOpen = created.questions.findIndex(
          (q) => !created.drill.answered_question_ids.includes(q.id),
        );
        setIndex(firstOpen === -1 ? 0 : firstOpen);
        setSeriesFinished(firstOpen === -1);
        return;
      } catch (error) {
        toast.error("Серия не подготовилась — даём отдельное задание", {
          description: error instanceof Error ? error.message : undefined,
        });
        await generateSoloTask(topic.topic_id, true);
        return;
      } finally {
        setPreparing(null);
      }
    }

    setPreparing(topic.topic_id);
    try {
      // The list endpoint carries the series ids; fetch the questions by asking
      // for the drill again, which is idempotent and returns them in order.
      const created = await prepareDrill(topic.topic_id);
      if (created.questions.length === 0) {
        await generateSoloTask(topic.topic_id, true);
        return;
      }
      setQuestions(created.questions);
      setAnswered(new Set(created.drill.answered_question_ids));
      series = created.drill;
      const firstOpen = created.questions.findIndex(
        (q) => !created.drill.answered_question_ids.includes(q.id),
      );
      setIndex(firstOpen === -1 ? 0 : firstOpen);
      setSeriesFinished(firstOpen === -1);
    } catch (error) {
      toast.error("Серия не загрузилась — даём отдельное задание", {
        description: error instanceof Error ? error.message : undefined,
      });
      await generateSoloTask(topic.topic_id, true);
    } finally {
      setPreparing(null);
    }
    void series;
  }

  const question = questions[index] ?? null;

  async function handleHint() {
    if (!question || hintsUsed >= 3) return;
    const level = (hintsUsed + 1) as 1 | 2 | 3;
    try {
      setHint(await getHint(question.id, level));
      setHintsUsed(level);
    } catch (error) {
      toast.error("Не удалось получить подсказку", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  async function handleSubmit(answer: AnswerShape) {
    if (!question) return;
    setBusy(true);
    try {
      const graded = await submitAnswer(question.id, {
        text: answer.text,
        value: answer.value,
        run_results: answer.runResults,
        hints_used: hintsUsed,
      });
      setResult(graded);
      setAnswered((prev) => new Set(prev).add(question.id));
      onAnswer?.(graded);
    } catch (error) {
      toast.error("Не удалось отправить ответ", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  /**
   * Ask the model for a coding task for this topic and open the editor.
   *
   * `existing` is set when the task came from the series: it is opened as-is
   * rather than replaced, so a drill's own tasks are what gets solved.
   */
  async function loadCodeTask(topicId: string) {
    setCodeLoading(true);
    setCodeOpen(true);
    try {
      setCodeTask(await generateQuestion(topicId, { format: "code" }));
    } catch (error) {
      toast.error("Не удалось подготовить задачу по коду", {
        description: error instanceof Error ? error.message : undefined,
      });
      setCodeOpen(false);
    } finally {
      setCodeLoading(false);
    }
  }

  function openCodeTask(topicId: string, existing?: Question) {
    if (existing) {
      setCodeTask(existing);
      setCodeOpen(true);
      return;
    }
    void loadCodeTask(topicId);
  }

  /** The dialog only calls this after the server accepted a passing solution. */
  function handleCodeSolved(graded: AnswerResult) {
    const solved = codeTask;
    if (solved && questions.some((q) => q.id === solved.id)) {
      setResult(graded);
      setAnswered((prev) => new Set(prev).add(solved.id));
    }
  }

  function handleNext() {
    setResult(null);
    setHint(null);
    setHintsUsed(0);
    const remaining = questions.findIndex(
      (q, i) => i > index && !answered.has(q.id),
    );
    if (remaining !== -1) setIndex(remaining);
    else {
      setQuestions([]);
      setSeriesFinished(true);
      if (soloTask) {
        toast.success("Задание выполнено");
        // A standalone task is not the topic's full series, so it must not
        // credit the series as done. The daily session still counts the score.
        if (!singleTopic) void loadTopics();
        return;
      }
      toast.success("Серия по теме пройдена");
      onTopicDone?.();
      // The guided session has no topic picker to refresh; refetching here
      // would briefly replace the completion state with a loading skeleton.
      if (!singleTopic) void loadTopics();
    }
  }

  async function handleDispute() {
    if (!result) return;
    try {
      await disputeAnswer(result.id, "Оценка несправедлива");
      toast.success("Оспаривание отправлено");
    } catch (error) {
      toast.error("Не удалось оспорить оценку", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  if (loading) {
    return <div className="h-64 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  if (topics.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Сначала разбейте цель на подтемы — тогда по каждой теме можно будет
        подготовить задания.
      </p>
    );
  }

  if (singleTopic && focusTopicId && seriesFinished) {
    return (
      <div className="rounded-xl border border-success/30 bg-success-muted/30 px-4 py-5 text-sm text-foreground">
        Серия по теме пройдена. Можно закрывать занятие.
      </div>
    );
  }

  if (
    singleTopic &&
    focusTopicId &&
    preparing &&
    topics.some((topic) => topic.topic_id === focusTopicId)
  ) {
    return (
      <div
        className="flex items-center gap-2 rounded-xl border border-border bg-muted/30 px-4 py-5 text-sm text-muted-foreground"
        aria-busy="true"
      >
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-brand" />
        Готовим задание по теме… Обычно это занимает несколько секунд.
      </div>
    );
  }

  // Topic picker.
  if (!question) {
    if (singleTopic && focusTopicId) {
      const focused = topics.find((topic) => topic.topic_id === focusTopicId);
      if (focused?.drill.done) {
        return (
          <div className="rounded-xl border border-success/30 bg-success-muted/30 px-4 py-5 text-sm text-foreground">
            Серия по теме пройдена. Можно закрывать занятие.
          </div>
        );
      }
      return (
        <div className="rounded-xl border border-dashed border-border bg-muted/30 px-4 py-5 text-sm text-muted-foreground">
          <p>
            Серию подготовить не удалось. Можно получить отдельное задание прямо
            здесь — без перехода к теории. Оно будет проверено так же, как
            задание из серии.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              onClick={() => void generateSoloTask(focusTopicId)}
              disabled={preparing === focusTopicId}
            >
              {preparing === focusTopicId
                ? "Готовим задание…"
                : "Дать задание сейчас"}
            </Button>
            <Link
              href={`/learning/goals/${goalId}/topic/${focusTopicId}`}
              className="text-xs text-brand hover:underline"
            >
              Повторить теорию
            </Link>
          </div>
        </div>
      );
    }
    const allDone = topics.every((t) => t.drill.done);
    return (
      <div className="space-y-4">
        {allDone && (
          <div className="rounded-xl border border-success/40 bg-success-muted/40 px-4 py-3 text-sm text-foreground">
            Все серии пройдены — можно идти на экзамен.
          </div>
        )}
        <p className="text-sm text-muted-foreground">
          Выберите тему: по каждой запланированы 2–3 задания с нарастающей
          сложностью.
        </p>
        <ul className="space-y-2">
          {topics.map((topic) => (
            <li
              key={topic.topic_id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">{topic.title}</p>
                <p className="text-xs text-muted-foreground">
                  {topic.drill.prepared
                    ? `задания: ${topic.drill.answered}/${topic.drill.required}`
                    : "задания не подготовлены"}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="xs"
                  variant="outline"
                  disabled={codeLoading && codeOpen}
                  onClick={() => openCodeTask(topic.topic_id)}
                >
                  {t("codeTask.pickerButton")}
                </Button>
                <Button
                  size="xs"
                  variant={topic.drill.done ? "ghost" : "secondary"}
                  disabled={preparing === topic.topic_id}
                  onClick={() => void openSeries(topic)}
                >
                  {preparing === topic.topic_id ? "Готовим…" : "Открыть"}
                </Button>
              </div>
            </li>
          ))}
        </ul>

        <CodeTaskDialog
          open={codeOpen}
          onOpenChange={setCodeOpen}
          question={codeTask}
          generating={codeLoading}
          onSolved={handleCodeSolved}
          onRequestNew={
            codeTask ? () => void loadCodeTask(codeTask.topic_id) : undefined
          }
        />
      </div>
    );
  }

  const seriesDone = questions.filter((q) => answered.has(q.id)).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary" className="text-xs">
            {active?.title}
          </Badge>
          <Badge variant="outline" className="text-xs">
            задание {Math.min(index + 1, questions.length)} из {questions.length}
          </Badge>
        </div>
        <Badge
          variant={seriesDone >= questions.length ? "default" : "outline"}
          className="text-xs"
        >
          решено {seriesDone} из {questions.length}
        </Badge>
      </div>

      {/* Difficulty ramp made visible: dots per task, filled as they are done. */}
      <div className="flex items-center gap-1.5">
        {questions.map((q, i) => (
          <span
            key={q.id}
            title={`Сложность ${Math.round(q.difficulty * 100)}%`}
            className={cn(
              "size-2.5 rounded-full border",
              i === index
                ? "border-brand bg-brand/30"
                : answered.has(q.id)
                  ? "border-success bg-success"
                  : "border-border bg-muted",
            )}
          />
        ))}
      </div>

      {hint && (
        <div className="rounded-xl bg-brand-muted px-4 py-3 text-sm text-foreground">
          <span className="text-xs text-muted-foreground">
            Подсказка {hintsUsed}/3 (снижает оценку):
          </span>
          <p className="mt-1">{hint}</p>
        </div>
      )}

      <MarkdownRenderer
        content={question.text}
        className="text-base leading-relaxed text-foreground"
      />

      {!result && (
        <>
          {/* keyed by the task, so a new task starts with an empty field. Resetting state
              by hand here would need either an effect — which renders one frame with
              the previous task's answer still on screen — or a ref read during
              render, which the React compiler forbids. Remounting is the answer it
              recommends, and it is also the cheapest of the three. */}
          {question.format === "code" ? (
            <div className="space-y-2 rounded-xl border border-border bg-muted/30 px-4 py-3">
              <p className="text-xs text-muted-foreground">
                {t("codeTask.codePracticeHint")}
              </p>
              <Button
                size="sm"
                onClick={() => openCodeTask(question.topic_id, question)}
                data-testid="open-code-task"
              >
                {t("codeTask.openEditor")}
              </Button>
            </div>
          ) : (
            <TaskAnswerForm
              key={question.id}
              format={(question.format ?? "text") as TaskFormat}
              payload={question.payload ?? {}}
              disabled={busy}
              submitLabel={busy ? t("taskFormat.submitting") : t("taskFormat.submit")}
              onAnswer={handleSubmit}
            />
          )}

          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={handleHint} disabled={hintsUsed >= 3}>
              Подсказка ({3 - hintsUsed})
            </Button>
          </div>
        </>
      )}

      {result && (
        <div className="space-y-3 rounded-xl bg-muted/60 px-4 py-3">
          <div className="flex flex-wrap items-baseline gap-3">
            <span className={cn("text-2xl font-bold", scoreTone(result.final_score))}>
              {Math.round(result.final_score * 100)}
            </span>
            <span className="text-xs text-muted-foreground">
              следующий повтор через {result.interval_days} дн.
            </span>
            {/* Who decided. "The system compared this" and "a model read this"
                are different claims, and the learner is entitled to know which
                one they are looking at. */}
            <Badge variant="outline" className="text-[10px]" data-testid="verdict-source">
              {result.checked_without_model
                ? t("taskFormat.checkedBySystem")
                : t("taskFormat.checkedByModel")}
            </Badge>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {Object.entries(result.breakdown ?? {}).map(([key, value]) => (
              <div key={key} className="rounded-lg bg-background px-2 py-1.5">
                <p className="text-[10px] text-muted-foreground">
                  {CRITERIA_LABEL[key] ?? key}
                </p>
                <p className="text-sm font-medium text-foreground">
                  {Math.round(Number(value) * 100)}%
                </p>
              </div>
            ))}
          </div>

          {result.error_types.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {result.error_types.map((type) => (
                <Badge key={type} className="bg-destructive/10 text-destructive">
                  {ERROR_LABEL[type] ?? type}
                </Badge>
              ))}
            </div>
          )}

          {result.feedback && (
            <MarkdownRenderer
              content={result.feedback}
              className="text-sm text-muted-foreground"
            />
          )}

          {/* Shown only when it was withheld and there is something to withhold.
              A wrong answer that cannot be looked at is a wrong answer that
              cannot be learned from. */}
          {result.expected_answer && (
            <details className="rounded-lg border border-border bg-background px-3 py-2 text-xs">
              <summary className="cursor-pointer text-muted-foreground">
                {t("taskFormat.expected")}
              </summary>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] text-foreground">
                {result.expected_answer}
              </pre>
            </details>
          )}

          <div className="flex flex-wrap gap-2">
            <Button onClick={handleNext}>Дальше</Button>
            <Button variant="ghost" size="sm" onClick={handleDispute}>
              Оспорить оценку
            </Button>
          </div>
        </div>
      )}

      <CodeTaskDialog
        open={codeOpen}
        onOpenChange={setCodeOpen}
        question={codeTask}
        generating={codeLoading}
        onSolved={handleCodeSolved}
        onRequestNew={
          codeTask && !questions.some((q) => q.id === codeTask.id)
            ? () => void loadCodeTask(codeTask.topic_id)
            : undefined
        }
      />
    </div>
  );
}