/**
 * Video channels and the lesson videos drawn from them.
 *
 * The learner names the sources; the system chooses within them. No function here
 * asks a model for a link — every video returned was reported by YouTube.
 */

import { request } from "./client";

/** Which search mode is available, so a short list is not read as exhaustive. */
export interface VideoStatus {
  api_search: boolean;
  /** null means unlimited; 15 is what the public feed exposes. */
  max_videos_per_channel: number | null;
  hint_ru: string;
}

export function getVideoStatus(): Promise<VideoStatus> {
  return request("/video/status");
}

export interface VideoChannel {
  id: string;
  channel_id: string;
  handle: string | null;
  title: string | null;
  url: string | null;
  enabled: boolean;
  /** True when the channel applies to every course rather than one. */
  is_global: boolean;
  course_id: string | null;
  course_name: string | null;
  refresh_hours: number;
  last_fetched_at: string | null;
}

/**
 * Channels for this learner: the shared ones plus, when a course is named, that
 * course's own.
 */
export async function listVideoChannels(
  courseId?: string,
): Promise<VideoChannel[]> {
  const query = courseId ? `?course_id=${courseId}` : "";
  return request(`/video/channels${query}`);
}

/** What a pasted value resolves to, without storing it. */
export interface ResolvedChannel {
  channel_id: string;
  handle: string | null;
  title: string | null;
  url: string | null;
}

/**
 * Resolve before adding.
 *
 * A bare name is the ambiguous case — a channel, a user page and a bare name can
 * all point at different things — so the learner sees the real title first and
 * costs a click rather than a channel full of the wrong videos.
 */
export function resolveVideoChannel(value: string): Promise<ResolvedChannel> {
  return request("/video/resolve", {
    method: "POST",
    body: JSON.stringify({ value }),
  });
}

/** Add a channel. The value is anything copyable off YouTube. */
export function addVideoChannel(
  value: string,
  courseId?: string | null,
): Promise<VideoChannel> {
  return request("/video/channels", {
    method: "POST",
    body: JSON.stringify({ value, course_id: courseId ?? null }),
  });
}

export function setVideoChannelEnabled(
  channelId: string,
  enabled: boolean,
): Promise<VideoChannel> {
  return request(`/video/channels/${channelId}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export function deleteVideoChannel(channelId: string): Promise<void> {
  return request(`/video/channels/${channelId}`, { method: "DELETE" });
}

/** Re-read the feed now, so a new channel is known to be readable. */
export function refreshVideoChannel(channelId: string): Promise<VideoChannel> {
  return request(`/video/channels/${channelId}/refresh`, { method: "POST" });
}

export interface VideoCandidate {
  video_id: string;
  title: string;
  url: string;
  channel_id: string;
  channel_title: string;
  published_at: string | null;
  description: string;
  /** The words that made this a candidate. Shown so the offer can be judged. */
  matched_terms: string[];
  /**
   * Why the model chose this one, in the language of the topic. Empty when the
   * word match decided alone. A cross-language pick has no matched words — an
   * English title cannot contain a Russian query — so this is the only
   * justification such a result carries.
   */
  reason: string;
}

export interface VideoDiscovery {
  topic_id: string;
  candidates: VideoCandidate[];
  report: {
    channels?: number;
    api_search?: boolean;
    feed_only?: number;
    search_failed?: number;
    videos_seen?: number;
    already_added?: number;
  };
}

/** Videos from the learner's channels that could serve as this lesson. */
export function getTopicVideoCandidates(
  topicId: string,
): Promise<VideoDiscovery> {
  return request(`/learning/topics/${topicId}/video-candidates`);
}