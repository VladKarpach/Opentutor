"use client";

/**
 * Plan view — TZ screen 3, FR-6.5, FR-6.6, FR-10.4.
 *
 * Shows the day-by-day plan, lets a session be marked done, and surfaces the
 * feasibility warnings the deterministic scheduler produced (FR-6.4): when the
 * deadline cannot be met, the three suggested ways out are shown rather than
 * silently truncating the plan.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  buildPlan,
  catchUpPlan,
  downloadPlanIcs,
  pullPlanItemAhead,
  updatePlanItem,
  type Plan,
  type PlanItem,
} from "@/lib/api/learning";
import { useWorkspaceStore } from "@/store/workspace";
import { cn } from "@/lib/utils";

const TYPE_LABEL: Record<string, string> = {
  study: "Изучение",
  review: "Повторение",
  practice: "Практика",
  exam: "Экзамен",
};

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-card border-border",
  done: "bg-success-muted border-success/30",
  skipped: "bg-muted/60 border-border opacity-70",
  missed: "bg-destructive/5 border-destructive/30",
};

function formatDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((date.getTime() - today.getTime()) / 86_400_000);
  const base = date.toLocaleDateString("ru-RU", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
  if (diff === 0) return `${base} · сегодня`;
  if (diff === 1) return `${base} · завтра`;
  return base;
}

function minutesLabel(value: number): string {
  if (value < 60) return `${value} мин`;
  const hours = Math.floor(value / 60);
  const rest = value % 60;
  return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
}

export function PlanView({
  goalId,
  /**
   * Fired after anything that changes the plan itself. The schedule and the
   * change list sit next to this component and read the same goal, so without
   * this they would keep showing the plan that was replaced a moment ago.
   */
  onPlanChanged,
}: {
  goalId: string;
  onPlanChanged?: () => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pullingId, setPullingId] = useState<string | null>(null);

  const currentMode = useWorkspaceStore((s) => s.spaceLayout.mode);
  const isInitial = useRef(true);

  const load = useCallback(async () => {
    try {
      setPlan(await import("@/lib/api/learning").then((m) => m.getPlan(goalId)));
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

  const handleBuild = useCallback(
    async (rebuildMode: "normal" | "catchup") => {
      setBuilding(true);
      try {
        const next =
          rebuildMode === "catchup"
            ? await catchUpPlan(goalId, { mode: currentMode })
            : await buildPlan(goalId, { mode: currentMode });
        setPlan(next);
        onPlanChanged?.();
        toast.success(
          rebuildMode === "catchup" ? "План смягчён после пропусков" : "План перестроен",
        );
      } catch (error) {
        toast.error("Не удалось построить план", {
          description: error instanceof Error ? error.message : undefined,
        });
      } finally {
        setBuilding(false);
      }
    },
    [goalId, currentMode, onPlanChanged],
  );

  // Auto-rebuild when the learning mode changes (not on first mount).
  useEffect(() => {
    if (isInitial.current) {
      isInitial.current = false;
      return;
    }
    if (currentMode) {
      void handleBuild("normal");
    }
  }, [currentMode, handleBuild]);

  async function handleComplete(item: PlanItem) {
    setBusyId(item.id);
    try {
      const updated = await updatePlanItem(goalId, item.id, { status: "done" });
      setPlan((prev) =>
        prev
          ? {
              ...prev,
              days: Object.fromEntries(
                Object.entries(prev.days).map(([day, items]) => [
                  day,
                  items.map((i) => (i.id === updated.id ? { ...i, ...updated } : i)),
                ]),
              ),
            }
          : prev,
      );
      onPlanChanged?.();
    } catch (error) {
      toast.error("Не удалось отметить выполненным", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusyId(null);
    }
  }

  async function handlePullAhead(item: PlanItem) {
    setPullingId(item.id);
    try {
      const result = await pullPlanItemAhead(goalId, item.id);
      await load();
      onPlanChanged?.();
      toast.success("Задание взято на сегодня", {
        description: `На ${result.original.day} осталось ${result.original.planned_minutes} мин.`,
      });
    } catch (error) {
      toast.error("Не удалось взять задание наперёд", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setPullingId(null);
    }
  }

  async function handleExport() {
    try {
      const { blob, fileName } = await downloadPlanIcs(goalId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success("Файл календаря скачан");
    } catch (error) {
      toast.error("Не удалось выгрузить план", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  const days = plan ? Object.entries(plan.days).sort(([a], [b]) => a.localeCompare(b)) : [];
  const todayIso = new Date().toISOString().slice(0, 10);
  // The server refuses an early pull while today's own work is still open.
  // Reflecting that here explains the disabled state instead of a failed click.
  const hasPendingToday = days.some(
    ([day, items]) =>
      day <= todayIso &&
      items.some(
        (item) =>
          !item.rejected &&
          (item.status === "pending" || item.status === "missed") &&
          // Copies already taken for today are the result of working ahead,
          // not unfinished work that blocks taking another task.
          !(item.reason ?? "").startsWith("взято наперёд с "),
      ),
  );

  function canPullAhead(item: PlanItem): boolean {
    return (
      !hasPendingToday &&
      item.day > todayIso &&
      !item.rejected &&
      (item.status === "pending" || item.status === "missed") &&
      item.item_type !== "exam" &&
      !(item.reason ?? "").includes("взято наперёд на")
    );
  }

  if (loading) {
    return <div className="h-48 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  if (!plan || days.length === 0) {
    return (
      <div className="card-shadow rounded-2xl border border-border bg-card p-8 text-center">
        <p className="text-sm text-muted-foreground">
          План ещё не построен. Добавьте темы и нажмите «Перестроить».
        </p>
        <Button
          className="mt-4"
          onClick={() => handleBuild("normal")}
          disabled={building}
        >
          {building ? "Строим…" : "Перестроить план"}
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{minutesLabel(plan.total_minutes)} всего</Badge>
        {!plan.feasible && (
          <Badge className="bg-warning text-warning-foreground">Срок напряжённый</Badge>
        )}
        {plan.speed !== 1 && (
          <Badge variant="outline">темп ×{plan.speed.toFixed(2)}</Badge>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="default"
            onClick={() => handleBuild("catchup")}
            disabled={building}
          >
            {building ? "Догоняем…" : "Догнать план"}
          </Button>
          <Button size="sm" variant="outline" onClick={handleExport}>
            В календарь
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => handleBuild("normal")}
            disabled={building}
          >
            Перестроить
          </Button>
        </div>
      </div>

      {/* FR-6.4: warn loudly and offer the three ways out. */}
      {plan.warnings.length > 0 && (
        <div className="rounded-xl border border-warning/40 bg-warning-muted px-4 py-3">
          {plan.warnings.map((warning) => (
            <p key={warning} className="text-sm text-foreground">
              {warning}
            </p>
          ))}
          {plan.suggestions.length > 0 && (
            <ul className="mt-2 list-inside list-disc space-y-0.5">
              {plan.suggestions.map((suggestion) => (
                <li key={suggestion} className="text-sm text-muted-foreground">
                  {suggestion}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="space-y-4">
        {days.map(([day, items]) => (
          <section key={day}>
            <h3 className="mb-2 text-sm font-semibold text-muted-foreground">
              {formatDay(day)}
            </h3>
            <ul className="space-y-2">
              {items.map((item) => (
                <li
                  key={item.id}
                  className={cn(
                    "flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3",
                    STATUS_STYLE[item.status],
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline" className="text-xs">
                        {TYPE_LABEL[item.item_type] ?? item.item_type}
                      </Badge>
                      <span className="text-sm font-medium text-foreground">
                        {item.title}
                      </span>
                      {(item.reason ?? "").includes("взято наперёд") && (
                        <Badge variant="secondary" className="text-[10px]">
                          взято наперёд
                        </Badge>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {minutesLabel(item.planned_minutes)}
                      {item.actual_minutes ? ` · факт ${minutesLabel(item.actual_minutes)}` : ""}
                    </p>
                  </div>
                  {item.status !== "done" &&
                    item.day > todayIso &&
                    item.item_type !== "exam" &&
                    !(item.reason ?? "").includes("взято наперёд на") && (
                    <Button
                      size="xs"
                      variant="secondary"
                      disabled={!canPullAhead(item) || pullingId === item.id}
                      title={
                        hasPendingToday
                          ? "Сначала завершите сегодняшние задания"
                          : undefined
                      }
                      onClick={() => void handlePullAhead(item)}
                    >
                      {pullingId === item.id ? "Берём…" : "Взять наперёд"}
                    </Button>
                  )}
                  {item.status !== "done" && (
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={busyId === item.id}
                      onClick={() => handleComplete(item)}
                    >
                      Готово
                    </Button>
                  )}
                  {item.status === "done" && (
                    <Badge className="bg-success-muted text-success">выполнено</Badge>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}