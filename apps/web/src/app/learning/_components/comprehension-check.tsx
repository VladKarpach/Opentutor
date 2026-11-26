"use client";

/**
 * The comprehension check on the topic page.
 *
 * It is a conversation, not a quiz. The learner explains the topic in their own
 * words; the server diagnoses each key unit and decides whether to ask one
 * targeted probe or to re-teach a single weak unit. The score is computed there
 * from fixed unit weights, so this component only renders the verdict.
 *
 * The card stays on the same page as the explanation because the check is about
 * *that* text: the diagnosis quotes the learner's answer and the material, and
 * "Спросить ИИ" opens the ordinary chat with the weak spot already named.
 */

import { useState, type FormEvent } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Loader2,
  MessageCircleQuestion,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import { MarkdownRenderer } from "@/components/shared/markdown-renderer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  answerComprehension,
  startComprehension,
  type ComprehensionGap,
  type ComprehensionLatest,
  type ComprehensionTurn,
  type ComprehensionVerdict,
} from "@/lib/api/learning";
import { requestChatOpen } from "@/lib/chat-events";
import { useLocale, useT, useTF } from "@/lib/i18n-context";
import { useChatStore } from "@/store/chat";

/** How each diagnosis status reads to the learner, in plain words. */
const KIND_LABEL: Record<string, string> = {
  missing: "comprehension.kind.missing",
  missing_prerequisite: "comprehension.kind.missingPrerequisite",
  partial: "comprehension.kind.partial",
  misconception: "comprehension.kind.misconception",
  surface_memorization: "comprehension.kind.surfaceMemorization",
};

export interface ComprehensionCheckProps {
  topicId: string;
  topicTitle: string;
  goalId: string;
  courseId?: string | null;
  /** False until an explanation exists; the check is pointless without one. */
  hasExplanation: boolean;
  /** The last finished check, if any, so a reload still shows its result. */
  latest: ComprehensionLatest | null;
  onLatestChange?: (latest: ComprehensionLatest) => void;
}

function percent(score: number | null | undefined): number {
  return Math.round((score ?? 0) * 100);
}

