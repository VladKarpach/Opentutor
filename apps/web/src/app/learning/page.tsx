"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { TopicNotes } from "@/components/sections/notes/topic-notes";
import {
  getLearningIndex,
  getLearnerContext,
  type LearnerContext,
  type LearningIndex,
} from "@/lib/api/learning";
import { streamChat } from "@/lib/api/chat";
import { cn } from "@/lib/utils";

function StatCard({
  title,
  value,
  subtitle,
  icon,
}: {
  title: string;
  value: React.ReactNode;
  subtitle?: string;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  const Icon = icon;
  return (
    <div className="rounded-xl border p-4 bg-card">
      {Icon && <Icon className="mb-2 size-5 text-muted-foreground" />}
      <div className="text-lg font-semibold text-foreground">{value}</div>
      <div className="text-sm text-muted-foreground">{title}</div>
      {subtitle && <div className="text-xs text-muted-foreground mt-1">{subtitle}</div>}
    </div>
  );
}

export default function LearningHomePage() {
  const [index, setIndex] = useState<LearningIndex | null>(null);
  const [context, setContext] = useState<LearnerContext | null>(null);
  const [loading, setLoading] = useState(true);

  // ── Ask AI state ──
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const [idx, ctx] = await Promise.all([
        getLearningIndex(),
        getLearnerContext(),
      ]);
      setIndex(idx);
      setContext(ctx);
    } catch (error) {
      toast.error("Не удалось загрузить страницу", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const courses = index?.courses ?? [];

  async function handleAskAI() {
    if (!chatInput.trim()) return;
    if (chatLoading) return;

    const userMsg = chatInput.trim();
    setChatInput("");
    setChatMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setChatLoading(true);

    abortControllerRef.current = new AbortController();

    try {
      const aiResponse: string[] = [];
      for await (const event of streamChat({
        courseId: "",
        message: userMsg,
        history: [],
        signal: abortControllerRef.current.signal,
      })) {
        if (event.type === "content") {
          aiResponse.push(event.content);
        }
        if (event.type === "done") {
          break;
        }
      }
      const fullResponse = aiResponse.join("");
      setChatMessages((prev) => [...prev, { role: "assistant", content: fullResponse }]);
    } catch (error) {
      toast.error("Не удалось получить ответ от ИИ", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setChatLoading(false);
      abortControllerRef.current = null;
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-background">
        <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6 md:px-10" aria-busy="true">
          <div className="h-28 animate-pulse rounded-2xl bg-muted" />
          <div className="h-28 animate-pulse rounded-2xl bg-muted" />
          <div className="h-28 animate-pulse rounded-2xl bg-muted" />
          <div className="h-28 animate-pulse rounded-2xl bg-muted" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 md:px-10">
        <header className="mb-10">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            OpenTutor
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Ваш локальный ИИ-репетитор. Всё работает на вашем компьютере.
          </p>
        </header>

        {/* === 1. Мои пространства === */}
        <section className="mb-10">
          <h2 className="mb-4 text-xl font-semibold text-foreground">Мои пространства</h2>
          {courses.length === 0 ? (
            <div className="rounded-xl border p-8 bg-card text-center">
              <p className="text-sm text-muted-foreground">
                Пространств пока нет. Создайте курс, чтобы добавить цели и материалы.
              </p>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {courses.map((course) => (
                <Link
                  key={course.course_id}
                  href={`/course/${course.course_id}`}
                  className="group block rounded-xl border p-4 hover:border-brand/50 transition-colors"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="text-base font-medium text-foreground group-hover:text-brand">
                        {course.course_name}
                      </h3>
                      <p className="text-xs text-muted-foreground mt-1">
                        {course.goal_count} цель{course.goal_count === 1 ? "" : "ей"}
                      </p>
                    </div>
                    <span className="text-xs font-medium bg-muted px-2 py-1 rounded">
                      {course.goal_count}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>

        {/* === 2. Конспекты === */}
        {/* The list used to show only a "Есть конспект" badge: the API already
            returned `note_markdown`, but this page rendered the row and dropped
            it, so the note itself was unreachable from the home screen. This is
            the same viewer the course's notes block uses — list plus the note. */}
        <section className="mb-10">
          <TopicNotes showAllGoals />
        </section>

        {/* === 3. Что известно обо мне === */}
        <section className="mb-10">
          <h2 className="mb-4 text-xl font-semibold text-foreground">
            Что известно обо мне
          </h2>
          {!context ? (
            <div className="rounded-xl border p-8 bg-card text-center">
              <p className="text-sm text-muted-foreground">
                Данные о прогрессе загрузятся после начала занятий.
              </p>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-3">
              <StatCard
                title="Целей"
                value={context.goals_seen ?? 0}
                subtitle="Всего создано целей"
              />
              <StatCard
                title="Тем затронуто"
                value={context.topics_touched ?? 0}
                subtitle="Тем, с которыми вы работали"
              />
              <StatCard
                title="Ответов"
                value={context.answers_total ?? 0}
                subtitle="Всего ответов на задания"
              />
              {context.weak && context.weak.length > 0 && (
                <div className="col-span-full rounded-lg border p-4 bg-card">
                  <p className="mb-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    Слабые места
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {context.weak.map((w: string) => (
                      <span key={w} className="text-xs bg-warning/10 text-warning rounded px-2 py-1">
                        {w}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {context.mean_score !== undefined && (
                <StatCard
                  title="Средняя оценка"
                  value={(context.mean_score * 100).toFixed(0) + "%"}
                  subtitle="По всем проверкам знаний"
                />
              )}
            </div>
          )}
        </section>

        {/* === 4. Спросить ИИ === */}
        <section className="mb-10">
          <div className="rounded-xl border p-5 bg-card">
            <h2 className="mb-4 text-xl font-semibold text-foreground">
              Спросить ИИ
            </h2>
            <div className="space-y-3">
              {chatMessages.length > 0 && (
                <div className="max-w-2xl space-y-2 text-sm">
                  {chatMessages.map((m, i) => (
                    <div
                      key={i}
                      className={cn(
                        "rounded-lg p-3 max-w-[80%]",
                        m.role === "user"
                          ? "ml-auto bg-brand text-brand-foreground"
                          : "bg-muted text-foreground",
                      )}
                    >
                      {m.content}
                    </div>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                <Input
                  placeholder="Напишите вопрос ИИ…"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void handleAskAI();
                    }
                  }}
                  disabled={chatLoading}
                />
                <Button
                  onClick={() => void handleAskAI()}
                  disabled={chatLoading || !chatInput.trim()}
                  className="items-center"
                >
                  {chatLoading ? (
                    <span className="flex items-center gap-2">
                      <svg
                        className="animate-spin h-4 w-4"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                        ></circle>
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M12 6a6 6 0 006 6z"
                        ></path>
                      </svg>
                      Думаю…
                    </span>
                  ) : (
                    <>
                      <Send className="size-4" aria-hidden="true" />
                      Спросить
                    </>
                  )}
                </Button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                ИИ отвечает на основе ваших материалов и прогресса обучения.
              </p>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
