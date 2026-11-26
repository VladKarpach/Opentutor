"use client";

/**
 * What this topic is taught from, and what to watch before starting.
 *
 * Two separate things, deliberately not merged. The source is the learner's own
 * uploaded literature, matched by keyword and pinnable by hand — it is what the
 * explanation will be written from, and saying so is what makes the text
 * checkable. The videos are links the learner supplied themselves: a model
 * asked for a video URL returns a plausible address for a video that does not
 * exist, and the learner only finds that out by clicking something which looked
 * trustworthy.
 */

import { useCallback, useEffect, useState } from "react";
import {
  BookMarked,
  ExternalLink,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  addTopicResource,
  deleteTopicResource,
  getTopicMaterial,
  listTopicResources,
  setTopicSource,
  type MaterialCandidates,
  type MaterialPassage,
  type TopicResource,
} from "@/lib/api/learning";
import { useT } from "@/lib/i18n-context";
import { TopicVideoSuggestions } from "./topic-video-suggestions";

export function TopicSourcePanel({ topicId }: { topicId: string }) {
  const t = useT();
  const [material, setMaterial] = useState<MaterialCandidates | null>(null);
  const [resources, setResources] = useState<TopicResource[]>([]);
  const [loading, setLoading] = useState(true);
  const [pinning, setPinning] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      const [materialData, resourceData] = await Promise.all([
        getTopicMaterial(topicId),
        listTopicResources(topicId),
      ]);
      setMaterial(materialData);
      setResources(resourceData);
    } catch {
      // Leave the panel out rather than showing "no literature" for a request
      // that failed — the two look identical and mean opposite things.
      setMaterial(null);
    } finally {
      setLoading(false);
    }
  }, [topicId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handlePin(nodeId: string | null) {
    setPinning(nodeId ?? "");
    try {
      await setTopicSource(topicId, nodeId);
      await load();
      toast.success(nodeId ? t("topicSource.pinned") : t("topicSource.unpinned"));
    } catch (error) {
      toast.error(t("topicSource.pinFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setPinning(null);
    }
  }

  async function handleAdd() {
    const trimmed = url.trim();
    if (!trimmed) return;
    setAdding(true);
    try {
      await addTopicResource(topicId, {
        url: trimmed,
        title: title.trim() || undefined,
      });
      setUrl("");
      setTitle("");
      await load();
      toast.success(t("topicSource.linkAdded"));
    } catch (error) {
      toast.error(t("topicSource.linkFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(resourceId: string) {
    try {
      await deleteTopicResource(topicId, resourceId);
      setResources((prev) => prev.filter((r) => r.id !== resourceId));
    } catch (error) {
      toast.error(t("topicSource.linkFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  if (loading) {
    return (
      <div className="h-24 animate-pulse rounded-2xl bg-muted" aria-busy="true" />
    );
  }

  const pinned = material?.source_node_id ?? null;
  const passages = material?.automatic ?? [];
  const hasLiterature = material?.course_has_material ?? false;

  return (
    <section
      className="card-shadow space-y-4 rounded-2xl border border-border bg-card p-5"
      data-testid="topic-source-panel"
    >
      {/* --- where the explanation comes from --- */}
      <div>
        <h2 className="flex items-center gap-2 text-base font-semibold tracking-tight text-foreground">
          <BookMarked className="size-4 text-muted-foreground" aria-hidden="true" />
          {t("topicSource.title")}
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">{t("topicSource.hint")}</p>
      </div>

      {material === null ? null : !hasLiterature ? (
        <p className="text-sm text-muted-foreground">{t("topicSource.noMaterial")}</p>
      ) : (
        <PassageList
          passages={passages}
          pinned={pinned}
          pinning={pinning}
          onPin={handlePin}
        />
      )}

      {/* --- videos the learner chose --- */}
      <div className="border-t border-border/60 pt-4">
        <h3 className="text-sm font-medium text-foreground">
          {t("topicSource.videos")}
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t("topicSource.videosHint")}
        </p>

        {resources.length > 0 && (
          <ul className="mt-3 space-y-1">
            {resources.map((resource) => (
              <li
                key={resource.id}
                className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-2 text-sm"
              >
                <a
                  href={resource.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-w-0 flex-1 items-center gap-1.5 truncate hover:underline"
                >
                  <ExternalLink
                    className="size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <span className="truncate">
                    {resource.title ?? resource.url}
                  </span>
                </a>
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => void handleRemove(resource.id)}
                  aria-label={t("topicSource.removeVideo")}
                >
                  <Trash2 className="size-3.5" aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-3 space-y-2">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…"
            aria-label={t("topicSource.videoUrl")}
          />
          <div className="flex gap-2">
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("topicSource.videoTitle")}
              aria-label={t("topicSource.videoTitle")}
            />
            <Button
              size="sm"
              onClick={() => void handleAdd()}
              disabled={adding || !url.trim()}
            >
              {adding ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Plus className="size-4" aria-hidden="true" />
              )}
              {t("topicSource.addVideo")}
            </Button>
          </div>
        </div>

        {/* Suggestions from the learner's own channels. Kept apart from the
            field above because the two make different promises: a pasted link is
            exactly what was pasted, while a suggestion was chosen for them. */}
        <TopicVideoSuggestions topicId={topicId} />
      </div>
    </section>
  );
}

function PassageList({
  passages,
  pinned,
  pinning,
  onPin,
}: {
  passages: MaterialPassage[];
  pinned: string | null;
  pinning: string | null;
  onPin: (nodeId: string | null) => void;
}) {
  const t = useT();
  if (passages.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">{t("topicSource.noMatch")}</p>
    );
  }

  return (
    <ul className="space-y-2">
      {passages.map((passage) => {
        const isPinned = passage.node_id === pinned;
        return (
          <li
            key={passage.node_id}
            className="rounded-xl border border-border/70 bg-muted/30 p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                {passage.title ?? passage.source_file ?? "—"}
              </span>
              {passage.source_file && (
                <Badge variant="outline" className="text-xs">
                  {passage.source_file}
                </Badge>
              )}
              <Button
                size="xs"
                variant={isPinned ? "default" : "outline"}
                onClick={() => onPin(isPinned ? null : passage.node_id)}
                disabled={pinning === passage.node_id}
              >
                {pinning === passage.node_id ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                ) : null}
                {isPinned ? t("topicSource.pinnedBadge") : t("topicSource.use")}
              </Button>
            </div>
            <p className="mt-1.5 line-clamp-3 text-xs text-muted-foreground">
              {passage.excerpt}
            </p>
            {/* The words that caused this match. Without them a learner cannot
                tell why an unrelated-looking chapter was offered, and the only
                recourse was to trust or distrust the list blindly. */}
            {passage.matched_terms.length > 0 ? (
              <p className="mt-1 text-[11px] text-muted-foreground">
                {t("topicSource.matchedTerms").replace(
                  "{terms}",
                  passage.matched_terms.slice(0, 5).join(", "),
                )}
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}