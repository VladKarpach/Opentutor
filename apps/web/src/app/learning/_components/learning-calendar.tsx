"use client";

/**
 * The goal's full day-by-day calendar.
 *
 * The plan already stores one row per scheduled item, but the learner had to
 * read those rows as a list. This view turns them into the shape people actually
 * plan in: weeks with a card per day, the minutes on that day, and the topics
 * scheduled for it.
 *
 * The old view showed one week at a time, which hid most of a 30-day goal. This
 * one renders every week of the plan, grouped by week, so "what happens on all
 * days of this goal" has one answer.
 *
 * Working ahead is deliberately visible rather than hidden. When today is
 * finished, the next study day's card offers "Взять на сегодня". The backend
 * moves that day's work to today and leaves a 40% review on the original day, so
 * the calendar does not become empty tomorrow just because the learner was early.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  CalendarDays,
  Check,
  Clock3,
  GraduationCap,
  ListChecks,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  getPlan,
  pullDailyDay,
  pullPlanItemAhead,
  type Plan,
  type PlanItem,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";

const DAY_MS = 86_400_000;
const PULL_REVIEW_PREFIX = "досрочное повторение";

const TYPE_LABEL: Record<string, string> = {
  study: "Теория + практика",
  review: "Повторение",
  practice: "Задания для допуска",
  exam: "Экзамен",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "запланировано",
  done: "выполнено",
  skipped: "пропущено",
  missed: "просрочено",
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function toUtcDate(iso: string): Date {
  return new Date(`${iso}T00:00:00.000Z`);
}

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function startOfWeek(date: Date): Date {
  const day = date.getUTCDay();
  const offset = (day + 6) % 7; // Monday first
  return new Date(date.getTime() - offset * DAY_MS);
}

function addDays(date: Date, days: number): Date {
  return new Date(date.getTime() + days * DAY_MS);
}

function dayLabel(iso: string): string {
  const date = toUtcDate(iso);
  return date.toLocaleDateString("ru-RU", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

function shortDate(iso: string): string {
  const date = toUtcDate(iso);
  return date.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
}

function weekRangeLabel(startIso: string): string {
  const start = toUtcDate(startIso);
  const end = addDays(start, 6);
  return `${shortDate(isoDate(start))} — ${shortDate(isoDate(end))}`;
}

function isPending(item: PlanItem): boolean {
  return !item.rejected && (item.status === "pending" || item.status === "missed");
}

function isShortReview(item: PlanItem): boolean {
  return (item.reason ?? "").startsWith(PULL_REVIEW_PREFIX);
}

function typeLabel(item: PlanItem): string {
  if (isShortReview(item)) return "Короткое повторение";
  return TYPE_LABEL[item.item_type] ?? item.item_type;
}

function dayMinutes(items: PlanItem[]): number {
  return items
    .filter((item) => !item.rejected)
    .reduce((total, item) => total + Math.max(0, item.planned_minutes), 0);
}

function statusTone(item: PlanItem): string {
  if (item.status === "done") return "text-success";
  if (item.status === "missed") return "text-destructive";
  if (item.status === "skipped") return "text-muted-foreground";
  return "text-foreground";
}

function itemHref(goalId: string, item: PlanItem): string {
  if (item.item_type === "exam") return `/learning/goals/${goalId}?tab=exam`;
  if (item.item_type === "practice") {
    return `/learning/goals/${goalId}?tab=practice`;
  }
  if (item.topic_id) {
    return `/learning/goals/${goalId}/topic/${item.topic_id}`;
  }
  return `/learning/goals/${goalId}?tab=plan`;
}

export function LearningCalendar({
  goalId,
  isActive = false,
}: {
  goalId: string;
  /** The goal the learner is working on now, marked in the header. */
  isActive?: boolean;
}) {
  const router = useRouter();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pulling, setPulling] = useState(false);
  const [pullingItemId, setPullingItemId] = useState<string | null>(null);

  const today = todayIso();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPlan(await getPlan(goalId));
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "План ещё не составлен. Откройте вкладку «План и темы».",
      );
      setPlan(null);
    } finally {
      setLoading(false);
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  const planDays = useMemo(
    () => Object.keys(plan?.days ?? {}).sort(),
    [plan],
  );

  // Monday-first weeks covering the whole plan, not only the current one.
  const weeks = useMemo(() => {
    if (planDays.length === 0) return [] as string[][];
    const start = startOfWeek(toUtcDate(planDays[0]));
    const lastWeekStart = startOfWeek(
      toUtcDate(planDays[planDays.length - 1]),
    );
    const out: string[][] = [];
    for (
      let cursor = start;
      cursor.getTime() <= lastWeekStart.getTime();
      cursor = addDays(cursor, 7)
    ) {
      out.push(
        Array.from({ length: 7 }, (_, index) =>
          isoDate(addDays(cursor, index)),
        ),
      );
    }
    return out;
  }, [planDays]);

  // Overdue items live on earlier keys, not on today's key. They still block an
  // early pull on the server, so the banner must count them as unfinished work.
  const todayPending = Boolean(
    plan &&
      Object.keys(plan.days).some(
        (day) => day <= today && (plan.days[day] ?? []).some(isPending),
      ),
  );

  const nextPullDay = useMemo(() => {
    if (!plan) return null;
    const future = Object.keys(plan.days)
      .filter((day) => day > today)
      .sort();
    return (
      future.find((day) => {
        const items = plan.days[day] ?? [];
        // The exam and the task-only day are never pulled: the server rejects
        // them, because the exam needs the practice that comes before it.
        if (
          items.some(
            (item) =>
              isPending(item) &&
              (item.item_type === "exam" || item.item_type === "practice"),
          )
        ) {
          return false;
        }
        return items.some(
          (item) =>
            isPending(item) &&
            item.item_type !== "exam" &&
            item.item_type !== "practice" &&
            !isShortReview(item),
        );
      }) ?? null
    );
  }, [plan, today]);

  const canPull = Boolean(nextPullDay) && !todayPending && !pulling;

  // Copies already pulled for today are today's work, not unfinished work that
  // should block taking another task. The server draws the same line.
  const ownTodayPending = Boolean(
    plan &&
      Object.keys(plan.days).some(
        (day) =>
          day <= today &&
          (plan.days[day] ?? []).some(
            (item) =>
              isPending(item) &&
              !(item.reason ?? "").startsWith("взято наперёд с "),
          ),
      ),
  );

  function canPullItem(item: PlanItem, day: string): boolean {
    return (
      day > today &&
      !ownTodayPending &&
      isPending(item) &&
      item.item_type !== "exam" &&
      !isShortReview(item) &&
      !(item.reason ?? "").includes("взято наперёд на")
    );
  }

  async function handlePull(day: string) {
    if (!canPull) return;
    setPulling(true);
    try {
      const result = await pullDailyDay(goalId, day);
      toast.success("Занятие перенесено на сегодня", {
        description: result.message ?? undefined,
      });
      // The moved items are now part of today's session. `fresh=1` tells the
      // session to ignore the old localStorage position from an earlier visit.
      router.push(`/learning/goals/${goalId}/today?fresh=1`);
    } catch (err) {
      toast.error("Не удалось взять занятие", {
        description: err instanceof Error ? err.message : undefined,
      });
      setPulling(false);
    }
  }

  async function handlePullItem(item: PlanItem) {
    setPullingItemId(item.id);
    try {
      const result = await pullPlanItemAhead(goalId, item.id);
      toast.success("Задание взято на сегодня", {
        description: `На ${dayLabel(result.original.day)} осталось ${result.original.planned_minutes} мин.`,
      });
      await load();
    } catch (err) {
      toast.error("Не удалось взять задание наперёд", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setPullingItemId(null);
    }
  }

  function scrollToToday() {
    document
      .getElementById(`goal-day-${today}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  if (loading) {
    return (
      <div className="space-y-3" aria-busy="true">
        <div className="h-24 animate-pulse rounded-2xl bg-muted" />
        {Array.from({ length: 3 }, (_, index) => (
          <div
            key={index}
            className="h-56 animate-pulse rounded-2xl border border-border bg-muted/40"
          />
        ))}
      </div>
    );
  }

  if (!plan) {
    return (
      <div className="rounded-2xl border border-border bg-card p-6">
        <div className="flex items-center gap-2 text-foreground">
          <CalendarDays className="h-5 w-5 text-brand" />
          <h2 className="text-lg font-semibold">Календарь</h2>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          {error ??
            "План ещё не составлен. Откройте вкладку «План и темы», чтобы распределить темы по дням."}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <CalendarDays className="h-5 w-5 text-brand" />
              <h2 className="text-lg font-semibold text-foreground">
                Календарь цели
              </h2>
              {isActive && (
                <Badge className="bg-brand text-brand-foreground">
                  Активная цель · сейчас
                </Badge>
              )}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {planDays.length > 0
                ? `${shortDate(planDays[0])} — ${shortDate(
                    planDays[planDays.length - 1],
                  )} · все дни плана`
                : "В плане пока нет дней"}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={scrollToToday}>
            Сегодня
          </Button>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-border bg-muted/30 px-3 py-2 text-sm">
          <Clock3 className="h-4 w-4 text-muted-foreground" />
          {todayPending ? (
            <span className="text-muted-foreground">
              Сначала завершите сегодняшние и просроченные задания — потом
              можно взять следующий день.
            </span>
          ) : nextPullDay ? (
            <>
              <span className="text-foreground">
                Сегодня всё выполнено. Можно взять занятие на{" "}
                {dayLabel(nextPullDay)}.
              </span>
              <Button
                size="xs"
                onClick={() => void handlePull(nextPullDay)}
                disabled={!canPull}
              >
                {pulling ? (
                  <>
                    <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                    Переносим…
                  </>
                ) : (
                  "Взять на сегодня"
                )}
              </Button>
            </>
          ) : (
            <span className="text-muted-foreground">
              Впереди нет учебных дней, которые можно взять заранее. Дни
              практики и экзамена остаются на своих датах.
            </span>
          )}
        </div>
      </div>

      {weeks.length === 0 && (
        <div className="rounded-2xl border border-dashed border-border bg-card p-6 text-sm text-muted-foreground">
          План пока пуст. Откройте вкладку «План и темы» и постройте его.
        </div>
      )}

      {weeks.map((week) => (
        <section
          key={week[0]}
          className="rounded-2xl border border-border bg-card p-3 sm:p-4"
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-foreground">
              {weekRangeLabel(week[0])}
            </h3>
            <span className="text-xs text-muted-foreground">
              {week.includes(today) ? "текущая неделя" : "по плану"}
            </span>
          </div>

          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-7">
            {week.map((day) => {
              const items = plan.days[day] ?? [];
              const visible = items.filter((item) => !item.rejected);
              const isToday = day === today;
              const isPast = day < today;
              const isNextToPull = day === nextPullDay;
              const minutes = dayMinutes(visible);

              return (
                <section
                  key={day}
                  id={`goal-day-${day}`}
                  className={cn(
                    "flex min-h-48 flex-col rounded-xl border p-2.5",
                    isToday
                      ? "border-brand bg-brand-muted/30"
                      : "border-border bg-background/60",
                    isPast && !isToday && "opacity-75",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p
                        className={cn(
                          "text-[11px] font-medium uppercase tracking-wide",
                          isToday ? "text-brand" : "text-muted-foreground",
                        )}
                      >
                        {toUtcDate(day).toLocaleDateString("ru-RU", {
                          weekday: "short",
                          timeZone: "UTC",
                        })}
                      </p>
                      <p className="text-base font-semibold text-foreground">
                        {toUtcDate(day).getUTCDate()}
                      </p>
                    </div>
                    {isToday && (
                      <Badge className="bg-brand text-brand-foreground">
                        сегодня
                      </Badge>
                    )}
                  </div>

                  <div className="mt-1.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                    <Clock3 className="h-3 w-3" />
                    {minutes > 0 ? `${minutes} мин` : "отдых"}
                  </div>

                  <div className="mt-2 flex-1 space-y-1.5">
                    {visible.length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">
                        На этот день занятий нет.
                      </p>
                    ) : (
                      visible.map((item) => {
                        const Icon =
                          item.item_type === "exam"
                            ? GraduationCap
                            : item.item_type === "practice"
                              ? ListChecks
                              : item.status === "done"
                                ? Check
                                : isShortReview(item)
                                  ? RotateCcw
                                  : null;
                        const showPull =
                          day > today &&
                          isPending(item) &&
                          item.item_type !== "exam" &&
                          !isShortReview(item) &&
                          !(item.reason ?? "").includes("взято наперёд на");
                        return (
                          <div
                            key={item.id}
                            className="rounded-lg border border-border/80 bg-card transition-colors hover:border-brand/40"
                          >
                            <Link
                              href={itemHref(goalId, item)}
                              className="block px-2 py-1.5"
                            >
                              <div className="flex items-start gap-1.5">
                                {Icon ? (
                                  <Icon
                                    className={cn(
                                      "mt-0.5 h-3.5 w-3.5 shrink-0",
                                      item.status === "done"
                                        ? "text-success"
                                        : item.item_type === "exam"
                                          ? "text-brand"
                                          : item.item_type === "practice"
                                            ? "text-info"
                                            : "text-info",
                                    )}
                                  />
                                ) : (
                                  <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                                )}
                                <div className="min-w-0">
                                  <p
                                    className={cn(
                                      "text-[11px] font-medium leading-snug",
                                      statusTone(item),
                                    )}
                                  >
                                    {item.title}
                                  </p>
                                  <p className="text-[10px] text-muted-foreground">
                                    {typeLabel(item)} · {item.planned_minutes} мин
                                  </p>
                                  <p className="text-[10px] text-muted-foreground">
                                    {STATUS_LABEL[item.status] ?? item.status}
                                  </p>
                                  {(item.reason ?? "").includes(
                                    "взято наперёд",
                                  ) && (
                                    <p className="text-[10px] text-brand">
                                      взято наперёд
                                    </p>
                                  )}
                                </div>
                              </div>
                            </Link>
                            {showPull && (
                              <Button
                                size="xs"
                                variant="ghost"
                                className="w-full rounded-t-none border-t border-border/60 text-[10px]"
                                disabled={
                                  !canPullItem(item, day) ||
                                  pullingItemId === item.id
                                }
                                title={
                                  ownTodayPending
                                    ? "Сначала завершите сегодняшние задания"
                                    : undefined
                                }
                                onClick={() => void handlePullItem(item)}
                              >
                                {pullingItemId === item.id
                                  ? "Берём…"
                                  : "Взять наперёд"}
                              </Button>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>

                  {isToday && (
                    <Button asChild size="sm" className="mt-2 w-full">
                      <Link href={`/learning/goals/${goalId}/today`}>
                        Начать занятие
                      </Link>
                    </Button>
                  )}

                  {isNextToPull && !isToday && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="mt-2 w-full"
                      onClick={() => void handlePull(day)}
                      disabled={!canPull}
                    >
                      {pulling ? "Переносим…" : "Взять на сегодня"}
                    </Button>
                  )}
                </section>
              );
            })}
          </div>
        </section>
      ))}

      {plan.warnings.length > 0 && (
        <div className="rounded-xl border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">
          {plan.warnings[0]}
        </div>
      )}
    </div>
  );
}
