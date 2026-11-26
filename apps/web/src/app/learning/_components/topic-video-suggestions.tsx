"use client";

/**
 * Videos from the learner's own channels that suit this topic.
 *
 * Separate from the manual link box on purpose. The learner chose the channels;
 * the system can only choose out of them, and every link shown was reported by
 * YouTube itself. Nothing here asks a model for a URL — that returns a
 * well-formed address for a video that does not exist, and the learner finds
 * that out by clicking something which looked trustworthy.
 *
 * Suggesting is not attaching. A click on "Добавить" is required, the same rule
 * the literature grounding follows.
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, Search, Tv } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n-context";
import { addTopicResource } from "@/lib/api/learning";
import {
  getTopicVideoCandidates,
  getVideoStatus,
  type VideoCandidate,
  type VideoStatus,
} from "@/lib/api";

export function TopicVideoSuggestions({ topicId }: { topicId: string }) {
  const t = useT();
  const [status, setStatus] = useState<VideoStatus | null>(null);
  const [candidates, setCandidates] = useState<VideoCandidate[] | null>(null);
  const [report, setReport] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [found, discovered] = await Promise.all([
        getVideoStatus(),
        getTopicVideoCandidates(topicId),
      ]);
      setStatus(found);
      setCandidates(discovered.candidates);
      setReport(discovered.report);
    } catch {
      // Leave the block out rather than claiming there are no suitable videos:
      // "could not ask" and "found nothing" are different facts.
      setCandidates(null);
    } finally {
      setLoading(false);
    }
  }, [topicId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleAdd(video: VideoCandidate) {
    setAdding(video.video_id);
    try {
      await addTopicResource(topicId, {
        url: video.url,
        title: video.title,
      });
      setCandidates((prev) => (prev ?? []).filter((c) => c.video_id !== video.video_id));
      toast.success(t("topicSource.linkAdded"));
    } catch (error) {
      toast.error(t("topicSource.linkFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setAdding(null);
    }
  }

  if (loading) {
    return <div className="h-20 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  if (candidates === null) return null;

  const channels = Number(report.channels ?? 0);
  // A key can be configured and still unable to search — the Data API allows
  // 100 catalogue searches a day, and each costs 100 units of a 10 000 quota.
  // When that happens the channels were not really consulted, so the block says
  // so instead of presenting the newest 15 videos as if they were the search.
  const searchFailed = Number(report.search_failed ?? 0) > 0;

  return (
    <div
      className="space-y-3 rounded-2xl border border-border bg-muted/20 p-4"
      data-testid="topic-video-suggestions"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Tv className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h3 className="text-sm font-medium text-foreground">
          {t("videoSuggestions.title")}
        </h3>
        {searchFailed ? (
          <Badge variant="outline" className="text-xs">
            {t("videoSuggestions.searchUnavailable")}
          </Badge>
        ) : (
          status && !status.api_search && (
            <Badge variant="outline" className="text-xs">
              {t("videoSuggestions.fifteenOnly")}
            </Badge>
          )
        )}
        <Button
          size="xs"
          variant="ghost"
          className="ml-auto"
          onClick={() => void load()}
          aria-label={t("videoSuggestions.rescan")}
        >
          <Search className="size-3.5" aria-hidden="true" />
          {t("videoSuggestions.rescan")}
        </Button>
      </div>

      {channels === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t("videoSuggestions.noChannels")}
        </p>
      ) : candidates.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {searchFailed
            ? t("videoSuggestions.searchBlockedHint")
            : t("videoSuggestions.noMatch")}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {candidates.map((video) => (
            <li
              key={video.video_id}
              className="flex items-start gap-2 rounded-lg bg-card px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <a
                  href={video.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block truncate text-sm text-foreground hover:underline"
                >
                  {video.title}
                </a>
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                  {video.channel_title && (
                    <span className="truncate text-[11px] text-muted-foreground">
                      {video.channel_title}
                    </span>
                  )}
                  {/* Why this one is offered. Which words matched makes a
                      same-language offer judgeable; when the topic is Russian
                      and the channel is English no word can match, so the
                      model's reason is the only thing that explains the pick
                      and it has to be shown instead of being hidden. */}
                  {video.reason ? (
                    <span className="text-[11px] text-muted-foreground/80">
                      {t("videoSuggestions.picked")}: {video.reason}
                    </span>
                  ) : (
                    video.matched_terms.length > 0 && (
                      <span className="text-[11px] text-muted-foreground/80">
                        {t("videoSuggestions.matched")}:{" "}
                        {video.matched_terms.slice(0, 5).join(", ")}
                      </span>
                    )
                  )}
                </div>
              </div>
              <Button
                size="xs"
                variant="outline"
                onClick={() => void handleAdd(video)}
                disabled={adding === video.video_id}
              >
                {adding === video.video_id ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Plus className="size-3.5" aria-hidden="true" />
                )}
                {t("videoSuggestions.add")}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}