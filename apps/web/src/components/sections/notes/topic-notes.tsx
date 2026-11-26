"use client";

/**
 * Notes, written per goal topic.
 *
 * The course's own note block summarised the course's chapter text, so on a course
 * whose material was not what the learner was studying the note was about the wrong
 * subject. Here a note summarises the explanation of one topic — the theory the
 * learner has already read — so it condenses rather than repeats it.
 *
 * Two scopes, both from the same index: the course block asks for one goal, the
 * home page asks for every topic.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  getTopicNotes,
  writeTopicNote,
  type TopicNoteIndexItem,
} from "@/lib/api/learning";
import { stabilizeMarkdownMermaidBlocks } from "@/lib/markdown/mermaid";
import { MarkdownRenderer } from "@/components/shared/markdown-renderer";
import { SelectionAskAi } from "@/components/chat/selection-ask-ai";
import { useT } from "@/lib/i18n-context";
import { cn } from "@/lib/utils";

function clean(markdown: string | null | undefined): string {
  if (!markdown) return "";
  return stabilizeMarkdownMermaidBlocks(markdown.replaceAll("\u0000", "").trim());
}

/** Topics listed before the "show the rest" control. A goal can have a dozen. */
const INITIAL_TOPIC_COUNT = 15;

export function TopicNotes({
  courseId,
  goalId,
  /** Every topic across every goal, grouped by goal. Used on the home page. */
  showAllGoals = false,
  aiActionsEnabled = true,
}: {
  courseId?: string;
  goalId?: string;
  showAllGoals?: boolean;
  aiActionsEnabled?: boolean;
}) {
  const t = useT();
  const [topics, setTopics] = useState<TopicNoteIndexItem[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await getTopicNotes(
        showAllGoals ? {} : { courseId, goalId },
      );
      setTopics(data.topics);
      setActiveId((current) =>
        current && data.topics.some((t) => t.topic_id === current)
          ? current
          : (data.topics[0]?.topic_id ?? null),
      );
    } catch {
      setTopics([]);
    }
  }, [courseId, goalId, showAllGoals]);

  useEffect(() => {
    void load();
  }, [load]);

  const active = useMemo(
    () => topics?.find((t) => t.topic_id === activeId) ?? null,
    [topics, activeId],
  );

  // Show the saved note as soon as a topic with one is picked.
  useEffect(() => {
    setMarkdown(clean(active?.note_markdown));
  }, [active?.topic_id, active?.note_markdown]);

  const handleWrite = useCallback(async () => {
    if (!active) return;
    setBusy(true);
    try {
      const result = await writeTopicNote(active.topic_id);
      setMarkdown(clean(result.markdown));
      if (!result.cached) {
        await load();
      }
    } catch (error) {
      toast.error(t("notes.writeFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }, [active, load, t]);

  if (topics === null) return null;

  if (topics.length === 0) {
    return (
      <section className="card-shadow rounded-2xl border border-border bg-card p-5">
        <h3 className="flex items-center gap-2 text-base font-semibold tracking-tight text-foreground">
          <FileText className="size-4 text-muted-foreground" aria-hidden="true" />
          {t("notes.title")}
        </h3>
        <p className="mt-1.5 text-xs text-muted-foreground">
          {/* The index lists only topics that have an explanation, so an empty list
              means exactly that — not "no notes written yet". The two states need
              different sentences, and the old one pointed at the wrong cause. */}
          {t("notes.noExplanation")}
        </p>
      </section>
    );
  }

  // Grouped by goal when the scope spans goals, so a long list stays readable.
  const grouped: Array<[string, TopicNoteIndexItem[]]> = showAllGoals
    ? Object.entries(
        topics.reduce<Record<string, TopicNoteIndexItem[]>>((acc, item) => {
          (acc[item.goal_topic] ??= []).push(item);
          return acc;
        }, {}),
      )
    : [["", topics]];

  // The first 15 across all groups; the rest sit behind a control rather than
  // pushing the note itself out of view.
  const flat = grouped.flatMap(([, items]) => items);
  const visible = showAll ? flat : flat.slice(0, INITIAL_TOPIC_COUNT);
  const hiddenCount = flat.length - visible.length;
  const visibleIds = new Set(visible.map((t) => t.topic_id));
  const groups: Array<[string, TopicNoteIndexItem[]]> = grouped
    .map(([name, items]) => [
      name,
      items.filter((item) => visibleIds.has(item.topic_id)),
    ] as [string, TopicNoteIndexItem[]])
    .filter(([, items]) => items.length > 0);

  return (
    <section className="card-shadow flex min-h-0 flex-col rounded-2xl border border-border bg-card">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 p-5">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-base font-semibold tracking-tight text-foreground">
            <FileText className="size-4 text-muted-foreground" aria-hidden="true" />
            {t("notes.title")}
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{t("notes.hint")}</p>
        </div>
        {active && aiActionsEnabled ? (
          <Button
            size="xs"
            variant="secondary"
            disabled={busy}
            onClick={() => void handleWrite()}
          >
            {busy ? (
              <Loader2 className="mr-1.5 size-3.5 animate-spin" aria-hidden="true" />
            ) : null}
            {active.has_note ? t("notes.rewrite") : t("notes.write")}
          </Button>
        ) : null}
      </header>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav
          className="shrink-0 space-y-3 overflow-y-auto border-b border-border/60 p-3 md:w-56 md:border-b-0 md:border-r"
          style={{ maxHeight: "min(60vh, 560px)" }}
          aria-label={t("notes.listLabel")}
        >
          {groups.map(([goalTopic, items]) => (
            <div key={goalTopic || "__single"}>
              {showAllGoals && goalTopic ? (
                <p className="mb-1 px-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {goalTopic}
                </p>
              ) : null}
              <ul className="space-y-0.5">
                {items.map((item) => (
                  <li key={item.topic_id}>
                    <button
                      type="button"
                      onClick={() => setActiveId(item.topic_id)}
                      aria-current={item.topic_id === activeId}
                      className={cn(
                        "w-full truncate rounded-lg px-2 py-1.5 text-left text-xs transition-colors",
                        item.topic_id === activeId
                          ? "bg-brand-muted font-medium text-brand"
                          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                      )}
                    >
                      {item.topic_title}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}

          {hiddenCount > 0 || showAll ? (
            <div className="mt-2 border-t border-border/60 pt-2">
              <button
                type="button"
                onClick={() => setShowAll((v) => !v)}
                className="w-full rounded-lg px-2 py-1.5 text-xs text-brand transition-colors hover:bg-muted/60"
              >
                {showAll
                  ? t("notes.showLess")
                  : t("notes.showMore").replace("{count}", String(hiddenCount))}
              </button>
            </div>
          ) : null}
        </nav>

        <div className="min-h-0 flex-1">
          {markdown ? (
            <>
              {/* A definite height with overflow, not a max-height on a flex
                  child: with no bounded parent `h-full` resolves to auto, the
                  column grows to the full length of the note and the page ends up
                  showing all of it with no scrolling. */}
              <div
                className="h-[60vh] min-h-[280px] overflow-y-auto scrollbar-thin md:h-[560px]"
              >
                <SelectionAskAi
                  key={active?.topic_id}
                  courseId={courseId}
                  enabled={aiActionsEnabled}
                  className="p-5 text-sm leading-relaxed text-foreground"
                >
                  <MarkdownRenderer content={markdown} />
                </SelectionAskAi>
              </div>
            </>
          ) : (
            <div className="flex h-full min-h-[160px] flex-col items-center justify-center gap-3 p-6 text-center">
              {active ? (
                <>
                  <Badge variant="secondary" className="text-xs">
                    {active.topic_title}
                  </Badge>
                  <p className="max-w-xs text-xs text-muted-foreground">
                    {t("notes.notWritten")}
                  </p>
                  {aiActionsEnabled ? (
                    <Button size="xs" disabled={busy} onClick={() => void handleWrite()}>
                      {t("notes.write")}
                    </Button>
                  ) : null}
                </>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}