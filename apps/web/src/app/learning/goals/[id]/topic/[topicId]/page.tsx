"use client";

/**
 * Topic page — TZ screen 4, FR-7.
 *
 * Explanation plus the "explain it differently" button. Regeneration sends the
 * previous text to the server, which is what makes FR-7.2's requirement of a
 * genuinely different angle enforceable rather than hopeful.
 */

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ChatDrawer } from "@/components/chat/chat-drawer";
import { ChatFab } from "@/components/chat/chat-fab";
import { SelectionAskAi } from "@/components/chat/selection-ask-ai";
import { MarkdownRenderer } from "@/components/shared/markdown-renderer";
import { TopicSourcePanel } from "../../../../_components/topic-source-panel";
import { CodeTaskDialog } from "../../../../_components/code-task-dialog";
import { ComprehensionCheck } from "../../../../_components/comprehension-check";
import {
  explainTopic,
  generateQuestion,
  getGoal,
  getLatestComprehension,
  getTopic,
  getTopicSources,
} from "@/lib/api/learning";
import type {
  ComprehensionLatest,
  LearningTopic,
  Question,
  TopicSourceFile,
} from "@/lib/api/learning";
import { useT } from "@/lib/i18n-context";
import { cn } from "@/lib/utils";

/** FR-7.2 asks for a different angle, not a rephrasing. */
const STYLES = [
  { value: "short", label: "Коротко" },
  { value: "detailed", label: "Подробно" },
  { value: "beginner", label: "Как новичку" },
  { value: "expert", label: "Как эксперту" },
  { value: "analogy", label: "Через аналогию" },
  { value: "example", label: "Через пример" },
] as const;

