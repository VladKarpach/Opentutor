"use client";

/**
 * Goal setup — TZ screens 1 and FR-2.
 *
 * Collects the four inputs the TZ defines for a goal (FR-2.1): a free-text
 * topic, a deadline (date or number of days), available minutes per day, and
 * the desired depth. Then it asks the backend whether the topic is too broad
 * to plan around (FR-2.2) before letting the user continue.
 *
 * A goal must live inside a course, so the course is chosen here too. It comes
 * from ?course= when the entry point already knows it (the course page, the
 * grouped goal list); otherwise the learner picks.
 */

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { listCourseOverview, type CourseOverviewCard } from "@/lib/api/courses";
import {
  clarifyGoal,
  createGoal,
  generateTopics,
  type ClarifyResult,
  type CreateGoalPayload,
  type GoalDepth,
  type LearningGoal,
} from "@/lib/api/learning";

const DEPTHS: { value: GoalDepth; label: string; hint: string }[] = [
  { value: "overview", label: "Обзор", hint: "Понять основные идеи" },
  { value: "confident", label: "Уверенное владение", hint: "Уметь применять самому" },
  { value: "deep", label: "Углублённо", hint: "Разобраться детально" },
];

const MINUTES = [30, 45, 60, 90, 120];

function GoalSetupInner() {
  const router = useRouter();
  const params = useSearchParams();

  const [topic, setTopic] = useState("");
  const [days, setDays] = useState("30");
  const [minutesPerDay, setMinutesPerDay] = useState(60);
  const [depth, setDepth] = useState<GoalDepth>("confident");

  const [courses, setCourses] = useState<{ id: string; name: string }[]>([]);
  const [courseId, setCourseId] = useState(params.get("course") ?? "");
  const [coursesLoading, setCoursesLoading] = useState(true);

  const [clarify, setClarify] = useState<ClarifyResult | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listCourseOverview()
      .then((rows: CourseOverviewCard[]) => {
        if (cancelled) return;
        const usable = rows.map((c: CourseOverviewCard) => ({ id: c.id, name: c.name }));
        setCourses(usable);
        setCourseId((current) => {
          if (current && usable.some((c) => c.id === current)) return current;
          // Exactly one course: no need to ask.
          return usable.length === 1 ? usable[0].id : current;
        });
      })
      .catch(() => {
        if (!cancelled) setCourses([]);
      })
      .finally(() => {
        if (!cancelled) setCoursesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const topicTooShort = topic.trim().length > 0 && topic.trim().length < 12;

  async function handleCreate() {
    const trimmed = topic.trim();
    if (!trimmed) {
      toast.error("Введите тему");
      return;
    }
    if (!courseId) {
      toast.error("Выберите курс — цель создаётся внутри него");
      return;
    }

    setCreating(true);
    try {
      const payload: CreateGoalPayload = {
        topic: trimmed,
        minutes_per_day: minutesPerDay,
        depth,
        course_id: courseId,
      };
      const parsedDays = Number.parseInt(days, 10);
      if (Number.isFinite(parsedDays) && parsedDays > 0) {
        payload.days = parsedDays;
      }

      const goal: LearningGoal = await createGoal(payload);

      // FR-2.2: a too-broad topic gets a clarifying question instead of a plan.
      const check = await clarifyGoal(goal.id);
      if (check.clarification_needed && check.prompt) {
        setClarify({ ...check, goal_id: goal.id });
        toast.info("Уточним цель перед построением плана");
        return;
      }

      // TZ 6.1 step 1: split the topic into subtopics straight away. Without
      // this the goal sits empty and both the diagnostic and the planner refuse
      // to start, which is the most likely reason a fresh goal looks broken.
      try {
        const topics = await generateTopics(goal.id, { depth });
        toast.success(`Цель создана, подтем: ${topics.length}`);
      } catch {
        // Not fatal: the Topics tab offers the same action, so just say so.
        toast.success("Цель создана. Разбейте её на подтем на вкладке «Темы».");
      }

      router.push(`/learning/goals/${goal.id}`);
    } catch (error) {
      toast.error("Не удалось создать цель", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setCreating(false);
    }
  }

  if (clarify) {
    return (
      <section className="mx-auto w-full max-w-2xl">
        <div className="card-shadow rounded-2xl border border-border bg-card p-6">
          <Badge variant="secondary" className="mb-3">
            Нужно уточнение
          </Badge>
          <h2 className="text-xl font-semibold tracking-tight text-foreground">
            {clarify.prompt}
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Уточнённая цель попадёт в планировщик вместо текущей. Можно и сразу
            перейти к плану, он будет построен как есть.
          </p>
          <div className="mt-6 flex flex-wrap gap-2">
            <Button
              onClick={() => router.push(`/learning/goals/${clarify.goal_id}`)}
            >
              Перейти к цели
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                setClarify(null);
                setTopic("");
              }}
            >
              Переформулировать
            </Button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="mx-auto w-full max-w-2xl">
      <header className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          Что будем изучать?
        </h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Тема, срок и время — этого хватит, чтобы построить план.
        </p>
      </header>

      <div className="card-shadow space-y-6 rounded-2xl border border-border bg-card p-6">
        {/* A goal lives inside a course; the course comes first. */}
        <div className="space-y-2">
          <label htmlFor="course" className="text-sm font-medium text-foreground">
            Курс
          </label>
          {coursesLoading ? (
            <div className="h-9 animate-pulse rounded-md bg-muted" />
          ) : courses.length === 0 ? (
            <p className="rounded-lg bg-warning-muted px-3 py-2 text-sm text-foreground">
              У вас пока нет ни одного курса. Создайте курс — цель создаётся внутри
              него.
            </p>
          ) : (
            <select
              id="course"
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
              className="h-9 w-full rounded-md border border-border bg-background px-2 text-sm text-foreground"
            >
              <option value="">Выберите курс…</option>
              {courses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="space-y-2">
          <label htmlFor="topic" className="text-sm font-medium text-foreground">
            Тема
          </label>
          <Input
            id="topic"
            value={topic}
            onChange={(e) => {
              setTopic(e.target.value);
              setClarify(null);
            }}
            placeholder="Например: производные и интегралы для экономического анализа"
            className="h-11"
          />
          {topicTooShort && (
            <p className="text-xs text-muted-foreground">
              Тема из одного-двух слов обычно слишком широкая — уточните её.
            </p>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <label htmlFor="days" className="text-sm font-medium text-foreground">
              Сколько дней
            </label>
            <Input
              id="days"
              type="number"
              min={1}
              max={3650}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              className="h-11"
            />
          </div>

          <div className="space-y-2">
            <span className="text-sm font-medium text-foreground">
              Время в день
            </span>
            <div className="flex flex-wrap gap-2">
              {MINUTES.map((value) => (
                <Button
                  key={value}
                  size="sm"
                  variant={minutesPerDay === value ? "default" : "outline"}
                  onClick={() => setMinutesPerDay(value)}
                  aria-pressed={minutesPerDay === value}
                >
                  {value} мин
                </Button>
              ))}
            </div>
          </div>
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-foreground">
            Глубина
          </legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {DEPTHS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setDepth(option.value)}
                aria-pressed={depth === option.value}
                className={
                  depth === option.value
                    ? "rounded-xl border border-brand bg-brand-muted px-4 py-3 text-left transition-colors"
                    : "rounded-xl border border-border bg-background px-4 py-3 text-left transition-colors hover:bg-accent"
                }
              >
                <span className="block text-sm font-medium text-foreground">
                  {option.label}
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {option.hint}
                </span>
              </button>
            ))}
          </div>
        </fieldset>

        <Button
          onClick={handleCreate}
          disabled={creating || !topic.trim() || !courseId || courses.length === 0}
          className="h-11 w-full"
        >
          {creating ? "Создаём…" : "Создать цель"}
        </Button>
      </div>
    </section>
  );
}

/** Wrapped so useSearchParams stays inside a Suspense boundary. */
export function GoalSetup() {
  return (
    <Suspense fallback={<div className="h-64 animate-pulse rounded-2xl bg-muted" />}>
      <GoalSetupInner />
    </Suspense>
  );
}