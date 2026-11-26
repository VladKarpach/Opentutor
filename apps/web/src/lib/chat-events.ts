/**
 * Cross-component chat events.
 *
 * Chat is opened by a local `useState` on each page. Notes (and any other
 * surface that can ask the tutor about a text selection) needs to open that
 * drawer without prop-drilling through the page tree, so we use one small
 * window event instead.
 */

export const CHAT_OPEN_EVENT = "opentutor:chat-open";

export interface ChatOpenDetail {
  courseId?: string;
}

/** Ask the mounted ChatDrawer to open. No-op during SSR. */
export function requestChatOpen(courseId?: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<ChatOpenDetail>(CHAT_OPEN_EVENT, { detail: { courseId } }),
  );
}
