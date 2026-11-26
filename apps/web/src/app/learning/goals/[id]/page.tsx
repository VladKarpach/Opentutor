"use client";

/**
 * Goal workspace — the hub tying the TZ screens together.
 *
 * Tabs: диагностика (screen 2), план и темы (screen 3, merged so the schedule
 * and the topics are visible together), задания (screen 5), экзамен (screen 8),
 * прогресс (screen 9).
 */

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  activateGoal,
  getGoal,
  planExam,
  type LearningGoal,
} from "@/lib/api/learning";
import { DiagnosticDialog } from "../../_components/diagnostic-dialog";
import { ExamPanel } from "../../_components/exam-panel";
import { PlanChangesPanel } from "../../_components/plan-changes-panel";
import { PracticePanel } from "../../_components/practice-panel";
import { LearningCalendar } from "../../_components/learning-calendar";
import { ScheduleView } from "../../_components/schedule-view";
import { PlanView } from "../../_components/plan-view";
import { TopicBoard } from "../../_components/topic-board";
import { ProgressView } from "../../_components/progress-view";
import { cn } from "@/lib/utils";

const TABS = [
  { id: "diagnostic", label: "Диагностика" },
  // "План" and "Темы" used to be separate tabs, so seeing what to study and
  // when it was scheduled cost a switch. The schedule view merges them.
  { id: "plan", label: "План и темы" },
  { id: "calendar", label: "Календарь" },
  { id: "practice", label: "Задания" },
  { id: "exam", label: "Экзамен" },
  { id: "progress", label: "Прогресс" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function GoalPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const searchParams = useSearchParams();
  const [goal, setGoal] = useState<LearningGoal | null>(null);
  // A link from a topic's final comprehension card says "К практике" and opens
  // the workspace on the practice tab. Reading the query string through Next's
  // hook keeps the initial render consistent on the server and the client.
  const [tab, setTab] = useState<TabId>(() => {
    const requested = searchParams.get("tab");
    return TABS.some((entry) => entry.id === requested)
      ? (requested as TabId)
      : "diagnostic";
  });
  const [examHint, setExamHint] = useState<string | null>(null);
  const [activating, setActivating] = useState(false);
  // Topic the schedule wants practice to open on.
  const [practiceTopic, setPracticeTopic] = useState<string | null>(null);
  const [scheduleNonce, setScheduleNonce] = useState(0);

  function openPractice(topicId: string) {
    setPracticeTopic(topicId);
    setTab("practice");
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getGoal(id);
        if (!cancelled) setGoal(data);
      } catch (error) {
        if (!cancelled) {
          toast.error("Не удалось загрузить цель", {
            description: error instanceof Error ? error.message : undefined,
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function handleActivate() {
    setActivating(true);
    try {
      const updated = await activateGoal(id);
      setGoal(updated);
      toast.success("Цель выбрана активной", {
        description: "Теперь «Сегодня» открывает занятие по этой цели.",
      });
    } catch (error) {
      toast.error("Не удалось выбрать цель", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setActivating(false);
    }
  }

  async function handleExam() {
    try {
      const exam = await planExam(id);
      const weak = exam.topics.filter((t) => t.focus === "weak").length;
      setExamHint(
        `В экзамене ${exam.suggested_questions} вопросов, ` +
          `${weak} из них по слабым темам.`,
      );
    } catch (error) {
      toast.error("Экзамен пока недоступен", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 md:px-10">
        {goal && (
          <Link
            href={`/course/${goal.course_id}`}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← Курс: {goal.course_name}
          </Link>
        )}

        <header className="mt-3 mb-6">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {goal?.topic ?? "Загрузка…"}
          </h1>
          {goal && (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {goal.is_active && (
                <Badge className="bg-brand text-brand-foreground">
                  Активная цель · выполняется сейчас
                </Badge>
              )}
              <Badge variant="secondary">{goal.minutes_per_day} мин/день</Badge>
              {goal.days_left !== null && (
                <span>
                  {goal.days_left >= 0
                    ? `до срока ${goal.days_left} дн.`
                    : "срок прошёл"}
                </span>
              )}
              {examHint && <span className="text-brand">{examHint}</span>}
              {goal?.course_name && (
                <Link
                  href={`/course/${goal.course_id}`}
                  className="text-brand hover:underline underline-offset-2"
                >
                  курс: {goal.course_name}
                </Link>
              )}
            </div>
          )}
        </header>

        <nav className="mb-6 flex flex-wrap gap-1 border-b border-border" role="tablist">
          {TABS.map((entry) => (
            <button
              key={entry.id}
              role="tab"
              type="button"
              aria-selected={tab === entry.id}
              onClick={() => setTab(entry.id)}
              className={cn(
                "-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors",
                tab === entry.id
                  ? "border-brand text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {entry.label}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-1 py-1">
            {goal?.status === "active" && !goal.is_active && (
              <Button
                size="xs"
                variant="outline"
                onClick={handleActivate}
                disabled={activating}
              >
                {activating ? "Выбираем…" : "Сделать активной"}
              </Button>
            )}
            <Button asChild size="xs" variant="secondary">
              <Link href={`/learning/goals/${id}/today`}>
                Занятие на сегодня
              </Link>
            </Button>
            <Button size="xs" variant="ghost" onClick={handleExam}>
              Составить экзамен
            </Button>
          </div>
        </nav>

        <div role="tabpanel">
          {tab === "diagnostic" && <DiagnosticDialog goalId={id} />}
          {tab === "plan" && (
            <div className="space-y-6">
              {/* FR-5.6 / FR-6.6 / FR-9.1: the topics themselves — strength,
                  next review, mark "already known" or "skip", add and edit.
                  This panel existed but nothing rendered it, so the manual
                  topic controls the spec asks for were unreachable. */}
              <TopicBoard goalId={id} />

              {/* FR-10.4: what the replan changed, and a way to decline it. */}
              <PlanChangesPanel goalId={id} refreshKey={scheduleNonce} />

              {/* FR-6.5 / FR-6.4 / FR-7.4 / FR-15.1: the day-by-day plan, the
                  feasibility warnings, marking a session done and the calendar
                  export. Also orphaned until now. */}
              <PlanView goalId={id} onPlanChanged={() => setScheduleNonce((n) => n + 1)} />

              <ScheduleView
                key={scheduleNonce}
                goalId={id}
                onOpenPractice={openPractice}
                onPlanChanged={() => setScheduleNonce((n) => n + 1)}
              />
            </div>
          )}
          {tab === "calendar" && (
            <LearningCalendar goalId={id} isActive={goal?.is_active ?? false} />
          )}
          {tab === "practice" && (
            <PracticePanel
              goalId={id}
              focusTopicId={practiceTopic}
              onTopicDone={() => setScheduleNonce((n) => n + 1)}
            />
          )}
          {tab === "exam" && <ExamPanel goalId={id} />}
          {tab === "progress" && <ProgressView goalId={id} />}
        </div>
      </div>
    </div>
  );
}