export default function TopicPage({
  params,
}: {
  params: Promise<{ id: string; topicId: string }>;
}) {
  const { id: goalId, topicId } = use(params);
  const t = useT();

  const [topic, setTopic] = useState<LearningTopic | null>(null);
  /** The goal's course, needed by the chat: chat sessions are course-scoped. */
  const [courseId, setCourseId] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [style, setStyle] = useState<string>("short");
  const [text, setText] = useState<string>("");
  const [loading, setLoading] = useState(false);
  // The learner names the book at generation time. "auto" keeps the old
  // course-wide keyword match (and honours a pinned passage); "ai" writes from
  // the model's own knowledge; anything else is one of the uploaded files.
  // A course holding several unrelated uploads makes the automatic match
  // confidently pick the wrong one, so an explicit file is the escape hatch.
  const [sourceFiles, setSourceFiles] = useState<TopicSourceFile[]>([]);
  const [sourceChoice, setSourceChoice] = useState<string>("auto");
  const [sourceLabel, setSourceLabel] = useState<string | null>(null);
  // FR-7.2: the previous attempt is sent along so the model avoids repeating it.
  const [previous, setPrevious] = useState<string>("");
  const [regenerations, setRegenerations] = useState(0);

  // The code practice window: a coding task generated for this topic, solved in
  // the browser's Python runner, submitted only once its checks pass.
  const [codeOpen, setCodeOpen] = useState(false);
  const [codeTask, setCodeTask] = useState<Question | null>(null);
  const [codeLoading, setCodeLoading] = useState(false);

  // The last comprehension check. Even when the explanation is not loaded on a
  // reload, this tells the card whether there is something to check (a saved
  // explanation) and shows the last result.
  const [latestCheck, setLatestCheck] = useState<ComprehensionLatest | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [data, goal, check, sources] = await Promise.all([
          getTopic(topicId),
          getGoal(goalId).catch(() => null),
          getLatestComprehension(topicId).catch(() => null),
          getTopicSources(topicId).catch(() => null),
        ]);
        if (!cancelled) {
          setTopic(data);
          setCourseId(goal?.course_id ?? null);
          setLatestCheck(check);
          setSourceFiles(sources?.files ?? []);
        }
      } catch (error) {
        if (!cancelled) {
          toast.error("Не удалось загрузить тему", {
            description: error instanceof Error ? error.message : undefined,
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [goalId, topicId]);

  async function loadExplanation(regenerate = false) {
    setLoading(true);
    try {
      const useAiOnly = sourceChoice === "ai";
      const chosenFile =
        sourceChoice === "auto" || useAiOnly ? null : sourceChoice;
      const result = await explainTopic(topicId, style, regenerate, {
        sourceFile: chosenFile,
        ground: !useAiOnly,
      });
      if (regenerate) setPrevious(text);
      setText(result.text);
      setSourceLabel(result.source_label ?? null);
      if (regenerate) setRegenerations((n) => n + 1);
    } catch (error) {
      toast.error("Не удалось получить объяснение", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }

  async function loadCodeTask() {
    setCodeLoading(true);
    setCodeOpen(true);
    try {
      setCodeTask(await generateQuestion(topicId, { format: "code" }));
    } catch (error) {
      toast.error("Не удалось подготовить задачу по коду", {
        description: error instanceof Error ? error.message : undefined,
      });
      setCodeOpen(false);
    } finally {
      setCodeLoading(false);
    }
  }

  const hasExplanation =
    Boolean(text) || Boolean(latestCheck?.has_explanation);
  const showComprehension =
    hasExplanation ||
    Boolean(latestCheck?.checked) ||
    latestCheck?.status === "in_progress";

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 md:px-10">
        <Link
          href={`/learning/goals/${goalId}`}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← К цели
        </Link>

        <header className="mt-3 mb-6">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {topic?.title ?? "Тема"}
          </h1>
          {topic?.description && (
            <p className="mt-2 text-sm text-muted-foreground">{topic.description}</p>
          )}
          {topic?.strength !== null && topic?.strength !== undefined && (
            <Badge variant="secondary" className="mt-3">
              сила знаний {Math.round(topic.strength * 100)}%
            </Badge>
          )}
        </header>

        <div className="card-shadow rounded-2xl border border-border bg-card p-6">
          <div className="flex flex-wrap gap-1.5">
            {STYLES.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setStyle(option.value)}
                aria-pressed={style === option.value}
                className={cn(
                  "rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                  style === option.value
                    ? "bg-brand text-brand-foreground"
                    : "bg-secondary text-secondary-foreground hover:bg-accent",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>

          {/* Which literature to write from. The learner chooses at generation
              time — the same place the explanation is asked for — because only
              they know which uploaded book this topic belongs to. "AI only"
              keeps the model from grounding in a course-mate's unrelated text. */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <label
              htmlFor="topic-source"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("topicSource.explainSource")}
            </label>
            <select
              id="topic-source"
              value={sourceChoice}
              onChange={(event) => setSourceChoice(event.target.value)}
              disabled={loading}
              className="max-w-full rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="auto">{t("topicSource.autoMatch")}</option>
              <option value="ai">{t("topicSource.aiOnly")}</option>
              {sourceFiles.map((file) => (
                <option key={file.name} value={file.name}>
                  {file.name}
                </option>
              ))}
            </select>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <Button onClick={() => loadExplanation(false)} disabled={loading}>
              {loading ? "Готовим…" : text ? "Объяснить заново" : "Объяснить"}
            </Button>
            {text && (
              <Button
                variant="outline"
                onClick={() => loadExplanation(true)}
                disabled={loading}
              >
                Объясни по-другому
              </Button>
            )}
          </div>

          {text ? (
            <>
              <article className="mt-5 text-sm leading-relaxed text-foreground">
                <SelectionAskAi
                  courseId={courseId}
                  enabled={Boolean(courseId)}
                >
                  <MarkdownRenderer content={text} />
                </SelectionAskAi>
              </article>
              {sourceLabel ? (
                <p className="mt-3 text-xs text-muted-foreground">
                  {t("topicSource.writtenFrom").replace("{label}", sourceLabel)}
                </p>
              ) : null}
            </>
          ) : (
            <p className="mt-5 text-sm text-muted-foreground">
              Выберите формат и нажмите «Объяснить». Если ИИ сейчас недоступен,
              можно читать материалы, повторять карточки и решать сохранённые
              задания. Чтобы вернуть объяснения, подключите локальную модель
              (Ollama или LM Studio) в настройках.
            </p>
          )}

          {regenerations > 0 && previous && (
            <p className="mt-5 text-xs text-muted-foreground">
              Показано {regenerations + 1}-й вариант — предыдущий исключён из
              подсказки, поэтому угол должен отличаться.
            </p>
          )}
        </div>
      </div>

      {/* The check follows the explanation because it quotes both the learner's
          answer and the text they just read. Hidden until there is something to
          check, so it cannot look like an exam gate before the lesson. */}
      {showComprehension && (
        <div className="mx-auto w-full max-w-3xl px-4 pb-6">
          <ComprehensionCheck
            topicId={topicId}
            topicTitle={topic?.title ?? ""}
            goalId={goalId}
            courseId={courseId}
            hasExplanation={hasExplanation}
            latest={latestCheck}
            onLatestChange={setLatestCheck}
          />
        </div>
      )}

      {/* Coding practice. Kept beside the explanation because the task is written
          from the same topic, and its checks run in the learner's own browser. */}
      <div className="mx-auto w-full max-w-3xl px-4 pb-6">
        <div className="card-shadow flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-card p-5">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-foreground">
              {t("codeTask.title")}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("codeTask.topicDescription")}
            </p>
          </div>
          <Button onClick={() => void loadCodeTask()} disabled={codeLoading}>
            {codeLoading ? t("codeTask.generating") : t("codeTask.open")}
          </Button>
        </div>
      </div>

      {/* Above the text on purpose: what the explanation will be written from
          is a choice, and a reader who cannot see which passage was used has
          no way to check it against their own book. */}
      <div className="mx-auto w-full max-w-3xl space-y-6 px-4 pb-10">
        <TopicSourcePanel topicId={topicId} />
      </div>

      <CodeTaskDialog
        open={codeOpen}
        onOpenChange={setCodeOpen}
        question={codeTask}
        generating={codeLoading}
        onRequestNew={() => void loadCodeTask()}
      />

      {courseId ? (
        <>
          <ChatFab open={chatOpen} onToggle={() => setChatOpen((v) => !v)} />
          <ChatDrawer
            courseId={courseId}
            open={chatOpen}
            onOpenChange={setChatOpen}
          />
        </>
      ) : null}
    </div>
  );
}