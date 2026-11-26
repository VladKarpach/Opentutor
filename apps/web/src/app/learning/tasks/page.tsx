"use client";

/**
 * Unified "Мои задачи" screen — one place for everything that needs attention.
 *
 * Four tabs replace the old split pages:
 *  • Входящие   — due reviews, wrong answers, videos (from /learning/review/due)
 *  • Сегодня    — all goals with work scheduled today (from /learning/daily/prepare)
 *  • Календарь  — global calendar with deadlines (GlobalCalendar component)
 *  • Отстающие  — overdue goals with cascade replan (from /learning/goals/overdue)
 *
 * Keyboard shortcuts: 1-4 switch tabs.
 */

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Clock3,
  FastForward,
  Loader2,
  RefreshCw,
  Search,
  Target,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import Link from "next/link";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  catchUpPlan,
  getDueReview,
  getLearningIndex,
  getPlan,
  pullPlanItemAhead,
  type DueReviewItem,
  type LearningGoal,
  type PlanItem,
} from "@/lib/api/learning";
import { useT } from "@/lib/i18n-context";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/store/workspace";

import { GlobalCalendar, ModeSelectorModal } from "@/app/learning/_components/global-calendar";
import { TaskCard } from "@/components/shared/task-card";

const TABS: { id: string; label: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { id: "inbox", label: "tasks.tab.inbox", Icon: AlertTriangle },
  { id: "today", label: "tasks.tab.today", Icon: Clock3 },
  { id: "ahead", label: "tasks.tab.ahead", Icon: FastForward },
  { id: "calendar", label: "tasks.tab.calendar", Icon: CalendarDays },
  { id: "overdue", label: "tasks.tab.overdue", Icon: Target },
];

const FILTER_CHIPS: { id: string; label: string; match: (item: DueReviewItem) => boolean }[] = [
  { id: "all", label: "tasks.filter.all", match: () => true },
  { id: "review", label: "tasks.filter.review", match: (i) => i.type === "review" },
  { id: "plan", label: "tasks.filter.plan", match: (i) => i.type === "plan_item" },
  { id: "wrong", label: "tasks.filter.wrong", match: (i) => i.type === "wrong_answer" },
  { id: "video", label: "tasks.filter.video", match: (i) => i.type === "video" },
];

