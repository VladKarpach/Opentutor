"use client";

/**
 * Final exam — TZ screen 8, FR-11.
 *
 * Two modes per FR-11.2: training (hints allowed, no clock) and strict (timed,
 * hints disabled and grades withheld until the end).
 *
 * The result view is FR-11.3: an overall mark, a per-topic breakdown and
 * recommendations.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  answerExamQuestion,
  getExamResult,
  getGoalSchedule,
  startExam,
  type ExamMode,
  type ExamQuestion,
  type ExamResult,
  type GoalSchedule,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";

function formatClock(seconds: number | null): string | null {
  if (seconds === null) return null;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function scoreTone(score: number): string {
  if (score >= 0.8) return "text-success";
  if (score >= 0.5) return "text-warning";
  return "text-destructive";
}

/** Grade feedback for one exam answer, shown only when the server reveals it. */
interface ExamAnswerFeedback {
  score: number;
  raw: number;
  penalty: number;
  feedback: string | null;
  errors: string[];
}

export function ExamPanel({ goalId }: { goalId: string }) {
  const [examId, setExamId] = useState<string | null>(null);
  const [question, setQuestion] = useState<ExamQuestion | null>(null);
  const [result, setResult] = useState<ExamResult | null>(null);
  const [mode, setMode] = useState<ExamMode>("training");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [clock, setClock] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<ExamAnswerFeedback | null>(null);
  // The exam is gated on the practice series; say so before the click, not after.
  const [gate, setGate] = useState<GoalSchedule | null>(null);

  // FR-11.2: tick a local countdown for feedback only. The server's
  // remaining_seconds() stays authoritative and rejects late answers.
  useEffect(() => {
    if (clock === null) return;
    const timer = setInterval(
      () => setClock((c) => (c === null ? null : Math.max(0, c - 1))),
      1000,
    );
    return () => clearInterval(timer);
  }, [clock]);

  // Load the gate as soon as the tab opens so the state is visible up front.
  useEffect(() => {
    let cancelled = false;
    getGoalSchedule(goalId)
      .then((data) => {
        if (!cancelled) setGate(data);
      })
      .catch(() => {
        /* advisory only; starting the exam still reports its own error */
      });
    return () => {
      cancelled = true;
    };
  }, [goalId]);

  const loadResult = useCallback(
    async (id: string) => {
      try {
        setResult(await getExamResult(goalId, id));
      } catch (error) {
        toast.error("Не удалось получить результат", {
          description: error instanceof Error ? error.message : undefined,
        });
      }
    },
    [goalId],
  );

  async function handleStart(selected: ExamMode, force = false) {
    setBusy(true);
    setResult(null);
    setFeedback(null);
    setText("");
    try {
      const first = await startExam(goalId, selected, force);
      setExamId(first.exam_id);
      setQuestion(first);
      setMode(selected);
      setClock(first.seconds_left);
      const fresh = await getGoalSchedule(goalId);
      setGate(fresh);
    } catch (error) {
      toast.error("Не удалось начать экзамен", {
        description: error instanceof Error ? error.message : undefined,
      });
      try {
        setGate(await getGoalSchedule(goalId));
      } catch {
        /* the gate is advisory here; the toast already explained the failure */
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleAnswer() {
    if (!examId || !question || !text.trim()) return;
    setBusy(true);
    try {
      const answer = await answerExamQuestion(goalId, examId, question.index, {
        text: text.trim(),
        hints_used: 0,
      });

      if (answer.revealed) {
        setFeedback({
          score: answer.final_score,
          raw: answer.raw_score,
          penalty: answer.hint_penalty,
          feedback: answer.feedback,
          errors: answer.error_types,
        });
      }

      setText("");
      if (answer.done) {
        setQuestion(null);
        await loadResult(answer.exam_id);
      } else if (answer.next_question) {
        setQuestion(answer.next_question);
        setFeedback(null);
      }
    } catch (error) {
      toast.error("Не удалось отправить ответ", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div className="card-shadow space-y-5 rounded-2xl border border-border bg-card p-6">
        <div className="flex flex-wrap items-baseline gap-3">
          <span className={cn("text-4xl font-bold", scoreTone(result.total_score))}>
            {Math.round(result.total_score * 100)}
          </span>
          <div>
            <p className="text-sm font-medium text-foreground">Итоговый балл</p>
            <p className="text-xs text-muted-foreground">
              {result.answered} из {result.total_questions} вопросов · режим{" "}
              {result.mode === "strict" ? "строгий" : "тренировочный"}
            </p>
          </div>
        </div>

        {/* Auto-completion: passing the exam is what closes the goal now. */}
        {result.goal_completed ? (
          <div className="rounded-xl border border-success/40 bg-success-muted/40 px-4 py-3">
            <p className="text-sm font-medium text-foreground">
              Цель выполнена
            </p>
            <p className="text-xs text-muted-foreground">
              Результат не ниже {Math.round(result.pass_threshold * 100)}% — цель
              закрыта автоматически.
            </p>
          </div>
        ) : (
          <div className="rounded-xl border border-border bg-muted/40 px-4 py-3">
            <p className="text-sm text-foreground">
              Балл ниже {Math.round(result.pass_threshold * 100)}% — цель остаётся
              открытой.
            </p>
            <p className="text-xs text-muted-foreground">
              Разберите слабые темы из разбора выше и попробуйте ещё раз.
            </p>
          </div>
        )}

        <div>
          <h3 className="mb-2 text-sm font-semibold text-foreground">По темам</h3>
          <ul className="space-y-2">
            {result.by_topic.map((topic) => (
              <li key={topic.topic_id}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm text-foreground">{topic.title}</span>
                  <span className={cn("text-sm font-medium", scoreTone(topic.score))}>
                    {Math.round(topic.score * 100)}%
                  </span>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className={cn(
                      "h-full rounded-full",
                      topic.score >= 0.8
                        ? "bg-success"
                        : topic.score >= 0.5
                          ? "bg-brand"
                          : "bg-destructive",
                    )}
                    style={{ width: `${Math.round(topic.score * 100)}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3 className="mb-2 text-sm font-semibold text-foreground">Что дальше</h3>
          <ul className="space-y-1">
            {result.recommendations.map((line) => (
              <li key={line} className="text-sm text-muted-foreground">
                {line}
              </li>
            ))}
          </ul>
          {result.suggestion && (
            <p className="mt-2 rounded-lg bg-brand-muted px-3 py-2 text-sm text-foreground">
              {result.suggestion}
            </p>
          )}
        </div>

        <Button
          variant="outline"
          onClick={() => {
            setResult(null);
            setExamId(null);
          }}
        >
          К списку целей
        </Button>
      </div>
    );
  }

  if (question && examId) {
    const clockLabel = formatClock(clock);
    return (
      <div className="card-shadow space-y-4 rounded-2xl border border-border bg-card p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Badge variant="outline" className="text-xs">
            Вопрос {question.index + 1} из {question.total}
          </Badge>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-xs">
              {mode === "strict" ? "строгий" : "тренировочный"}
            </Badge>
            {clockLabel && (
              // FR-11.2: the timer only shows in strict mode.
              <Badge
                className={cn(
                  "text-xs",
                  clock !== null && clock < 60
                    ? "bg-destructive/10 text-destructive"
                    : "",
                )}
              >
                {clockLabel}
              </Badge>
            )}
          </div>
        </div>

        {question.topic_title && (
          <p className="text-xs text-muted-foreground">{question.topic_title}</p>
        )}
        <p className="text-base leading-relaxed text-foreground">{question.text}</p>

        {!question.hints_allowed && (
          <p className="text-xs text-muted-foreground">
            В строгом режиме подсказки недоступны, оценки покажутся в конце.
          </p>
        )}

        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ваш ответ"
          aria-label="Ответ на вопрос экзамена"
        />

        <Button onClick={handleAnswer} disabled={busy || !text.trim()}>
          {busy ? "Проверяем…" : question.index + 1 >= question.total ? "Завершить" : "Далее"}
        </Button>

        {feedback && (
          <div className="space-y-2 rounded-xl bg-muted/60 px-4 py-3">
            <p className="text-sm text-foreground">
              Оценка: {Math.round(feedback.score * 100)}
              {feedback.penalty > 0 && (
                <span className="text-muted-foreground">
                  {" "}
                  (без подсказок {Math.round(feedback.raw * 100)})
                </span>
              )}
            </p>
            {feedback.errors.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {feedback.errors.map((type) => (
                  <Badge key={type} className="bg-destructive/10 text-destructive">
                    {type}
                  </Badge>
                ))}
              </div>
            )}
            {feedback.feedback && (
              <p className="text-sm text-muted-foreground">{feedback.feedback}</p>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="card-shadow space-y-4 rounded-2xl border border-border bg-card p-6">
      <h2 className="text-lg font-semibold tracking-tight text-foreground">
        Итоговый экзамен
      </h2>
      <p className="text-sm text-muted-foreground">
        Собирается из вопросов по всем темам, слабые темы попадают в него чаще.
        Результат не ниже {Math.round((gate ? 0.7 : 0.7) * 100)}% закрывает цель
        автоматически.
      </p>

      {/* The gate, stated before the button rather than as a rejection after it. */}
      {gate && !gate.exam_unlocked && (
        <div className="space-y-2 rounded-xl border border-warning/40 bg-warning-muted/40 px-4 py-3">
          <p className="text-sm text-foreground">
            Сначала выполните задания по темам: готово {gate.topics_done} из{" "}
            {gate.topics_total}.
          </p>
          {gate.pending_exam.length > 0 && (
            <ul className="space-y-0.5">
              {gate.pending_exam.slice(0, 5).map((item) => (
                <li key={item.topic_id} className="text-xs text-muted-foreground">
                  {item.title} —{" "}
                  {item.reason === "not_prepared"
                    ? "задания не подготовлены"
                    : `решено ${item.answered} из ${item.required}`}
                </li>
              ))}
              {gate.pending_exam.length > 5 && (
                <li className="text-xs text-muted-foreground">
                  и ещё {gate.pending_exam.length - 5}
                </li>
              )}
            </ul>
          )}
        </div>
      )}

      {gate?.exam_unlocked && (
        <div className="rounded-xl border border-success/40 bg-success-muted/40 px-4 py-3 text-sm text-foreground">
          Все серии пройдены — экзамен открыт.
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => handleStart("training")} disabled={busy}>
          Тренировочный
        </Button>
        <Button variant="outline" onClick={() => handleStart("strict")} disabled={busy}>
          Строгий, с таймером
        </Button>
        {/* Explicit opt-out, only when the gate is actually closed. Without it a
            failed drill generation would leave no way to reach the exam. */}
        {gate && !gate.exam_unlocked && (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  "Пройти экзамен без всех заданий? Цель всё равно закроется только " +
                    "по результату экзамена.",
                )
              ) {
                void handleStart("training", true);
              }
            }}
          >
            Всё равно пройти
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        В тренировочном режиме доступны подсказки и видна оценка сразу. В строгом —
        таймер, без подсказок, оценки в конце.
      </p>
    </div>
  );
}