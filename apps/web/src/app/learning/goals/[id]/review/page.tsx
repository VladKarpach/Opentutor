"use client";

/**
 * Goal-level review session.
 *
 * The unified inbox (`/learning/review/due`) links each due review item to
 * `/learning/goals/{id}/review` (adaptive_learning.py:due_review_global). Until
 * this page existed that link produced a Next.js 404 "This page could not be
 * found." This page drives the due-review queue for a single goal:
 * fetch a question → answer it → show the verdict → move on to the next.
 */

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, CheckCircle2, Loader2, RefreshCw, XCircle } from "lucide-react";
import { toast } from "sonner";

import {
  getGoal,
  getNextReviewQuestion,
  submitAnswer,
  type AnswerResult,
  type LearningGoal,
  type Question,
} from "@/lib/api/learning";
import { MarkdownRenderer } from "@/components/shared/markdown-renderer";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TaskAnswerForm, type AnswerShape, type TaskFormat } from "@/app/learning/_components/task-answer-form";
import { useT, useTF } from "@/lib/i18n-context";

export default function GoalReviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const t = useT();
  const tf = useTF();

  const [goal, setGoal] = useState<LearningGoal | null>(null);
  const [question, setQuestion] = useState<Question | null>(null);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewedCount, setReviewedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingNext, setLoadingNext] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const loadGoal = useCallback(async () => {
    try {
      const data = await getGoal(id);
      setGoal(data);
    } catch (err) {
      toast.error("Не удалось загрузить цель", {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  }, [id]);

  const loadQuestion = useCallback(
    async (next = false) => {
      setError(null);
      setResult(null);
      if (next) setLoadingNext(true);
      else setLoading(true);
      try {
        const q = await getNextReviewQuestion(id);
        setQuestion(q);
      } catch (err) {
        // The backend reports "Nothing to review yet" when the queue is exhausted.
        const msg = err instanceof Error ? err.message : "";
        if (/nothing to review/i.test(msg)) {
          setQuestion(null);
          setError(null);
        } else {
          setError(t("goalReview.loadFailed"));
        }
      } finally {
        if (next) setLoadingNext(false);
        else setLoading(false);
      }
    },
    [id, t],
  );

  useEffect(() => {
    void loadGoal();
    void loadQuestion();
  }, [loadGoal, loadQuestion]);

  const handleAnswer = useCallback(
    async (answer: AnswerShape) => {
      if (!question) return;
      setSubmitting(true);
      try {
        const res = await submitAnswer(question.id, {
          text: answer.text,
          value: answer.value,
          run_results: answer.runResults,
          image: answer.image,
        });
        setResult(res);
        setReviewedCount((n) => n + 1);
      } catch (err) {
        toast.error(t("goalReview.submitFailed"), {
          description: err instanceof Error ? err.message : undefined,
        });
      } finally {
        setSubmitting(false);
      }
    },
    [question, t],
  );

  const handleNext = () => void loadQuestion(true);

  const isInitialLoading =
    loading && question === null && result === null && !error;
  const isComplete =
    !loading && !loadingNext && question === null && result === null && !error;

  // Border background for the answer verdict card.
  let verdictClass = "";
  if (result) {
    verdictClass =
      result.final_score >= 0.8
        ? "border-success bg-success/5"
        : result.final_score >= 0.5
          ? "border-warning bg-warning/5"
          : "border-destructive bg-destructive/5";
  }

  if (isInitialLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="size-6 animate-spin text-brand" />
          <p className="text-sm text-muted-foreground">{t("goalReview.loading")}</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="rounded-xl border border-border bg-card p-8 text-center max-w-md w-full">
          <XCircle className="mx-auto size-12 text-destructive mb-4" />
          <h2 className="text-lg font-semibold text-foreground">
            {t("goalReview.failed")}
          </h2>
          <p className="text-sm text-muted-foreground mb-6">{error}</p>
          <div className="flex flex-col sm:flex-row gap-2">
            <Button onClick={() => void loadQuestion()} variant="outline">
              <RefreshCw className="mr-2 size-4" /> {t("common.retry")}
            </Button>
            <Button asChild>
              <Link href={`/learning/goals/${id}`}>{t("goalReview.backToGoal")}</Link>
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (isComplete) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="rounded-xl border border-border bg-card p-8 text-center max-w-md w-full">
          <CheckCircle2 className="mx-auto size-12 text-success mb-4" />
          <h2 className="text-lg font-semibold text-foreground">
            {t("goalReview.complete")}
          </h2>
          <p className="text-sm text-muted-foreground mb-6">
            {tf("goalReview.completeMessage", { count: reviewedCount })}
          </p>
          <div className="flex flex-col sm:flex-row gap-2">
            <Button asChild>
              <Link href={`/learning/goals/${id}`}>{t("goalReview.backToGoal")}</Link>
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-2xl px-4 py-8 sm:px-6 md:px-8">
        {goal && (
          <div className="mb-6 flex items-start justify-between gap-2">
            <div className="flex items-center gap-2">
              <Button asChild size="sm" variant="outline">
                <Link href={`/learning/goals/${id}`}>
                  <ArrowLeft className="mr-2 size-3.5" /> {t("goalReview.backToGoal")}
                </Link>
              </Button>
              {goal.course_name && (
                <Link
                  href={`/course/${goal.course_id}`}
                  className="text-xs text-muted-foreground hover:text-foreground"
                >
                  ← Курс: {goal.course_name}
                </Link>
              )}
            </div>
          </div>
        )}
        <header className="mb-6">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {goal ? goal.topic : t("goalReview.title")}
          </h1>
          {goal && (
            <p className="text-sm text-muted-foreground">
              Сессия повторения по цели <strong>{goal.topic}</strong>.
            </p>
          )}
        </header>

        <div className="rounded-xl border border-border bg-card p-6">
          <div className="flex items-center justify-between mb-4">
            <Badge variant="secondary">{t("goalReview.sessionTitle")}</Badge>
            <span className="text-xs text-muted-foreground">
              {tf("goalReview.reviewed", { reviewed: reviewedCount })}
            </span>
          </div>

          {question && (
            <>
              <MarkdownRenderer content={question.text ?? ""} />

              {result === null && (
                <div className="mt-6">
                  <TaskAnswerForm
                    format={(question.format ?? "text") as TaskFormat}
                    payload={question.payload ?? {}}
                    disabled={submitting}
                    onAnswer={handleAnswer}
                    submitLabel={t("goalReview.submit")}
                  />
                </div>
              )}

              {result && (
                <div className={`mt-6 rounded-lg border p-4 ${verdictClass}`}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="font-medium text-foreground">
                      {tf("goalReview.score", {
                        score: Math.round(result.final_score * 100),
                      })}
                    </span>
                  </div>
                  {result.feedback && (
                    <div className="mb-2">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                        {t("goalReview.feedback")}
                      </p>
                      <MarkdownRenderer
                        content={result.feedback}
                        className="text-sm text-foreground"
                      />
                    </div>
                  )}
                  {!result.feedback && result.expected_answer && (
                    <div className="mb-2">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                        {t("goalReview.expectedAnswer")}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {result.expected_answer}
                      </p>
                    </div>
                  )}
                  {result.next_review_at && (
                    <p className="text-xs text-muted-foreground">
                      Следующее повторение:{" "}
                      {new Date(result.next_review_at).toLocaleDateString("ru-RU", {
                        day: "numeric",
                        month: "short",
                        year: "numeric",
                      })}
                    </p>
                  )}
                </div>
              )}

              {result && (
                <div className="mt-6">
                  <Button onClick={handleNext} disabled={loadingNext}>
                    {loadingNext ? (
                      <>
                        <Loader2 className="mr-2 size-3.5 animate-spin" />
                        Загружаем…
                      </>
                    ) : (
                      t("goalReview.next")
                    )}
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