export function ComprehensionCheck({
  topicId,
  topicTitle,
  goalId,
  courseId,
  hasExplanation,
  latest,
  onLatestChange,
}: ComprehensionCheckProps) {
  const t = useT();
  const tf = useTF();
  const { locale } = useLocale();
  const sendMessage = useChatStore((s) => s.sendMessage);

  const [active, setActive] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [turn, setTurn] = useState<ComprehensionTurn | null>(null);
  const [step, setStep] = useState(0);
  const [maxSteps, setMaxSteps] = useState(4);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const finalTurn = turn && turn.status !== "in_progress" ? turn : null;
  const verdict = turn?.verdict ?? null;
  const next = turn?.next ?? null;
  const currentQuestion = next?.question ?? question;
  const gaps = verdict?.gaps ?? [];

  function reviewLine(days: number | null | undefined): string | null {
    if (days === null || days === undefined) return null;
    if (locale !== "ru") {
      return days <= 1
        ? t("comprehension.nextReviewTomorrow")
        : tf("comprehension.nextReview", { days });
    }
    if (days <= 0) return t("comprehension.nextReviewToday");
    const mod10 = days % 10;
    const mod100 = days % 100;
    if (mod10 === 1 && mod100 !== 11) {
      return tf("comprehension.nextReviewOne", { days });
    }
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
      return tf("comprehension.nextReviewFew", { days });
    }
    return tf("comprehension.nextReviewMany", { days });
  }

  async function begin() {
    setBusy(true);
    setError(null);
    try {
      const data = await startComprehension(topicId);
      setSessionId(data.session_id);
      setQuestion(data.question);
      setStep(data.step);
      setMaxSteps(data.max_steps);
      setTurn({
        session_id: data.session_id,
        topic_id: data.topic_id,
        status: data.status,
        step: data.step,
        max_steps: data.max_steps,
        score: data.score ?? 0,
        verdict: data.verdict ?? null,
        next: data.next ?? null,
      });
      setAnswer("");
      setActive(true);
    } catch {
      // The backend's message can name providers, models or HTTP status codes.
      // The card says what the learner can do instead of showing that.
      setError(t("comprehension.error"));
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sessionId || !answer.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const data = await answerComprehension(topicId, sessionId, answer.trim());
      setTurn(data);
      setStep(data.step);
      setMaxSteps(data.max_steps);
      setAnswer("");
      if (data.status !== "in_progress") {
        onLatestChange?.({
          checked: true,
          has_explanation: true,
          session_id: data.session_id,
          status: data.status,
          score: data.score,
          summary: data.verdict?.summary ?? null,
          gaps: data.verdict?.gaps ?? [],
          next_review_at: data.verdict?.next_review_at ?? null,
          interval_days: data.verdict?.interval_days ?? null,
          completed_at: new Date().toISOString(),
        });
      }
    } catch {
      setError(t("comprehension.error"));
    } finally {
      setBusy(false);
    }
  }

  function askAi(gapLabel?: string) {
    if (!courseId) return;
    const prompt = tf("comprehension.askAiPrompt", {
      topic: topicTitle,
      gap: gapLabel || gaps[0]?.label || topicTitle,
    });
    requestChatOpen(courseId);
    void sendMessage(courseId, prompt);
  }

  /**
   * What is wrong with each weak unit: the learner's words, why it is wrong,
   * what it would break, and the passage that settles it.
   *
   * Shared with the final screen on purpose: the result used to list only unit
   * labels, so a learner who was told "осталось разобраться: X" saw neither
   * what was wrong with X nor where to look.
   */
  function GapList({ gaps }: { gaps: ComprehensionGap[] }) {
    if (gaps.length === 0) return null;
    return (
      <section className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-amber-950">
          <AlertTriangle className="size-4" aria-hidden="true" />
          {t("comprehension.whereWrong")}
        </h3>
        <div className="mt-3 space-y-3">
          {gaps.map((gap) => (
            <div key={gap.unit_id} className="rounded-lg bg-background/70 p-3">
              <p className="text-sm font-medium text-foreground">{gap.label}</p>
              {KIND_LABEL[gap.kind] && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t(KIND_LABEL[gap.kind])}
                </p>
              )}
              {gap.evidence && (
                <p className="mt-1 text-xs italic text-muted-foreground">
                  {t("comprehension.youWrote")} «{gap.evidence}»
                </p>
              )}
              {gap.why && (
                <p className="mt-2 text-sm text-foreground">
                  <span className="font-medium">
                    {t("comprehension.whyWrong")}:
                  </span>{" "}
                  {gap.why}
                </p>
              )}
              {gap.consequence && (
                <p className="mt-1 text-sm text-foreground">
                  <span className="font-medium">
                    {t("comprehension.consequence")}:
                  </span>{" "}
                  {gap.consequence}
                </p>
              )}
              {gap.source_quote && (
                <blockquote className="mt-2 border-l-2 border-amber-300 pl-3 text-sm text-muted-foreground">
                  <span className="font-medium">
                    {t("comprehension.fromMaterial")}:
                  </span>{" "}
                  «{gap.source_quote}»
                </blockquote>
              )}
            </div>
          ))}
        </div>
      </section>
    );
  }

  function Diagnosis({ value }: { value: ComprehensionVerdict }) {
    const positive = value.units.filter((unit) => unit.status === "understood");
    return (
      <div className="space-y-4" data-testid="comprehension-verdict">
        {positive.length > 0 && (
          <section className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-emerald-900">
              <CheckCircle2 className="size-4" aria-hidden="true" />
              {t("comprehension.understood")}
            </h3>
            <div className="mt-3 space-y-2">
              {positive.map((unit) => (
                <div key={unit.id}>
                  <p className="text-sm font-medium text-emerald-950">
                    {unit.label}
                  </p>
                  {unit.evidence && (
                    <p className="mt-0.5 text-xs text-emerald-800">
                      {t("comprehension.youWrote")} «{unit.evidence}»
                    </p>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        <GapList gaps={value.gaps} />

        {value.summary && (
          <MarkdownRenderer
            content={value.summary}
            className="text-sm text-muted-foreground"
          />
        )}
      </div>
    );
  }

  function Reteach() {
    if (!next || next.kind !== "reteach" || !next.reteach) return null;
    const block = next.reteach;
    return (
      <section
        className="rounded-xl border border-brand/30 bg-brand/5 p-4"
        data-testid="comprehension-reteach"
      >
        <p className="text-[11px] font-semibold uppercase tracking-wide text-brand">
          {t("comprehension.reteachTitle")}
        </p>
        <h3 className="mt-1 text-base font-semibold text-foreground">
          {block.title}
        </h3>
        {block.why_wrong && (
          <p className="mt-2 text-sm text-foreground">
            <span className="font-medium">{t("comprehension.youThought")}</span>{" "}
            {block.why_wrong}
          </p>
        )}
        <p className="mt-3 text-sm font-medium text-foreground">
          {t("comprehension.really")}
        </p>
        <MarkdownRenderer
          content={block.explanation}
          className="mt-1 text-sm leading-relaxed text-foreground"
        />
        {block.example && (
          <div className="mt-3 text-sm text-foreground">
            <p className="font-medium">{t("comprehension.example")}:</p>
            <MarkdownRenderer content={block.example} className="mt-1" />
          </div>
        )}
        {block.source_quote && (
          <blockquote className="mt-3 border-l-2 border-brand/40 pl-3 text-sm text-muted-foreground">
            <span className="font-medium">
              {t("comprehension.fromMaterial")}:
            </span>{" "}
            «{block.source_quote}»
          </blockquote>
        )}
      </section>
    );
  }

  function Final({
    value,
    score,
    status,
    summary,
    finalGaps,
    intervalDays,
  }: {
    value: ComprehensionVerdict | null;
    score: number;
    status: string;
    summary: string | null;
    finalGaps: ComprehensionGap[];
    intervalDays: number | null | undefined;
  }) {
    const targetUnderstood = value?.target_status === "understood";
    return (
      <div data-testid="comprehension-final">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-base font-semibold text-foreground">
            <CheckCircle2 className="size-4 text-emerald-600" aria-hidden="true" />
            {t("comprehension.doneTitle")}
          </h2>
          <Badge variant={status === "passed" ? "secondary" : "outline"}>
            {status === "passed"
              ? t("comprehension.statusPassed")
              : t("comprehension.statusNeedsReview")}
          </Badge>
        </div>

        <p className="mt-4 text-2xl font-bold tracking-tight text-foreground">
          {tf("comprehension.final", { score })}
        </p>
        {targetUnderstood && (
          <p className="mt-1 text-sm font-medium text-emerald-700">
            {t("comprehension.correct")}
          </p>
        )}
        {summary && (
          <MarkdownRenderer
            content={summary}
            className="mt-2 text-sm text-foreground"
          />
        )}
        {finalGaps.length > 0 ? (
          <>
            <p className="mt-2 text-sm text-muted-foreground">
              {tf("comprehension.finalGaps", {
                items: finalGaps.map((gap) => gap.label).join(", "),
              })}
            </p>
            {/* The result has to say what is wrong and where to look, not only
                which units were weak. The same block the dialogue showed is
                repeated here so the check ends with an actionable explanation. */}
            <div className="mt-3">
              <GapList gaps={finalGaps} />
            </div>
          </>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">
            {t("comprehension.noGaps")}
          </p>
        )}
        {reviewLine(intervalDays) && (
          <p className="mt-1 text-sm text-muted-foreground">
            {reviewLine(intervalDays)}
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          <Button asChild>
            <Link href={`/learning/goals/${goalId}?tab=practice`}>
              {t("comprehension.toPractice")}
              <ArrowRight className="size-4" aria-hidden="true" />
            </Link>
          </Button>
          {courseId && (
            <Button variant="outline" onClick={() => askAi(finalGaps[0]?.label)}>
              <MessageCircleQuestion className="size-4" aria-hidden="true" />
              {t("comprehension.askAi")}
            </Button>
          )}
          <Button variant="outline" onClick={() => void begin()} disabled={busy}>
            <RefreshCw className="size-4" aria-hidden="true" />
            {t("comprehension.restart")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="card-shadow rounded-2xl border border-border bg-card p-5">
      {error && (
        <p
          className="mb-3 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive"
          data-testid="comprehension-error"
        >
          {error}
        </p>
      )}

      {finalTurn ? (
        <Final
          value={finalTurn.verdict}
          score={percent(finalTurn.score)}
          status={finalTurn.status}
          summary={finalTurn.verdict?.summary ?? null}
          finalGaps={finalTurn.verdict?.gaps ?? []}
          intervalDays={finalTurn.verdict?.interval_days}
        />
      ) : active && sessionId ? (
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 text-base font-semibold text-foreground">
              <Sparkles className="size-4 text-brand" aria-hidden="true" />
              {t("comprehension.title")}
            </h2>
            <Badge variant="outline">
              {tf("comprehension.step", {
                step: Math.min(step + 1, maxSteps),
                max: maxSteps,
              })}
            </Badge>
          </div>

          {verdict && (
            <div className="mt-4">
              <Diagnosis value={verdict} />
            </div>
          )}

          {next?.kind === "reteach" && (
            <div className="mt-4">
              <Reteach />
            </div>
          )}

          <form className="mt-4" onSubmit={(event) => void submit(event)}>
            <h3 className="text-sm font-semibold text-foreground">
              {next?.kind === "reteach"
                ? t("comprehension.checkTitle")
                : verdict
                  ? t("comprehension.probeTitle")
                  : t("comprehension.firstQuestion")}
            </h3>
            <MarkdownRenderer
              content={currentQuestion}
              className="mt-1 text-sm text-foreground"
            />
            <textarea
              aria-label={t("comprehension.answerLabel")}
              placeholder={t("comprehension.placeholder")}
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              rows={5}
              disabled={busy}
              data-testid="comprehension-answer"
              className="mt-3 w-full resize-y rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 disabled:opacity-60"
            />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button
                type="submit"
                disabled={busy || !answer.trim()}
                data-testid="comprehension-submit"
              >
                {busy && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
                {busy
                  ? t("comprehension.checking")
                  : t("comprehension.submit")}
              </Button>
              {courseId && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => askAi()}
                  disabled={busy}
                >
                  <MessageCircleQuestion className="size-4" aria-hidden="true" />
                  {t("comprehension.askAi")}
                </Button>
              )}
            </div>
          </form>
        </div>
      ) : (
        <div>
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-brand/10 p-2 text-brand">
              <Sparkles className="size-5" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h2 className="text-base font-semibold text-foreground">
                {t("comprehension.title")}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {t("comprehension.description")}
              </p>
            </div>
          </div>

          {latest?.checked && (
            <div className="mt-4 rounded-xl border border-border bg-secondary/40 p-4">
              <p className="text-lg font-semibold text-foreground">
                {tf("comprehension.final", { score: percent(latest.score) })}
              </p>
              {latest.summary && (
                <MarkdownRenderer
                  content={latest.summary}
                  className="mt-1 text-sm text-foreground"
                />
              )}
              {latest.gaps.length > 0 && (
                <>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {tf("comprehension.finalGaps", {
                      items: latest.gaps.map((gap) => gap.label).join(", "),
                    })}
                  </p>
                  <div className="mt-3">
                    <GapList gaps={latest.gaps} />
                  </div>
                </>
              )}
              {reviewLine(latest.interval_days) && (
                <p className="mt-1 text-sm text-muted-foreground">
                  {reviewLine(latest.interval_days)}
                </p>
              )}
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              onClick={() => void begin()}
              disabled={busy || !hasExplanation}
              data-testid="comprehension-start"
            >
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
              {busy
                ? t("comprehension.starting")
                : latest?.status === "in_progress"
                  ? t("comprehension.resume")
                  : latest?.checked
                    ? t("comprehension.restart")
                    : t("comprehension.start")}
            </Button>
            {latest?.checked && courseId && (
              <Button variant="outline" onClick={() => askAi()}>
                <MessageCircleQuestion className="size-4" aria-hidden="true" />
                {t("comprehension.askAi")}
              </Button>
            )}
          </div>

          {!hasExplanation && (
            <p className="mt-3 text-xs text-muted-foreground">
              {t("comprehension.needExplanation")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