export default function TasksPage() {
  const t = useT();
  const [tab, setTab] = useState<typeof TABS[number]["id"]>("inbox");

  // Inbox data
  const [inbox, setInbox] = useState<DueReviewItem[]>([]);
  const [inboxLoading, setInboxLoading] = useState(true);
  const [inboxError, setInboxError] = useState<string | null>(null);

  // Today data
  const [today, setToday] = useState<DueReviewItem[]>([]);
  const [todayLoading, setTodayLoading] = useState(true);
  const [todayError, setTodayError] = useState<string | null>(null);

  // Overdue data
  const [overdue, setOverdue] = useState<DueReviewItem[]>([]);
  const [overdueLoading, setOverdueLoading] = useState(false);
  const [overdueError, setOverdueError] = useState<string | null>(null);

  // Search & filter state (only applies to inbox tab)
  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState("all");

  const loadInbox = useCallback(async () => {
    setInboxLoading(true);
    setInboxError(null);
    try {
      const data = await getDueReview();
      setInbox(data.items);
    } catch (err) {
      setInboxError(
        err instanceof Error ? err.message : "Не удалось загрузить входящие.",
      );
    } finally {
      setInboxLoading(false);
    }
  }, []);

  const loadToday = useCallback(async () => {
    setTodayLoading(true);
    setTodayError(null);
    try {
      const data = await getDueReview();
      const todayIso = data.date;
      const planItems = data.items.filter(
        (i) => i.type === "plan_item" && i.day === todayIso,
      );
      setToday(planItems);
    } catch (err) {
      setTodayError(
        err instanceof Error ? err.message : "Не удалось загрузить задачи на сегодня.",
      );
    } finally {
      setTodayLoading(false);
    }
  }, []);

  const loadOverdue = useCallback(async () => {
    setOverdueLoading(true);
    setOverdueError(null);
    try {
      const data = await getDueReview();
      const todayIso = data.date;
      const overdueItems = data.items.filter(
        (i) =>
          i.type === "plan_item" &&
          i.day &&
          i.day < todayIso &&
          i.status !== "done",
      );
      setOverdue(overdueItems);
    } catch (err) {
      setOverdueError(
        err instanceof Error ? err.message : "Не удалось загрузить отстающие.",
      );
    } finally {
      setOverdueLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadInbox();
    void loadToday();
    void loadOverdue();
  }, [loadInbox, loadToday, loadOverdue]);

  // Filtered & searched inbox items
  const filteredInbox = useMemo(() => {
    let items = inbox;
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      items = items.filter(
        (i) =>
          (i.title ?? "").toLowerCase().includes(q) ||
          (i.goal_title ?? "").toLowerCase().includes(q) ||
          (i.topic_title ?? "").toLowerCase().includes(q),
      );
    }
    if (activeFilter !== "all") {
      items = items.filter((i) =>
        FILTER_CHIPS.find((f) => f.id === activeFilter)!.match(i),
      );
    }
    return items;
  }, [inbox, searchQuery, activeFilter]);

  // Keyboard shortcuts: 1-5 switch tabs
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const key = e.key;
      if (key >= "1" && key <= "5") {
        e.preventDefault();
        const next = TABS[Number(key) - 1];
        if (next) setTab(next.id);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 md:px-8">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-2">
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-foreground">
                {t("nav.tasks")}
              </h1>
              <p className="text-sm text-muted-foreground">
                Всё, что требует вашего внимания, в одном месте.
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setTab("inbox");
                setSearchQuery("");
                setActiveFilter("all");
                void loadInbox();
                void loadToday();
                void loadOverdue();
              }}
            >
              <RefreshCw className="mr-2 size-3.5" /> Обновить
            </Button>
          </div>

          {/* Progress bar */}
          <div className="mt-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-foreground">
                Прогресс недели
              </span>
              <span className="text-xs text-muted-foreground">
                {inbox.length + today.length} задач
              </span>
            </div>
            <div className="w-full bg-muted rounded-full h-2">
              <div
                className="h-2 bg-brand rounded-full transition-all duration-300"
                style={{ width: "65%" }}
              />
            </div>
          </div>
        </div>

        {/* Search & filters bar (shown on inbox & today tabs) */}
        {tab === "inbox" || tab === "today" ? (
          <div className="mt-6 mb-4">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="relative w-full max-w-md">
                <Search className="absolute left-3 top-1/2 -mt-2 size-4 text-muted-foreground" />
                <Input
                  type="text"
                  placeholder={t("tasks.search.placeholder")}
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9"
                  disabled={tab === "today" && today.length === 0 && !todayLoading}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                {FILTER_CHIPS.map((chip) => (
                  <Button
                    key={chip.id}
                    size="xs"
                    variant={
                      activeFilter === chip.id ? "default" : "outline"
                    }
                    className={cn(
                      activeFilter === chip.id
                        ? "bg-brand text-brand-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                    onClick={() => setActiveFilter(chip.id)}
                    disabled={tab === "today" && chip.id !== "all" && chip.id !== "plan"}
                  >
                    {t(chip.label)}
                  </Button>
                ))}
              </div>
            </div>
          </div>
        ) : null}

        {/* Tabs */}
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="grid w-full grid-cols-5 md:grid-cols-5">
            {TABS.map((tabDef) => (
              <TabsTrigger
                key={tabDef.id}
                value={tabDef.id}
                className={cn(
                  "flex flex-col items-center justify-center gap-1.5 px-2 py-3 text-sm transition-colors",
                  tab === tabDef.id
                    ? "bg-brand/10 text-brand border-b-2 border-brand"
                    : "text-muted-foreground hover:bg-muted/50",
                )}
              >
                {(() => {
                  const Icon = tabDef.Icon;
                  return <Icon className="size-4 mx-auto" />;
                })()}
                <span className="font-medium">{t(tabDef.label)}</span>
                <span className="text-[10px] text-muted-foreground">
                  {TABS.findIndex((t) => t.id === tabDef.id) + 1}
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        <div className="mt-6">
          {tab === "inbox" && (
            <InboxTab
              items={filteredInbox}
              loading={inboxLoading}
              error={inboxError}
              onRetry={loadInbox}
              searchQuery={searchQuery}
              activeFilter={activeFilter}
            />
          )}
          {tab === "today" && (
            <TodayTab
              items={today}
              loading={todayLoading}
              error={todayError}
              onRetry={loadToday}
            />
          )}
          {tab === "ahead" && <AheadTab />}
          {tab === "calendar" && <CalendarTab />}
          {tab === "overdue" && (
            <OverdueTab items={overdue} loading={overdueLoading} error={overdueError} onRetry={loadOverdue} />
          )}
        </div>

        
      </div>
    </div>
  );
}

function InboxTab({
  items,
  loading,
  error,
  onRetry,
  searchQuery,
  activeFilter,
}: {
  items: DueReviewItem[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  searchQuery: string;
  activeFilter: string;
}) {
  const t = useT();

  if (loading) {
    return (
      <div className="h-64 flex items-center justify-center">
        <Loader2 className="size-6 animate-spin text-brand" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-center">
        <AlertTriangle className="mx-auto size-12 text-destructive mb-4" />
        <p className="text-sm text-muted-foreground">{error}</p>
        <Button onClick={onRetry} className="mt-4">
          <RefreshCw className="mr-2 size-4" /> Попробовать снова
        </Button>
      </div>
    );
  }

  if (items.length === 0) {
    const hasSearchOrFilter = searchQuery.trim().length > 0 || activeFilter !== "all";
    return (
      <div className="rounded-xl border border-border bg-card p-8 text-center">
        {hasSearchOrFilter ? (
          <>
            <Search className="mx-auto size-12 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold text-foreground">
              Поиск не дал результатов
            </h3>
            <p className="text-sm text-muted-foreground mt-2">
              Попробуйте изменить запрос или сбросить фильтр.
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  void onRetry();
                }}
              >
                Сбросить фильтр
              </Button>
            </div>
          </>
        ) : (
          <>
            <CheckCircle2 className="mx-auto size-12 text-success mb-4" />
            <h3 className="text-lg font-semibold text-foreground">
              {t("tasks.empty.inbox.title")}
            </h3>
            <p className="text-sm text-muted-foreground mt-2">
              {t("tasks.empty.inbox.subtitle")}
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              <Button asChild size="sm">
                <Link href="/learning/calendar">Открыть календарь</Link>
              </Button>
              <Button asChild size="sm" variant="outline">
                <Link href="/learning">К целям</Link>
              </Button>
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <TaskCard key={`${item.type}-${item.goal_id}-${item.topic_id ?? item.day ?? item.title}`} item={item} />
      ))}
    </div>
  );
}

function TodayTab({
  items,
  loading,
  error,
  onRetry,
}: {
  items: DueReviewItem[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const t = useT();

  if (loading) {
    return (
      <div className="h-64 flex items-center justify-center">
        <Loader2 className="size-6 animate-spin text-brand" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-center">
        <AlertTriangle className="mx-auto size-12 text-destructive mb-4" />
        <p className="text-sm text-muted-foreground">{error}</p>
        <Button onClick={onRetry} className="mt-4">
          <RefreshCw className="mr-2 size-4" /> Попробовать снова
        </Button>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-8 text-center">
        <Target className="mx-auto size-12 text-muted-foreground mb-4" />
        <h3 className="text-lg font-semibold text-foreground">
          {t("tasks.empty.today.title")}
        </h3>
        <p className="text-sm text-muted-foreground mt-2">
          {t("tasks.empty.today.subtitle")}
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <Button asChild size="sm">
            <Link href="/learning/calendar">Открыть календарь</Link>
          </Button>
          <Button asChild size="sm" variant="outline">
            <Link href="/learning">К целям</Link>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <TaskCard key={`${item.type}-${item.goal_id}-${item.day ?? item.title}`} item={item} />
      ))}
    </div>
  );
}

const PLAN_TYPE_LABEL: Record<string, string> = {
  study: "Теория + практика",
  review: "Повторение",
  practice: "Контрольное задание",
  exam: "Экзамен",
};

interface AheadRow {
  goalId: string;
  goalTitle: string;
  item: PlanItem;
}

interface AheadDay {
  day: string;
  rows: AheadRow[];
}

function isActionablePlanItem(item: PlanItem): boolean {
  return (
    !item.rejected && (item.status === "pending" || item.status === "missed")
  );
}

/** A copy created on today by an earlier pull. It is today's work, not work
 *  that still blocks taking another task from the same future day. */
function isPulledCopy(item: PlanItem): boolean {
  return (item.reason ?? "").includes("взято наперёд с ");
}

/** The original item after it was taken ahead: it keeps half the time and is
 *  no longer offered for a second pull. */
function isPulledOriginal(item: PlanItem): boolean {
  return (item.reason ?? "").includes("взято наперёд на ");
}

function isShortReview(item: PlanItem): boolean {
  return (item.reason ?? "").startsWith("досрочное повторение");
}

function allGoals(index: Awaited<ReturnType<typeof getLearningIndex>>): LearningGoal[] {
  const byId = new Map<string, LearningGoal>();
  for (const course of index.courses) {
    for (const goal of course.goals) byId.set(goal.id, goal);
  }
  for (const goal of index.unattached) byId.set(goal.id, goal);
  return Array.from(byId.values());
}

function aheadDayLabel(iso: string): string {
  return new Date(`${iso}T00:00:00.000Z`).toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
}

/**
 * "Взять наперёд" — one task from any future day, on today.
 *
 * The original stays on its day with half the time (minimum 5 minutes); the
 * copy here keeps the full time. Control assignments are included. Today's own
 * plan must be finished first, which is what turns "I'm ahead" into a deliberate
 * state rather than a way to bury a backlog.
 */
function AheadTab() {
  const [days, setDays] = useState<AheadDay[]>([]);
  const [blockedGoals, setBlockedGoals] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pullingId, setPullingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const index = await getLearningIndex();
      const goals = allGoals(index);
      const plans = await Promise.allSettled(
        goals.map((goal) => getPlan(goal.id)),
      );
      const today = new Date().toISOString().slice(0, 10);
      const grouped = new Map<string, AheadRow[]>();
      const blocked = new Set<string>();

      plans.forEach((result, position) => {
        if (result.status !== "fulfilled") return;
        const goal = goals[position];
        const plan = result.value;

        for (const [day, items] of Object.entries(plan.days)) {
          if (
            day <= today &&
            items.some(
              (item) => isActionablePlanItem(item) && !isPulledCopy(item),
            )
          ) {
            blocked.add(goal.id);
          }
        }

        for (const [day, items] of Object.entries(plan.days)) {
          if (day <= today) continue;
          for (const item of items) {
            if (
              !isActionablePlanItem(item) ||
              item.item_type === "exam" ||
              isPulledOriginal(item) ||
              isShortReview(item)
            ) {
              continue;
            }
            const list = grouped.get(day) ?? [];
            list.push({ goalId: goal.id, goalTitle: goal.topic, item });
            grouped.set(day, list);
          }
        }
      });

      setBlockedGoals(blocked);
      setDays(
        [...grouped.entries()]
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([day, rows]) => ({ day, rows })),
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Не удалось загрузить будущие задания.",
      );
      setDays([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handlePull(row: AheadRow) {
    setPullingId(row.item.id);
    try {
      const result = await pullPlanItemAhead(row.goalId, row.item.id);
      toast.success("Задание взято на сегодня", {
        description: `На ${aheadDayLabel(result.original.day)} осталось ${result.original.planned_minutes} мин.`,
      });
      await load();
    } catch (err) {
      toast.error("Не удалось взять задание наперёд", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setPullingId(null);
    }
  }

  if (loading) {
    return (
      <div className="h-64 flex items-center justify-center">
        <Loader2 className="size-6 animate-spin text-brand" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-center">
        <AlertTriangle className="mx-auto size-12 text-destructive mb-4" />
        <p className="text-sm text-muted-foreground">{error}</p>
        <Button onClick={load} className="mt-4">
          <RefreshCw className="mr-2 size-4" /> Попробовать снова
        </Button>
      </div>
    );
  }

  if (days.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-8 text-center">
        <FastForward className="mx-auto size-12 text-muted-foreground mb-4" />
        <h3 className="text-lg font-semibold text-foreground">
          Впереди нет заданий
        </h3>
        <p className="text-sm text-muted-foreground mt-2">
          Все будущие задания уже взяты или в плане пока нет новых дней.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {days.map(({ day, rows }) => (
        <section key={day} className="space-y-2">
          <h3 className="px-1 text-sm font-semibold text-foreground">
            {aheadDayLabel(day)}
          </h3>
          {rows.map((row) => {
            const blocked = blockedGoals.has(row.goalId);
            return (
              <div
                key={row.item.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="text-xs">
                      {PLAN_TYPE_LABEL[row.item.item_type] ??
                        row.item.item_type}
                    </Badge>
                    <span className="text-sm font-medium text-foreground">
                      {row.item.title}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {row.item.planned_minutes} мин · {row.goalTitle}
                    {blocked
                      ? " · сначала завершите сегодняшние задания по этой цели"
                      : ""}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={blocked || pullingId === row.item.id}
                  onClick={() => void handlePull(row)}
                >
                  {pullingId === row.item.id ? "Берём…" : "Взять на сегодня"}
                </Button>
              </div>
            );
          })}
        </section>
      ))}
      <div className="rounded-xl border border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
        Задание можно взять с любого будущего дня. Исходное задание останется на
        своём дне с половинным временем (минимум 5 мин), а сегодня появится его
        копия с полным временем. Контрольные задания тоже можно брать; экзамен —
        нельзя.
      </div>
    </div>
  );
}

function CalendarTab() {
  const [modeModalOpen, setModeModalOpen] = useState(false);
  const currentMode = useWorkspaceStore((s) => s.spaceLayout.mode);
  const t = useT();

  return (
    <div className="h-[600px] flex flex-col">
      <GlobalCalendar />
      <div className="flex items-center justify-between mb-3 px-2">
        <div className="flex items-center gap-2">
          <Badge variant="secondary" className="text-[10px] uppercase">
            Режим обучения
          </Badge>
          <span className="text-sm font-medium text-foreground">
            {currentMode ? t(`mode.${currentMode}`) : "Не выбран"}
          </span>
        </div>
        <Button size="sm" variant="outline" onClick={() => setModeModalOpen(true)}>
          Сменить режим
        </Button>
      </div>
      <ModeSelectorModal open={modeModalOpen} onOpenChange={setModeModalOpen} />
    </div>
  );
}

function OverdueTab({
  items,
  loading,
  error,
  onRetry,
}: {
  items: DueReviewItem[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const t = useT();
  const [catchingUp, setCatchingUp] = useState(false);
  const currentMode = useWorkspaceStore((s) => s.spaceLayout.mode);

  const uniqueGoalIds = useMemo(
    () => [...new Set(items.map((i) => i.goal_id))],
    [items],
  );

  const handleCatchup = useCallback(async () => {
    setCatchingUp(true);
    try {
      for (const goalId of uniqueGoalIds) {
        await catchUpPlan(goalId, { mode: currentMode });
      }
      toast.success("План пересобран после пропусков");
      void onRetry();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : "Не удалось догнать план",
      );
    } finally {
      setCatchingUp(false);
    }
  }, [uniqueGoalIds, onRetry, t, currentMode]);

  if (loading) {
    return (
      <div className="h-64 flex items-center justify-center">
        <Loader2 className="size-6 animate-spin text-brand" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-center">
        <AlertTriangle className="mx-auto size-12 text-destructive mb-4" />
        <p className="text-sm text-muted-foreground">{error}</p>
        <Button onClick={onRetry} className="mt-4">
          <RefreshCw className="mr-2 size-4" /> Попробовать снова
        </Button>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-8 text-center">
        <Target className="mx-auto size-12 text-muted-foreground mb-4" />
        <h3 className="text-lg font-semibold text-foreground">
          Отстающих задач нет
        </h3>
        <p className="text-sm text-muted-foreground mt-2">
          Все блоки в рамках сроков — всё выполнено вовремя.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <Button asChild size="sm">
            <Link href="/learning/calendar">Открыть календарь</Link>
          </Button>
          <Button asChild size="sm" variant="outline">
            <Link href="/learning">К целям</Link>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider px-2">
          Блоки, которые нужно закрыть, чтобы вернуться к плану
        </p>
        <Button
          size="sm"
          variant="default"
          disabled={catchingUp}
          onClick={handleCatchup}
        >
          {catchingUp ? "Догоняем…" : "Догнать план"}
        </Button>
      </div>
      {items.map((item) => (
        <TaskCard key={`${item.type}-${item.goal_id}-${item.day ?? item.title}`} item={item} />
      ))}
    </div>
  );
}