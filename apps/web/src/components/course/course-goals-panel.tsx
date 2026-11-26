"use client";

/**
 * Goals that hang off this course workspace.
 *
 * Variant A of the course/goal integration: a goal may be attached to a course so
 * it can be planned against that course's materials and show up where the learner
 * already is. Goals without a course keep living under /learning.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createGoal,
  listCourseGoals,
  type LearningGoal,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";

function deadlineLabel(goal: LearningGoal): string | null {
  if (goal.days_left === null) return null;
  if (goal.days_left < 0) return "срок прошёл";
  if (goal.days_left === 0) return "срок сегодня";
  if (goal.days_left <= 7) return `${goal.days_left} дн. до срока`;
  return null;
}

export function CourseGoalsPanel({
  courseId,
  courseName,
}: {
  courseId: string;
  courseName?: string | null;
}) {
  const router = useRouter();
  const [goals, setGoals] = useState<LearningGoal[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await listCourseGoals(courseId);
      setGoals(data.goals);
    } catch (error) {
      toast.error("Не удалось загрузить цели курса", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate() {
    const value = topic.trim();
    if (!value) return;
    setBusy(true);
    try {
      // Prefill from the course name so a learner does not retype it.
      const goal = await createGoal({
        topic: value,
        days: 30,
        minutes_per_day: 60,
        course_id: courseId,
      });
      setTopic("");
      setCreating(false);
      toast.success("Цель создана");
      router.push(`/learning/goals/${goal.id}`);
    } catch (error) {
      toast.error("Не удалось создать цель", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="h-20 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  return (
    <section className="card-shadow rounded-2xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-tight text-foreground">
            Цели обучения
          </h2>
          {courseName && (
            <p className="text-xs text-muted-foreground">в курсе «{courseName}»</p>
          )}
        </div>
        {!creating && (
          <div className="flex flex-wrap items-center gap-2">
            {/* Goals have no top-level nav entry any more, so the course is the
                way in. Without this the goal list would be unreachable. */}
            {goals.length > 0 && (
              <Button
                size="xs"
                variant="ghost"
                onClick={() => router.push("/learning")}
              >
                Все цели
              </Button>
            )}
            <Button size="xs" variant="secondary" onClick={() => setCreating(true)}>
              Новая цель
            </Button>
          </div>
        )}
      </div>

      {creating && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
            }}
            placeholder={courseName ? `Что изучаем в курсе «${courseName}»?` : "Что изучаем?"}
            aria-label="Тема новой цели"
            className="max-w-md"
          />
          <Button onClick={handleCreate} disabled={busy || !topic.trim()}>
            {busy ? "Создаём…" : "Создать"}
          </Button>
          <Button variant="ghost" onClick={() => setCreating(false)} disabled={busy}>
            Отмена
          </Button>
        </div>
      )}

      {goals.length === 0 && !creating ? (
        <p className="mt-3 text-sm text-muted-foreground">
          Для этого курса целей пока нет. Нажмите «Новая цель» — план будет
          учитывать материалы курса.
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          {goals.map((goal) => {
            const deadline = deadlineLabel(goal);
            return (
              <li
                key={goal.id}
                className="relative rounded-xl border border-border bg-background p-3 transition-colors hover:border-brand/50"
              >
                <Link
                  href={`/learning/goals/${goal.id}`}
                  className="absolute inset-0 z-0 rounded-xl"
                  aria-label={`Открыть цель «${goal.topic}»`}
                />
                <div className="pointer-events-none relative z-10 flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-sm font-medium text-foreground">
                    {goal.topic}
                  </span>
                  <span className="flex items-center gap-2">
                    {goal.is_active && (
                      <Badge className="bg-brand text-brand-foreground text-xs">
                        для «Сегодня»
                      </Badge>
                    )}
                    {deadline && (
                      <Badge variant="outline" className="text-xs">
                        {deadline}
                      </Badge>
                    )}
                    {goal.exam_passed ? (
                      <Badge className="bg-success-muted text-success text-xs">
                        экзамен сдан
                      </Badge>
                    ) : null}
                    <span
                      className={cn(
                        "text-xs text-muted-foreground",
                        goal.topic_count === 0 && "text-warning",
                      )}
                    >
                      {goal.topic_count === 0
                        ? "темы не построены"
                        : `тем: ${goal.topic_count}`}
                    </span>
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}