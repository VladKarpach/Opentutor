"use client";

/**
 * Topic board — the planner's view of a goal's subtopics.
 *
 * Covers FR-5.6 (mark "already known" / "skip"), FR-6.6 (edit, add) and shows
 * the strength and next review date the repetition engine keeps per topic
 * (FR-9.1). The dependency graph itself lives on the progress screen.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  addTopics,
  generateTopics,
  updateTopic,
  type LearningTopic,
  type TopicStatus,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<TopicStatus, string> = {
  not_started: "не начато",
  weak: "слабое место",
  learning: "в работе",
  known: "освоено",
};

const STATUS_TONE: Record<TopicStatus, string> = {
  not_started: "bg-muted text-muted-foreground",
  weak: "bg-destructive/10 text-destructive",
  learning: "bg-info-muted text-info",
  known: "bg-success-muted text-success",
};

/** Strength as a percentage, for the inline bar (FR-9.1). */
function StrengthBar({ value }: { value: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      role="meter"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`Сила знаний ${pct}%`}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-500",
          pct >= 80 ? "bg-success" : pct >= 50 ? "bg-brand" : "bg-destructive",
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function nextReviewLabel(iso: string | null): string | null {
  if (!iso) return null;
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return null;
  const days = Math.round((target.getTime() - Date.now()) / 86_400_000);
  if (days <= 0) return "сегодня";
  if (days === 1) return "завтра";
  return `через ${days} дн.`;
}

export function TopicBoard({ goalId }: { goalId: string }) {
  const [topics, setTopics] = useState<LearningTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTitle, setNewTitle] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [explaining, setExplaining] = useState(false);

  const load = useCallback(async () => {
    try {
      setTopics(await import("@/lib/api/learning").then((m) => m.listTopics(goalId)));
    } catch (error) {
      toast.error("Не удалось загрузить темы", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  const missingExplanation = topics.filter((t) => t.has_explanation === false);
  const missingAtRunStart = useRef(0);

  // Progress is derived from the topics themselves rather than tracked by hand:
  // whatever is no longer missing since the run started has been written.
  const explainedDuringRun = explaining
    ? Math.max(0, missingAtRunStart.current - missingExplanation.length)
    : 0;

  /**
   * FR-7.1 for the whole goal at once.
   *
   * The request only queues the work, so the topics are polled until they stop
   * changing. Polling rather than a push: the API has no progress channel for
   * this, and a topic's explanation appears as an ordinary row update.
   *
   * The counter follows the drop in the number of topics still missing one,
   * which is the only progress signal the endpoint gives us.
   */
  useEffect(() => {
    if (!explaining) return;
    if (missingExplanation.length === 0) {
      setExplaining(false);
      return;
    }
    const timer = setTimeout(() => {
      void load();
    }, 3000);
    return () => clearTimeout(timer);
  }, [explaining, missingExplanation.length, load]);

  async function handleExplainMissing() {
    setExplaining(true);
    missingAtRunStart.current = missingExplanation.length;
    try {
      const { explainMissingTopics } = await import("@/lib/api/learning");
      const result = await explainMissingTopics(goalId);
      if (result.queued === 0) {
        toast.success(result.message);
        setExplaining(false);
        return;
      }
      toast.success(
        `${result.message}. Тема обновляется по мере готовности — это занимает около минуты.`,
      );
    } catch (error) {
      setExplaining(false);
      toast.error("Не удалось запустить объяснения", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  /**
   * TZ 6.1 step 1. A goal without subtopics blocks the diagnostic (FR-4) and
   * the planner (FR-6), so this is the recovery path when the automatic
   * decomposition did not happen or produced nothing useful.
   *
   * Reachable only when the goal has no topics at all, which is the whole
   * safety story here: `generateTopics` *replaces* the topic set and the delete
   * cascades to every topic state, plan item, drill, question and answer. The
   * button is rendered from the empty branch below, so the destructive path has
   * no entry point. Do not move this call into the populated branch without
   * adding a confirmation that names what is lost.
   */
  async function handleGenerate() {
    setGenerating(true);
    try {
      const created = await generateTopics(goalId);
      setTopics(created);
      toast.success(`Тема разбита на ${created.length} подтем`);
    } catch (error) {
      toast.error("Не удалось разбить тему на подтемы", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setGenerating(false);
    }
  }

  async function handleMark(topic: LearningTopic, manual: "known" | "skip" | null) {
    setBusyId(topic.id);
    try {
      const updated = await updateTopic(topic.id, { manual_status: manual });
      setTopics((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      if (manual === "known") {
        toast.success("Тема помечена как освоенная — попадёт в план с меньшим приоритетом");
      }
    } catch (error) {
      toast.error("Не удалось обновить тему", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusyId(null);
    }
  }

  async function handleAdd() {
    const title = newTitle.trim();
    if (!title) return;
    try {
      const created = await addTopics(goalId, [title]);
      setTopics((prev) => [...prev, ...created]);
      setNewTitle("");
      toast.success("Тема добавлена");
    } catch (error) {
      toast.error("Не удалось добавить тему", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  if (loading) {
    return (
      <div className="space-y-2" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  if (topics.length === 0) {
    // Without subtopics nothing else in the flow can start, so make this the
    // only offered action rather than showing an empty list.
    return (
      <div className="card-shadow rounded-2xl border border-border bg-card p-8 text-center">
        <h2 className="text-lg font-semibold tracking-tight text-foreground">
          Подтем пока нет
        </h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
          Диагностика и план не запустятся без подтем. Разобьём тему на части
          автоматически — или добавьте их вручную.
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Button onClick={handleGenerate} disabled={generating}>
            {generating ? "Разбиваем…" : "Разбить на подтемы"}
          </Button>
        </div>
        <div className="mx-auto mt-6 flex max-w-md gap-2">
          <Input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleAdd();
            }}
            placeholder="Или добавьте тему вручную"
            aria-label="Название темы"
          />
          <Button variant="secondary" onClick={handleAdd} disabled={!newTitle.trim()}>
            Добавить
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* FR-7.1. A note and a quiz are both refused without an explanation, so
          a topic without one cannot be studied — saying which those are is the
          difference between "the app is empty" and "read these three". */}
      {missingExplanation.length > 0 && (
        <div className="card-shadow rounded-2xl border border-warning/40 bg-warning-muted/30 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">
                Без объяснения: {missingExplanation.length}{" "}
                {missingExplanation.length === 1 ? "тема" : "тем"}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Конспект и тест составляются из объяснения. Пока его нет, эти темы
                нельзя проработать.
              </p>
            </div>
            <Button size="sm" onClick={handleExplainMissing} disabled={explaining}>
              {explaining
                ? `Готовим… ${explainedDuringRun}/${missingExplanation.length}`
                : "Объяснить все"}
            </Button>
          </div>
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {missingExplanation.slice(0, 8).map((topic) => (
              <li key={topic.id}>
                <Link
                  href={`/learning/goals/${goalId}/topic/${topic.id}`}
                  className="inline-block max-w-[22rem] truncate rounded-full bg-card px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  {topic.title}
                </Link>
              </li>
            ))}
            {missingExplanation.length > 8 && (
              <li className="px-2.5 py-1 text-xs text-muted-foreground">
                и ещё {missingExplanation.length - 8}
              </li>
            )}
          </ul>
        </div>
      )}

      <ul className="space-y-2">
        {topics.map((topic) => {
          const strength = topic.strength ?? 0;
          const status: TopicStatus = topic.status ?? "not_started";
          const review = nextReviewLabel(topic.next_review_at);
          return (
            <li
              key={topic.id}
              className="card-shadow relative rounded-xl border border-border bg-card p-4 transition-colors hover:border-brand/50"
            >
              {/* FR-7.1: whole card opens the topic explanation. The overlay
                  sits at z-0 and the action buttons at z-20, so the buttons
                  stay clickable on top of it. */}
              <Link
                href={`/learning/goals/${goalId}/topic/${topic.id}`}
                className="absolute inset-0 z-0 rounded-xl"
                aria-label={`Открыть тему «${topic.title}»`}
              />
              <div className="pointer-events-none relative z-10 flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-foreground">{topic.title}</span>
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-xs font-medium",
                        STATUS_TONE[status],
                      )}
                    >
                      {STATUS_LABEL[status]}
                    </span>
                    {topic.manual_status === "skip" && (
                      <Badge variant="outline" className="text-xs">
                        пропущена
                      </Badge>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {topic.estimated_minutes} мин
                    {review ? ` · повтор ${review}` : ""}
                  </p>
                </div>
              </div>

              <div className="mt-3">
                <StrengthBar value={strength} />
              </div>

              <div className="absolute bottom-3 right-3 z-20 flex gap-1.5">
                {topic.manual_status !== "known" && (
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={busyId === topic.id}
                    onClick={() => handleMark(topic, "known")}
                  >
                    Уже знаю
                  </Button>
                )}
                {topic.manual_status !== "skip" && (
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={busyId === topic.id}
                    onClick={() => handleMark(topic, "skip")}
                  >
                    Пропустить
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <div className="flex gap-2">
        <Input
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleAdd();
          }}
          placeholder="Добавить тему вручную"
          aria-label="Название новой темы"
        />
        <Button variant="secondary" onClick={handleAdd} disabled={!newTitle.trim()}>
          Добавить
        </Button>
      </div>
    </div>
  );
}