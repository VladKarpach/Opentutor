"use client";

/**
 * The channels lesson videos may be drawn from.
 *
 * The learner picks the sources; the system only chooses within them. That is why
 * this list is edited by hand and stored, rather than searched for on the fly:
 * a channel the learner never chose would put videos from somewhere they did not
 * ask for into their lessons.
 *
 * A pasted value is resolved before it is stored, and the resolved name shown,
 * because a bare word is ambiguous — a channel, a user page and a short name can
 * all point at different things.
 */

import { useCallback, useEffect, useState } from "react";
import { Globe, Loader2, Plus, Tv, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  addVideoChannel,
  deleteVideoChannel,
  getVideoStatus,
  listVideoChannels,
  resolveVideoChannel,
  setVideoChannelEnabled,
  type ResolvedChannel,
  type VideoChannel,
  type VideoStatus,
} from "@/lib/api";
import { useT } from "@/lib/i18n-context";

export function VideoChannelsPanel({
  courseId,
  courseName,
}: {
  courseId?: string;
  courseName?: string;
}) {
  const t = useT();
  const [channels, setChannels] = useState<VideoChannel[] | null>(null);
  const [status, setStatus] = useState<VideoStatus | null>(null);
  const [value, setValue] = useState("");
  const [preview, setPreview] = useState<ResolvedChannel | null>(null);
  const [busy, setBusy] = useState(false);
  const [resolving, setResolving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [found, listed] = await Promise.all([
        getVideoStatus(),
        listVideoChannels(courseId),
      ]);
      setStatus(found);
      setChannels(listed);
    } catch {
      setChannels(null);
    }
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleResolve() {
    const trimmed = value.trim();
    if (!trimmed) return;
    setResolving(true);
    setPreview(null);
    try {
      setPreview(await resolveVideoChannel(trimmed));
    } catch (error) {
      toast.error(t("videoChannels.resolveFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setResolving(false);
    }
  }

  async function handleAdd() {
    const trimmed = value.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await addVideoChannel(trimmed, courseId ?? null);
      setValue("");
      setPreview(null);
      await load();
      toast.success(t("videoChannels.added"));
    } catch (error) {
      toast.error(t("videoChannels.addFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleToggle(channel: VideoChannel) {
    try {
      await setVideoChannelEnabled(channel.id, !channel.enabled);
      await load();
    } catch (error) {
      toast.error(t("videoChannels.changeFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  async function handleRemove(channel: VideoChannel) {
    try {
      await deleteVideoChannel(channel.id);
      setChannels((prev) => (prev ?? []).filter((c) => c.id !== channel.id));
    } catch (error) {
      toast.error(t("videoChannels.changeFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  return (
    <section
      className="card-shadow rounded-2xl border border-border bg-card p-5"
      data-testid="video-channels-panel"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Tv className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h2 className="text-base font-semibold tracking-tight text-foreground">
          {t("videoChannels.title")}
        </h2>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {courseId
          ? t("videoChannels.hintCourse").replace("{course}", courseName ?? "")
          : t("videoChannels.hintGlobal")}
      </p>

      {/* Which search mode is in use. Said plainly, because the two produce very
          different lists and a short one must not look exhaustive. */}
      {status && !status.api_search && (
        <p
          className="mt-3 rounded-lg border border-warning/40 bg-warning-muted/30 px-3 py-2 text-xs"
          data-testid="video-no-key"
        >
          {status.hint_ru}
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <Input
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setPreview(null);
          }}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            // Resolve first when there is nothing to confirm, so the common case
            // is one Enter rather than two.
            if (preview) void handleAdd();
            else void handleResolve();
          }}
          placeholder="@название или ссылка на канал"
          aria-label={t("videoChannels.input")}
          className="max-w-sm"
        />
        <Button
          size="sm"
          variant="outline"
          onClick={() => void handleResolve()}
          disabled={resolving || !value.trim()}
        >
          {resolving ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : null}
          {t("videoChannels.check")}
        </Button>
        <Button
          size="sm"
          onClick={() => void handleAdd()}
          disabled={busy || !value.trim()}
        >
          <Plus className="size-4" aria-hidden="true" />
          {t("videoChannels.add")}
        </Button>
      </div>

      {preview && (
        <p className="mt-2 rounded-lg bg-muted/50 px-3 py-2 text-xs text-foreground">
          {t("videoChannels.willAdd")}:{" "}
          <span className="font-medium">{preview.title ?? preview.channel_id}</span>
        </p>
      )}

      {channels === null ? null : channels.length === 0 ? (
        <p className="mt-4 text-xs text-muted-foreground">
          {t("videoChannels.empty")}
        </p>
      ) : (
        <ul className="mt-4 space-y-1">
          {channels.map((channel) => (
            <li
              key={channel.id}
              className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-2 text-sm"
            >
              {channel.is_global ? (
                <Badge variant="outline" className="shrink-0 text-xs">
                  <Globe className="size-3" aria-hidden="true" />
                  {t("videoChannels.global")}
                </Badge>
              ) : (
                <Badge variant="secondary" className="shrink-0 text-xs">
                  {channel.course_name ?? t("videoChannels.course")}
                </Badge>
              )}
              <a
                href={channel.url ?? `https://www.youtube.com/channel/${channel.channel_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="min-w-0 flex-1 truncate hover:underline"
              >
                {channel.title ?? channel.channel_id}
              </a>
              <Button
                size="xs"
                variant="ghost"
                onClick={() => void handleToggle(channel)}
                aria-pressed={channel.enabled}
                aria-label={
                  channel.enabled
                    ? t("videoChannels.disable")
                    : t("videoChannels.enable")
                }
              >
                {channel.enabled ? t("videoChannels.on") : t("videoChannels.off")}
              </Button>
              <Button
                size="xs"
                variant="ghost"
                onClick={() => void handleRemove(channel)}
                aria-label={t("videoChannels.remove")}
              >
                <Trash2 className="size-3.5" aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}