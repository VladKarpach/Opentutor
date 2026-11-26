"use client";

/**
 * The plan and the topics as one list.
 *
 * These used to be two tabs, which meant seeing "what to study" and "when" cost
 * a tab switch and the two never appeared together. Each row now carries the
 * topic, its scheduled days, the repetition state and how much of its task
 * series is done.
 *
 * A subtopic's series is prepared on demand: generating 2-3 tasks for ten topics
 * at once would be thirty model calls in a single request.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  buildPlan,
  catchUpPlan,
  getGoalSchedule,
  prepareDrill,
  type GoalSchedule,
  type ScheduledTopic,
} from "@/lib/api/learning";
import { useWorkspaceStore } from "@/store/workspace";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<string, string> = {
  not_started: "не начато",
  weak: "забыто",
  learning: "в работе",
  known: "освоено",
};

const STATUS_COLOR: Record<string, string> = {
  not_started: "bg-muted",
  weak: "bg-destructive",
  learning: "bg-info",
  known: "bg-success",
};

function shortDay(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(5);
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

function daysLabel(row: ScheduledTopic): string | null {
  const first = shortDay(row.first_day);
  const last = shortDay(row.last_day);
  if (!first) return null;
  if (!last || first === last) return first;
  // More than one session on this topic: show the span and the count.
  return `${first}–${last} · ${row.study_days.length} дн.`;
}

export function ScheduleView({
  goalId,
  onOpenPractice,
  onPlanChanged,
}: {
  goalId: string;
  /** Jump to the practice tab with this topic preselected. */
  onOpenPractice: (topicId: string) => void;
  /** Tells the parent a replan happened, so it can refresh the changes panel. */
  onPlanChanged?: () => void;
}) {
  const router = useRouter();
  const [schedule, setSchedule] = useState<GoalSchedule | null>(null);
  const [loading, setLoading] = useState(true);
  const [preparing, setPreparing] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);

  const currentMode = useWorkspaceStore((s) => s.spaceLayout.mode);
  const isInitial = useRef(true);

  const load = useCallback(async () => {
    try {
      setSchedule(await getGoalSchedule(goalId));
    } catch (error) {
      toast.error("Не удалось загрузить план", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRebuild = useCallback(
    async (rebuildMode: "plan" | "catchup") => {
      setRebuilding(true);
      try {
        // Deterministic server-side, so neither call needs the model.
        const result =
          rebuildMode === "catchup"
            ? await catchUpPlan(goalId, { mode: currentMode })
            : await buildPlan(goalId, { mode: currentMode });
        const count = Object.values(result.days ?? {}).reduce(
          (sum, group) => sum + group.length,
          0,
        );
        toast.success(
          rebuildMode === "catchup"
            ? `План пересобран после пропусков: ${count} пунктов`
            : `План перестроен: ${count} пунктов`,
        );
        await load();
        onPlanChanged?.();
      } catch (error) {
        toast.error("Не удалось перестроить план", {
          description: error instanceof Error ? error.message : undefined,
        });
      } finally {
        setRebuilding(false);
      }
    },
    [goalId, currentMode, load, onPlanChanged],
  );

  // Auto-rebuild when the learning mode changes (not on first mount).
  useEffect(() => {
    if (isInitial.current) {
      isInitial.current = false;
      return;
    }
    if (currentMode) {
      void handleRebuild("plan");
    }
  }, [currentMode, handleRebuild]);

  async function handlePrepare(topic: ScheduledTopic) {
    setPreparing(topic.topic_id);
    try {
      const result = await prepareDrill(topic.topic_id);
      toast.success(
        `Задания по теме «${topic.title}» готовы: ${result.questions.length}`,
      );
      await load();
      onOpenPractice(topic.topic_id);
    } catch (error) {
      toast.error("Не удалось подготовить задания", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setPreparing(null);
    }
  }

  if (loading) {
    return <div className="h-64 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  if (!schedule || schedule.topics.length === 0) {
    return (
      <div className="card-shadow rounded-2xl border border-border bg-card p-8 text-center">
        <p className="text-sm text-muted-foreground">
          Тем пока нет. Разбейте цель на подтемы, чтобы построить план.
        </p>
        <Button
          className="mt-4"
          onClick={async () => {
            try {
              const created = await fetch(
                `/api/learning/goals/${goalId}/topics/generate`,
                { method: "POST", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ max_topics: 10 }) },
              );
              if (!created.ok) throw new Error("не удалось");
              await load();
            } catch {
              toast.error("Не удалось разбить цель на подтемы");
            }
          }}
        >
          Разбить на подтемы
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* The gate, stated plainly rather than discovered by clicking a dead tab. */}
      <div
        className={cn(
          "rounded-2xl border p-4",
          schedule.exam_unlocked
            ? "border-success/40 bg-success-muted/40"
            : "border-border bg-card",
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium text-foreground">
              {schedule.exam_unlocked
                ? "Экзамен открыт"
                : "До экзамена: выполните задания по всем темам"}
            </p>
            <p className="text-xs text-muted-foreground">
              Готово тем: {schedule.topics_done} из {schedule.topics_total}
            </p>
          </div>
          <Button
            size="xs"
            variant={schedule.exam_unlocked ? "default" : "outline"}
            onClick={() => router.push(`/learning/goals/${goalId}`)}
          >
            Перейти к экзамену
          </Button>
        </div>
        {!schedule.exam_unlocked && schedule.pending_exam.length > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            Осталось:{" "}
            {schedule.pending_exam
              .slice(0, 3)
              .map((item) => item.title)
              .join(", ")}
            {schedule.pending_exam.length > 3
              ? ` и ещё ${schedule.pending_exam.length - 3}`
              : ""}
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="default"
          disabled={rebuilding}
          onClick={() => void handleRebuild("catchup")}
        >
          {rebuilding ? "Догоняем…" : "Догнать план"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={rebuilding}
          onClick={() => void handleRebuild("plan")}
        >
          {rebuilding ? "Перестраиваем…" : "Перестроить план"}
        </Button>
        <span className="self-center text-xs text-muted-foreground">
          Пересчёт учитывает силу тем и то, что вы уже освоили в других целях
        </span>
      </div>

      <ul className="space-y-2">
        {schedule.topics.map((row) => {
          const days = daysLabel(row);
          const strength = row.strength ?? 0;
          const drill = row.drill;
          return (
            <li
              key={row.topic_id}
              className="card-shadow rounded-xl border border-border bg-card p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-foreground">{row.title}</span>
                    {row.status && (
                      <Badge
                        className={cn(
                          "rounded-full px-2 py-0.5 text-xs font-medium",
                          STATUS_COLOR[row.status],
                        )}
                      >
                        {STATUS_LABEL[row.status] ?? row.status}
                      </Badge>
                    )}
                  </div>

                  <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    {days ? (
                      <span>📅 {days}</span>
                    ) : (
                      <span className="text-warning">не запланировано</span>
                    )}
                    <span>{row.planned_minutes || row.estimated_minutes} мин</span>
                    {row.next_review_at && (
                      <span>повтор {shortDay(row.next_review_at)}</span>
                    )}
                  </p>

                  <div className="mt-2 flex items-center gap-2">
                    <div className="h-1.5 w-32 overflow-hidden rounded-full bg-muted">
                      <div
                        className={cn(
                          "h-full rounded-full",
                          STATUS_COLOR[row.status ?? "not_started"],
                        )}
                        style={{ width: `${Math.round(strength * 100)}%` }}
                      />
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {Math.round(strength * 100)}%
                    </span>
                  </div>

                  {row.practice_items.length > 0 && (
                    <div className="mt-3">
                      <p className="text-xs font-semibold text-muted-foreground mb-1">
                        Контрольные задания ({row.practice_items.length})
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {row.practice_items.map((item) => (
                          <span
                            key={item.id}
                            className={cn(
                              "flex items-center gap-1.5 rounded border px-2 py-1 text-[10px]",
                              item.status === "done"
                                ? "bg-success/10 border-success/30 text-success"
                                : "bg-muted/50 border-border text-muted-foreground",
                            )}
                          >
                            {item.day ? shortDay(item.day) : "—"} · {item.title}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                </div>

                <div className="flex shrink-0 flex-col items-end gap-2">
                  {drill.prepared ? (
                    <>
                      <Badge
                        variant={drill.done ? "default" : "outline"}
                        className="text-xs"
                      >
                        задания: {drill.answered}/{drill.required}
                      </Badge>
                      <Button
                        size="xs"
                        variant={drill.done ? "ghost" : "secondary"}
                        onClick={() => onOpenPractice(row.topic_id)}
                      >
                        {drill.done ? "Повторить" : "Решать"}
                      </Button>
                    </>
                  ) : (
                    <Button
                      size="xs"
                      variant="secondary"
                      disabled={preparing === row.topic_id}
                      onClick={() => void handlePrepare(row)}
                    >
                      {preparing === row.topic_id
                        ? "Готовим…"
                        : "Подготовить задания"}
                    </Button>
                  )}
                  <Link
                    href={`/learning/goals/${goalId}/topic/${row.topic_id}`}
                    className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                  >
                    объяснение
                  </Link>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}