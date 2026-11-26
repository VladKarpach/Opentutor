"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import Link from "next/link";
import {
  ArrowRight,
  CalendarDays,
  Clock3,
  Compass,
  GraduationCap,
  ListChecks,
  RotateCcw,
  Sparkles,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogClose,
} from "@/components/ui/dialog";
import {
  getLearningIndex,
  getPlan,
  type LearningGoal,
  type Plan,
  type PlanItem,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/store/workspace";
import { useT } from "@/lib/i18n-context";
import type { LearningMode } from "@/lib/block-system/types";

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

interface GoalCalendar {
  goal: LearningGoal;
  plan: Plan;
}

interface GoalSchedule {
  goal: LearningGoal;
  /** Day → items as rendered in this global calendar. */
  days: Record<string, PlanItem[]>;
  /** First and last rendered day (never earlier than today). */
  first: string;
  last: string;
  done: number;
  total: number;
  /** The goal's plan started before today and continues into this range. */
  continuation: boolean;
  /** A stale plan with unfinished work, laid out from today. */
  projected: boolean;
  /** First and last day of the stored plan, before any projection. */
  originalFirst: string;
  originalLast: string;
  /** There is work to show at or after today. */
  hasFuture: boolean;
  /** The goal's deadline, if set (ISO date). */
  deadline: string | null;
  /** Chosen learning mode for this schedule. */
  mode: LearningMode;
}

interface DayGroup {
  goal: LearningGoal;
  items: PlanItem[];
  isActive: boolean;
  isStart: boolean;
  isContinuation: boolean;
  isProjectedStart: boolean;
  projected: boolean;
  projectedFrom: string | null;
  isEnd: boolean;
}

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
  const offset = (day + 6) % 7;
  return new Date(date.getTime() - offset * DAY_MS);
}

function addDays(date: Date, days: number): Date {
  return new Date(date.getTime() + days * DAY_MS);
}

