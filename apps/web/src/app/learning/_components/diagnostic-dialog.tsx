"use client";

/**
 * Hidden diagnostic — TZ screen 2, FR-4.
 *
 * The point of this screen is that it must not feel like a level test. The
 * backend already refuses to generate a level question (FR-4.1 is enforced in
 * code, not only in the prompt), so the UI's job is to keep the tone
 * conversational and to let the learner skip or correct the result.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  correctDiagnostic,
  getDiagnosticState,
  replyToDiagnostic,
  startDiagnostic,
  type DiagnosticState,
  type DiagnosticTurn,
} from "@/lib/api/learning";

const BAND_LABEL: Record<string, string> = {
  not_familiar: "пока не знакомы",
  basic_familiarity: "базовое знакомство",
  working_knowledge: "уверенно применяете",
  solid: "хорошо владеете",
  strong: "очень хорошо владеете",
};

const REPLY_BUDGET = 8;

export function DiagnosticDialog({ goalId }: { goalId: string }) {
  const [turn, setTurn] = useState<DiagnosticTurn | null>(null);
  const [state, setState] = useState<DiagnosticState | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [finished, setFinished] = useState(false);
  // Multi-line answer field: diagnostic questions can ask about code ("что делает
  // этот код", "как он работает"), so the answer must be writable in a proper
  // multi-line field rather than a single-line input.
  const answerRef = useRef<HTMLTextAreaElement>(null);

  const loadState = useCallback(
    async (sessionId: string) => {
      try {
        setState(await getDiagnosticState(goalId, sessionId));
      } catch {
        // A missing state is not worth surfacing; the dialogue still works.
      }
    },
    [goalId],
  );

  useEffect(() => {
    // Resume the latest dialogue if one is already open.
    let cancelled = false;
    (async () => {
      try {
        const goals = await import("@/lib/api/learning").then((m) => m.listGoals());
        const goal = goals.find((g) => g.id === goalId);
        if (cancelled || !goal) return;
      } catch {
        // Ignore: the screen works without preloading.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [goalId]);

  async function handleStart() {
    setBusy(true);
    try {
      const first = await startDiagnostic(goalId, { max_replies: REPLY_BUDGET });
      setTurn(first);
      setState(null);
      setFinished(false);
      setText("");
    } catch (error) {
      toast.error("Не удалось начать диагностику", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleReply() {
    const answer = text.trim();
    if (!turn || !answer) return;

    setBusy(true);
    try {
      const next = await replyToDiagnostic(goalId, turn.session_id, answer);
      setText("");
      if (next.done) {
        setFinished(true);
        await loadState(next.session_id);
      } else {
        setTurn(next);
      }
    } catch (error) {
      toast.error("Не удалось отправить ответ", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleCorrect(topicId: string, value: number) {
    if (!state) return;
    try {
      const updated = await correctDiagnostic(goalId, state.session_id, {
        [topicId]: value,
      });
      setState(updated);
      toast.success("Оценка обновлена");
    } catch (error) {
      toast.error("Не удалось сохранить", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  if (!turn) {
    return (
      <div className="card-shadow rounded-2xl border border-border bg-card p-6">
        <h2 className="text-lg font-semibold tracking-tight text-foreground">
          Определим, что вы уже знаете
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Это короткий разговор — {REPLY_BUDGET} вопросов, по одному за раз. Мы не
          спрашиваем, насколько вы хороши: просто давайте пообщаемся о теме.
        </p>
        <Button className="mt-5" onClick={handleStart} disabled={busy}>
          {busy ? "Начинаем…" : "Начать"}
        </Button>
      </div>
    );
  }

  if (finished && state) {
    return (
      <div className="card-shadow rounded-2xl border border-border bg-card p-6">
        <Badge variant="secondary" className="mb-3">
          Вот что я понял о вашем уровне
        </Badge>
        <ul className="space-y-3">
          {state.estimates.map((estimate) => (
            <li key={estimate.id} className="rounded-xl border border-border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-foreground">{estimate.title}</span>
                <span className="text-xs text-muted-foreground">
                  {BAND_LABEL[estimate.band] ?? estimate.band}
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={Math.round(estimate.estimate * 100)}
                onChange={(e) =>
                  void handleCorrect(estimate.id, Number(e.target.value) / 100)
                }
                className="mt-3 w-full accent-brand"
                aria-label={`Оценка уровня по теме «${estimate.title}»`}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Если оценка неточна — передвиньте ползунок, план перестроится.
              </p>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="card-shadow rounded-2xl border border-border bg-card p-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <Badge variant="outline" className="text-xs">
          вопрос {Math.min((turn.turn_index ?? 0) + 1, REPLY_BUDGET)}
        </Badge>
        {turn.subtopic_title && (
          <span className="text-xs text-muted-foreground">{turn.subtopic_title}</span>
        )}
      </div>

      <p className="text-base leading-relaxed text-foreground">{turn.question}</p>

      <div className="mt-5 flex gap-2">
        <textarea
          ref={answerRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void handleReply();
            }
            if (e.key === "Tab") {
              e.preventDefault();
              const target = e.currentTarget;
              const { selectionStart, selectionEnd } = target;
              const next = `${text.slice(0, selectionStart ?? 0)}    ${text.slice(selectionEnd ?? 0)}`;
              setText(next);
              requestAnimationFrame(() => {
                if (answerRef.current) {
                  answerRef.current.selectionStart = (selectionStart ?? 0) + 4;
                  answerRef.current.selectionEnd = (selectionStart ?? 0) + 4;
                }
              });
            }
          }}
          placeholder="Ваш ответ"
          aria-label="Ответ на вопрос диагностики"
          className="min-h-[96px] w-full resize-y rounded-lg border border-border bg-background p-3 font-mono text-sm leading-5 text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
          disabled={busy}
        />
        <Button onClick={handleReply} disabled={busy || !text.trim()}>
          {busy ? "…" : "Ответить"}
        </Button>
      </div>
    </div>
  );
}