function shortDate(iso: string): string {
  return toUtcDate(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
}

function weekRangeLabel(week: string[]): string {
  return `${shortDate(week[0])} — ${shortDate(week[week.length - 1])}`;
}

function isVisible(item: PlanItem): boolean {
  return !item.rejected;
}

function isActionable(item: PlanItem): boolean {
  return !item.rejected && (item.status === "pending" || item.status === "missed");
}

function isShortReview(item: PlanItem): boolean {
  return (item.reason ?? "").startsWith(PULL_REVIEW_PREFIX);
}

const MODE_ICONS: Record<LearningMode, typeof GraduationCap> = {
  course_following: GraduationCap,
  self_paced: Compass,
};

function ModeIcon({ mode, className }: { mode: LearningMode; className: string }) {
  const Icon = MODE_ICONS[mode];
  return <Icon className={className} aria-hidden="true" />;
}

function typeLabel(item: PlanItem): string {
  if (isShortReview(item)) return "Короткое повторение";
  return TYPE_LABEL[item.item_type] ?? item.item_type;
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

function statusTone(item: PlanItem): string {
  if (item.status === "done") return "text-success";
  if (item.status === "missed") return "text-destructive";
  if (item.status === "skipped") return "text-muted-foreground";
  return "text-foreground";
}

function allGoalsFrom(index: Awaited<ReturnType<typeof getLearningIndex>>) {
  const byId = new Map<string, LearningGoal>();
  for (const course of index.courses) {
    for (const goal of course.goals) byId.set(goal.id, goal);
  }
  for (const goal of index.unattached) byId.set(goal.id, goal);
  return Array.from(byId.values());
}

export function GlobalCalendar() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [goals, setGoals] = useState<LearningGoal[]>([]);
  const [entries, setEntries] = useState<GoalCalendar[]>([]);
  const [activeGoalId, setActiveGoalId] = useState<string | null>(null);
  const [modeModalOpen, setModeModalOpen] = useState(false);

  const t = useT();
  const currentMode = useWorkspaceStore((s) => s.spaceLayout.mode);
  const setMode = useWorkspaceStore((s) => s.setMode);

  const today = todayIso();

  const MODE_LABELS: Record<LearningMode, string> = {
    course_following: t("mode.course_following"),
    self_paced: t("mode.self_paced"),
  };

  const MODE_COLORS: Record<LearningMode, string> = {
    course_following: "bg-brand text-brand-foreground",
    self_paced: "bg-info text-info-foreground",
  };

  /** Режим обучения, которым руководствуется календарь.
   *  Пользователь выбирает его в модалке "Сменить режим". */
  const activeMode: LearningMode = currentMode ?? "course_following";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const index = await getLearningIndex();
      const all = allGoalsFrom(index);
      setGoals(all);
      setActiveGoalId(index.active_goal_id);

      const settled = await Promise.allSettled(
        all.map(async (goal) => ({ goal, plan: await getPlan(goal.id) })),
      );
      const next: GoalCalendar[] = [];
      settled.forEach((result) => {
        if (result.status === "rejected") return;
        const hasItems = Object.values(result.value.plan.days).some((items) =>
          items.some(isVisible),
        );
        if (hasItems) next.push(result.value);
      });
      setEntries(next);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Не удалось загрузить общий календарь.",
      );
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const activeGoal = useMemo(
    () => goals.find((goal) => goal.id === activeGoalId) ?? null,
    [goals, activeGoalId],
  );

  const schedules = useMemo<GoalSchedule[]>(() => {
    const out: GoalSchedule[] = [];
    for (const { goal, plan } of entries) {
      const originalDays = Object.keys(plan.days)
        .filter((day) => (plan.days[day] ?? []).some(isVisible))
        .sort();
      if (originalDays.length === 0) continue;

      const originalFirst = originalDays[0];
      const originalLast = originalDays[originalDays.length - 1];

      // ─── В своём темпе ─────────────────────────────────────────────
      // Невыполненные задания переносятся на следующий день.
      // Курс может выходить за изначальные сроки — дедлайн игнорируется.
      if (activeMode === "self_paced") {
        const pendingDays = originalDays.filter((day) =>
          (plan.days[day] ?? []).some(isActionable),
        );
        if (pendingDays.length === 0) {
          const allItems = originalDays
            .flatMap((day) => plan.days[day] ?? [])
            .filter(isVisible);
          out.push({
            goal,
            days: {},
            first: originalLast,
            last: originalLast,
            done: allItems.filter((item) => item.status === "done").length,
            total: allItems.length,
            continuation: false,
            projected: false,
            originalFirst,
            originalLast,
            hasFuture: false,
            deadline: null,
            mode: activeMode,
          });
          continue;
        }

        const days: Record<string, PlanItem[]> = {};
        let total = 0;
        pendingDays.forEach((originalDay, index) => {
          const displayDay = isoDate(addDays(toUtcDate(today), index));
          const items = (plan.days[originalDay] ?? []).filter(isActionable);
          days[displayDay] = items;
          total += items.length;
        });
        out.push({
          goal,
          days,
          first: today,
          last: isoDate(addDays(toUtcDate(today), pendingDays.length - 1)),
          done: 0,
          total,
          continuation: false,
          projected: true,
          originalFirst,
          originalLast,
          hasFuture: true,
          deadline: null,
          mode: activeMode,
        });
        continue;
      }

      // ─── По программе курса ────────────────────────────────────────
      // План перестраивается, чтобы успеть выполнить все задания в срок.
      const futureDays = originalDays.filter((day) => day >= today);

      if (futureDays.length > 0) {
        const days: Record<string, PlanItem[]> = {};
        for (const day of futureDays) {
          days[day] = (plan.days[day] ?? []).filter(isVisible);
        }
        const visibleItems = futureDays
          .flatMap((day) => days[day] ?? [])
          .filter(isVisible);
        out.push({
          goal,
          days,
          first: futureDays[0],
          last: futureDays[futureDays.length - 1],
          done: visibleItems.filter((item) => item.status === "done").length,
          total: visibleItems.length,
          continuation: originalFirst < today,
          projected: false,
          originalFirst,
          originalLast,
          hasFuture: true,
          deadline: goal.deadline ?? null,
          mode: activeMode,
        });
        continue;
      }

      const pendingDays = originalDays.filter((day) =>
        (plan.days[day] ?? []).some(isActionable),
      );
      if (pendingDays.length === 0) {
        const allItems = originalDays
          .flatMap((day) => plan.days[day] ?? [])
          .filter(isVisible);
        out.push({
          goal,
          days: {},
          first: originalLast,
          last: originalLast,
          done: allItems.filter((item) => item.status === "done").length,
          total: allItems.length,
          continuation: false,
          projected: false,
          originalFirst,
          originalLast,
          hasFuture: false,
          deadline: goal.deadline ?? null,
          mode: activeMode,
        });
        continue;
      }

      const days: Record<string, PlanItem[]> = {};
      let total = 0;
      pendingDays.forEach((originalDay, index) => {
        const displayDay = isoDate(addDays(toUtcDate(today), index));
        const items = (plan.days[originalDay] ?? []).filter(isActionable);
        days[displayDay] = items;
        total += items.length;
      });
      out.push({
        goal,
        days,
        first: today,
        last: isoDate(addDays(toUtcDate(today), pendingDays.length - 1)),
        done: 0,
        total,
        continuation: false,
        projected: true,
        originalFirst,
        originalLast,
        hasFuture: true,
        deadline: goal.deadline ?? null,
        mode: activeMode,
      });
    }
    return out;
  }, [entries, today, activeMode]);

  const schedulesByMode = useMemo(() => {
    const grouped = new Map<LearningMode, GoalSchedule[]>();
    for (const schedule of schedules) {
      const mode = schedule.mode;
      const list = grouped.get(mode) ?? [];
      list.push(schedule);
      grouped.set(mode, list);
    }
    return grouped;
  }, [schedules]);

  const scheduleById = useMemo(
    () => new Map(schedules.map((schedule) => [schedule.goal.id, schedule])),
    [schedules],
  );

  const allDays = useMemo(() => {
    const planKeys = schedules.flatMap((schedule) =>
      Object.keys(schedule.days),
    );
    const deadlineKeys = schedules
      .map((schedule) => schedule.deadline)
      .filter(
        (d): d is string => d !== null && d >= today,
      );
    const allKeys = [...planKeys, ...deadlineKeys];
    if (allKeys.length === 0) return [] as string[];

    const last = allKeys.sort()[allKeys.length - 1];
    const out: string[] = [];
    for (
      let cursor = toUtcDate(today);
      cursor.getTime() <= toUtcDate(last).getTime();
      cursor = addDays(cursor, 1)
    ) {
      out.push(isoDate(cursor));
    }
    return out;
  }, [schedules, today]);

  const weeks = useMemo(() => {
    if (allDays.length === 0) return [] as string[][];
    const first = toUtcDate(allDays[0]);
    const last = toUtcDate(allDays[allDays.length - 1]);
    const start = startOfWeek(first);
    const lastWeek = startOfWeek(last);
    const out: string[][] = [];
    for (
      let cursor = start;
      cursor.getTime() <= lastWeek.getTime();
      cursor = addDays(cursor, 7)
    ) {
      const week = Array.from({ length: 7 }, (_, index) =>
        isoDate(addDays(cursor, index)),
      ).filter((day) => day >= allDays[0] && day <= allDays[allDays.length - 1]);
      if (week.length > 0) out.push(week);
    }
    return out;
  }, [allDays]);

  const groupsFor = useCallback(
    (day: string): DayGroup[] => {
      const byGoal = new Map<string, DayGroup>();
      for (const schedule of schedules) {
        const items = schedule.days[day] ?? [];
        if (items.length === 0) continue;
        byGoal.set(schedule.goal.id, {
          goal: schedule.goal,
          items,
          isActive: schedule.goal.id === activeGoalId,
          isStart:
            schedule.first === day &&
            !schedule.continuation &&
            !schedule.projected,
          isContinuation:
            schedule.first === day && schedule.continuation,
          isProjectedStart:
            schedule.first === day && schedule.projected,
          projected: schedule.projected,
          projectedFrom: schedule.projected
            ? schedule.originalFirst
            : null,
          isEnd: schedule.last === day,
        });
      }
      return Array.from(byGoal.values()).sort((a, b) => {
        if (a.isActive !== b.isActive) return a.isActive ? -1 : 1;
        return a.goal.topic.localeCompare(b.goal.topic, "ru");
      });
    },
    [schedules, activeGoalId],
  );

  const dayDeadlineGoals = useMemo(
    () =>
      new Map(
        schedules
          .filter((schedule) => schedule.deadline)
          .map((schedule) => [
            schedule.deadline,
            schedule.goal,
          ] as [string, LearningGoal]),
      ),
    [schedules],
  );

  function scrollToToday() {
    document
      .getElementById(`global-day-${today}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  if (loading) {
    return (
      <div className="space-y-3" aria-busy="true">
        <div className="h-32 animate-pulse rounded-2xl bg-muted" />
        {Array.from({ length: 2 }, (_, index) => (
          <div
            key={index}
            className="h-64 animate-pulse rounded-2xl border border-border bg-muted/40"
          />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-border bg-card p-6">
        <div className="flex items-center gap-2 text-foreground">
          <CalendarDays className="h-5 w-5 text-brand" />
          <h2 className="text-lg font-semibold">Общий календарь</h2>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">{error}</p>
        <Button size="sm" variant="outline" className="mt-4" onClick={() => void load()}>
          Повторить
        </Button>
      </div>
    );
  }

  if (goals.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center">
        <CalendarDays className="mx-auto h-8 w-8 text-muted-foreground" />
        <h2 className="mt-3 text-lg font-semibold text-foreground">
          Календарь пока пуст
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Создайте цель и постройте план — её дни появятся здесь вместе с
          остальными.
        </p>
        <Button asChild className="mt-4">
          <Link href="/learning">К целям</Link>
        </Button>
      </div>
    );
  }

  const activeGoalBadge = activeGoal ? (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand/30 bg-brand-muted/30 px-4 py-3">
      <div className="flex items-center gap-2">
        <Badge className="bg-brand text-brand-foreground">
          Активная цель
        </Badge>
        <Sparkles className="h-4 w-4 text-brand" />
        <span className="text-sm font-medium text-foreground">
          {activeGoal.topic}
        </span>
        {activeGoal.course_name && (
          <span className="text-xs text-muted-foreground">
            · {activeGoal.course_name}
          </span>
        )}
      </div>
      <Button asChild size="sm">
        <Link href="/learning/tasks">Все занятия на сегодня</Link>
      </Button>
    </div>
  ) : (
    <div className="mt-4 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3 text-sm text-muted-foreground">
      Активная цель ещё не выбрана. Откройте цель и нажмите «Сделать
      активной» — она будет подсвечена в общем занятии «Сегодня».
    </div>
  );

  const modeInfo = (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-info/30 bg-info-muted/30 px-4 py-3">
      <div className="flex items-center gap-2">
        <ModeIcon mode={activeMode} className="h-4 w-4 text-info" />
        <Badge className="bg-info text-info-foreground">
          Режим обучения
        </Badge>
        <span className="text-sm font-medium text-foreground">
          {MODE_LABELS[activeMode]}
        </span>
        {activeMode === "self_paced" && (
          <span className="text-xs text-muted-foreground ml-2">
            · невыполненное переносится на следующий день
          </span>
        )}
        {activeMode === "course_following" && (
          <span className="text-xs text-muted-foreground ml-2">
            · план перестраивается, чтобы успеть к сроку
          </span>
        )}
      </div>
      <ModeSelectorModal
        open={modeModalOpen}
        onOpenChange={setModeModalOpen}
        onModeChange={setMode}
      />
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <CalendarDays className="h-5 w-5 text-brand" />
              <h1 className="text-xl font-bold tracking-tight text-foreground">
                Единый календарь
              </h1>
              <Badge variant="secondary" className="text-[10px]">
                с сегодняшнего дня
              </Badge>
            </div>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Все цели и темы в одном расписании, начиная с сегодняшнего дня.
              Здесь видно, что происходит в каждый день и когда начинается
              следующая цель.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={scrollToToday}>
            Сегодня
          </Button>
        </div>

        {activeGoalBadge}
        {modeInfo}
      </div>

      <section className="rounded-2xl border border-border bg-card p-4 sm:p-5">
        <h2 className="text-sm font-semibold text-foreground">Карта целей</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Незавершённые планы, которые уже закончились, показаны с сегодняшнего
          дня. Исходные даты плана при этом не меняются.
        </p>
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {goals.map((goal) => {
            const schedule = scheduleById.get(goal.id);
            const isActive = goal.id === activeGoalId;
            return (
              <Link
                key={goal.id}
                href={`/learning/goals/${goal.id}?tab=calendar`}
                className={cn(
                  "rounded-xl border p-3 transition-colors hover:border-brand/50",
                  isActive
                    ? "border-brand/45 bg-brand-muted/25"
                    : "border-border bg-background/60",
                )}
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-sm font-semibold text-foreground">
                    {goal.topic}
                  </span>
                  {isActive && (
                    <Badge className="bg-brand text-brand-foreground">
                      сейчас
                    </Badge>
                  )}
                  {schedule?.projected && (
                    <Badge variant="secondary" className="text-[10px]">
                      с сегодня
                    </Badge>
                  )}
                  <Badge variant="secondary" className="text-[10px]">
                    {goal.status === "active"
                      ? "в работе"
                      : goal.status === "paused"
                        ? "на паузе"
                        : goal.status === "completed"
                          ? "завершена"
                          : goal.status}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {schedule
                    ? schedule.hasFuture
                      ? `${shortDate(schedule.first)} — ${shortDate(schedule.last)} · ${schedule.total} дел`
                      : `план завершён ${shortDate(schedule.originalLast)} · выполнено ${schedule.done}/${schedule.total}`
                    : "план ещё не составлен"}
                  {goal.course_name ? ` · ${goal.course_name}` : ""}
                </p>
                {schedule?.hasFuture && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                    <Clock3 className="h-3 w-3" />
                    {schedule.projected
                      ? `план от ${shortDate(schedule.originalFirst)} перенесён на сегодня`
                      : schedule.continuation
                        ? `продолжение с ${shortDate(schedule.first)}`
                        : `старт ${shortDate(schedule.first)}`}{" "}
                    · финиш {shortDate(schedule.last)} · выполнено{" "}
                    {schedule.done}/{schedule.total}
                  </div>
                )}
              </Link>
            );
          })}
        </div>
      </section>

      {schedules.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-card p-6 text-sm text-muted-foreground">
          {activeMode === "self_paced"
            ? "Невыполненных заданий нет. Все задачи распределены по дням."
            : "С сегодняшнего дня в планах целей нет занятий. Постройте план или пересоберите план цели — после этого общий календарь заполнится."}
        </div>
      ) : (
        <>
          {Array.from(schedulesByMode.entries()).map(([mode, modeSchedules]) => (
            <section key={mode} className="mb-6">
              <div className="mb-3 flex items-center gap-2">
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider ${MODE_COLORS[mode]}`}
                >
                  {MODE_LABELS[mode]}
                </span>
                {activeMode === null && (
                  <span className="text-xs text-muted-foreground">
                    ({modeSchedules.length} цель{
                      modeSchedules.length === 1 ? "" : "ей"
                    })
                  </span>
                )}
              </div>
              {weeks.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border bg-card p-4 text-sm text-muted-foreground">
                  В этом режиме занятий нет.
                </div>
              ) : (
                weeks.map((week) => (
                  <section
                    key={week[0]}
                    className="rounded-2xl border border-border bg-card p-3 sm:p-4"
                  >
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <h2 className="text-sm font-semibold text-foreground">
                        {weekRangeLabel(week)}
                      </h2>
                      <span className="text-xs text-muted-foreground">
                        {week.includes(today) ? "текущая неделя" : "по плану"}
                      </span>
                    </div>

                    <div className="space-y-1.5">
                      {week.map((day) => {
                        // groupsFor уже возвращает только группы из расписаний
                        // выбранного режима, дополнительная фильтрация не нужна.
                        const groups = groupsFor(day).filter(
                          (g) => g.goal.id !== undefined,
                        );
                        const isToday = day === today;
                        if (groups.length === 0) return null;
                        return (
                          <div
                            key={day}
                            id={`global-day-${day}-${mode}`}
                            className={cn(
                              "grid gap-2 rounded-xl border px-2.5 py-2 md:grid-cols-[140px_1fr]",
                              isToday
                                ? "border-brand/50 bg-brand-muted/20"
                                : "border-border/70 bg-background/40",
                            )}
                          >
                            <div className="flex items-start justify-between gap-2 md:block">
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
                                <p className="text-sm font-semibold text-foreground">
                                  {toUtcDate(day).getUTCDate()}
                                </p>
                                <p className="text-[10px] text-muted-foreground">
                                  {toUtcDate(day).toLocaleDateString("ru-RU", {
                                    month: "short",
                                    timeZone: "UTC",
                                  })}
                                </p>
                              </div>
                              {isToday && (
                                <Badge className="bg-brand text-brand-foreground">
                                  сегодня
                                </Badge>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              {groups.length === 0 ? (
                                <p className="py-2 text-xs text-muted-foreground">
                                  На этот день занятий нет.
                                </p>
                              ) : (
                                groups.map((group) => (
                                  <div
                                    key={group.goal.id}
                                    className={cn(
                                      "rounded-lg border px-2.5 py-2",
                                      group.isActive
                                        ? "border-brand/40 bg-brand-muted/20"
                                        : "border-border bg-card",
                                    )}
                                  >
                                    <div className="flex flex-wrap items-center gap-1.5">
                                      <Link
                                        href={`/learning/goals/${group.goal.id}?tab=calendar`}
                                        className="text-xs font-semibold text-foreground hover:text-brand"
                                      >
                                        {group.goal.topic}
                                      </Link>
                                      {group.isActive && (
                                        <Badge
                                          className="bg-brand text-brand-foreground text-[9px]"
                                        >
                                          активная · сейчас
                                        </Badge>
                                      )}
                                      {group.isStart && (
                                        <Badge
                                          className="bg-info-muted text-info text-[9px]"
                                        >
                                          старт цели
                                        </Badge>
                                      )}
                                      {group.isContinuation && (
                                        <Badge
                                          variant="secondary"
                                          className="text-[9px]"
                                        >
                                          продолжение цели
                                        </Badge>
                                      )}
                                      {group.isProjectedStart &&
                                      group.projectedFrom && (
                                        <Badge
                                          variant="outline"
                                          className="text-[9px]"
                                        >
                                          перенесён с{" "}
                                          {shortDate(group.projectedFrom)}
                                        </Badge>
                                      )}
                                      {group.isEnd && (
                                        <Badge
                                          className="bg-success-muted text-success text-[9px]"
                                        >
                                          финиш цели
                                        </Badge>
                                      )}
                                    </div>
                                    <div className="mt-1 grid gap-1 sm:grid-cols-2">
                                      {group.items.map((item) => {
                                        const Icon =
                                          item.item_type === "exam"
                                            ? GraduationCap
                                            : item.item_type === "practice"
                                              ? ListChecks
                                              : isShortReview(item)
                                                ? RotateCcw
                                                : null;
                                        return (
                                          <Link
                                            key={item.id}
                                            href={itemHref(
                                              group.goal.id,
                                              item,
                                            )}
                                            className="flex items-start gap-1.5 rounded-md px-1 py-0.5 hover:bg-muted/50"
                                          >
                                            {Icon ? (
                                              <Icon
                                                className={cn(
                                                  "mt-0.5 h-3.5 w-3.5 shrink-0",
                                                  item.status === "done"
                                                    ? "text-success"
                                                    : item.item_type === "exam"
                                                      ? "text-brand"
                                                      : "text-info",
                                                )}
                                              />
                                            ) : (
                                              <span
                                                className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-brand"
                                              />
                                            )}
                                            <span className="min-w-0">
                                              <span
                                                className={cn(
                                                  "block text-[11px] font-medium leading-snug",
                                                  statusTone(item),
                                                )}
                                              >
                                                {item.title}
                                              </span>
                                              <span className="block text-[10px] text-muted-foreground">
                                                {typeLabel(item)} ·
                                                {item.planned_minutes} мин ·
                                                {group.projected &&
                                                item.status === "missed"
                                                  ? "перенесено"
                                                  : (STATUS_LABEL[item
                                                      .status] ??
                                                    item.status)}
                                              </span>
                                            </span>
                                          </Link>
                                        );
                                      })}
                                    </div>
                                  </div>
                                ))
                              )}
                              {dayDeadlineGoals.get(day) && (
                                <div
                                  className="mt-1.5 flex flex-wrap items-center gap-1"
                                >
                                  <span className="text-[10px] font-semibold text-destructive">
                                    ⚠️
                                  </span>
                                  <span className="text-xs text-destructive/80">
                                    дедлайн:{" "}
                                    {dayDeadlineGoals.get(day)!.topic}
                                  </span>
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </section>
                ))
              )}
            </section>
          ))}
        </>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-card px-4 py-3 text-xs text-muted-foreground">
        <span>
          Календарь собирается из планов целей. Изменения в плане появляются
          здесь сразу.
        </span>
        <Button asChild size="xs" variant="ghost">
          <Link href="/learning/tasks">
            Открыть «Мои задачи» <ArrowRight className="ml-1 h-3 w-3" />
          </Link>
        </Button>
      </div>
    </div>
  );
}

/**
 * ModeSelectorModal — chooses the active learning mode for the workspace.
 * Opened from the global calendar ("Мои задачи" → Календарь).
 * Два режима: по программе курса (успеть к дедлайну) и в своём темпе
 * (переносить невыполненное на следующий день).
 */
export function ModeSelectorModal({
  open,
  onOpenChange,
  onModeChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onModeChange?: (mode: LearningMode) => void;
}) {
  const t = useT();
  const currentMode = useWorkspaceStore((s) => s.spaceLayout.mode);
  const setMode = useWorkspaceStore((s) => s.setSpaceMode);

  const MODES: LearningMode[] = ["course_following", "self_paced"];

  const MODE_LABELS: Record<LearningMode, string> = {
    course_following: t("mode.course_following"),
    self_paced: t("mode.self_paced"),
  };

  const MODE_DESCRIPTIONS: Record<LearningMode, string> = {
    course_following:
      "План перестраивается, чтобы успеть выполнить все задания в срок. Дедлайн считается жестким.",
    self_paced:
      "Невыполненные задания просто переносятся на следующий день. Курс может выходить за изначальные сроки.",
  };

  const MODE_ICONS: Record<LearningMode, React.ComponentType<{ className?: string }>> =
    {
      course_following: GraduationCap,
      self_paced: Compass,
    };

  function pick(mode: LearningMode) {
    setMode(mode);
    void onModeChange?.(mode);
    void onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md p-0">
        <DialogTitle className="flex items-center gap-2">
          <Compass className="size-5 text-brand" />
          {t("mode.title")}
        </DialogTitle>
        <DialogDescription className="mb-4">
          {t("mode.description")}
        </DialogDescription>
        <div className="space-y-1.5 p-3">
          {MODES.map((mode) => {
            const Icon = MODE_ICONS[mode];
            const isActive = currentMode === mode;
            return (
              <Button
                key={mode}
                onClick={() => pick(mode)}
                variant={isActive ? "default" : "outline"}
                className="w-full flex items-center gap-3 text-left"
              >
                <Icon className="size-4 shrink-0" aria-hidden="true" />
                <div className="flex-1 min-w-0">
                  <span className="font-medium block">{MODE_LABELS[mode]}</span>
                  <span className="text-[10px] text-muted-foreground block mt-0.5">
                    {MODE_DESCRIPTIONS[mode]}
                  </span>
                </div>
                <span className="ml-auto text-xs text-muted-foreground">
                  {isActive ? "активен" : ""}
                </span>
              </Button>
            );
          })}
          <p className="mt-3 text-[11px] text-muted-foreground">
            Выберите, как хотите обучаться. От этого зависит, как перестраивается
            план занятий в календаре.
          </p>
        </div>
      </DialogContent>
      <DialogClose />
    </Dialog>
  );
}
