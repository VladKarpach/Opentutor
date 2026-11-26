"use client";

/**
 * Select-to-ask for explanation text.
 *
 * Wraps a block of prose. When the learner selects a passage inside it, a small
 * toolbar appears above the selection with two actions:
 *  - "Объяснить" sends the excerpt to the tutor as an explanation request;
 *  - "Спросить" opens a one-line input for a question about that excerpt.
 *
 * The message goes through the ordinary chat stream (see `useChatStore`), and
 * `requestChatOpen` asks the page's ChatDrawer to slide in. This keeps the
 * feature on the same orchestration path as normal chat instead of adding a
 * second LLM call path.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { MessageCircleQuestion, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { requestChatOpen } from "@/lib/chat-events";
import { useT } from "@/lib/i18n-context";
import { useChatStore } from "@/store/chat";

interface SelectionAskAiProps {
  /** Course the question belongs to. Without it the toolbar stays hidden. */
  courseId?: string | null;
  /** Feature flag from the health check / block config. */
  enabled?: boolean;
  className?: string;
  children: ReactNode;
}

interface SelectionState {
  text: string;
  left: number;
  top: number;
}

export function SelectionAskAi({
  courseId,
  enabled = true,
  className,
  children,
}: SelectionAskAiProps) {
  const t = useT();
  const sendMessage = useChatStore((s) => s.sendMessage);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const contentRef = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<SelectionState | null>(null);
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");

  const canAsk = Boolean(courseId) && enabled && !isStreaming;

  const clearSelection = useCallback(() => {
    setSelection(null);
    setAsking(false);
    setQuestion("");
  }, []);

  const captureSelection = useCallback(() => {
    if (!canAsk) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      setSelection(null);
      return;
    }
    const text = sel.toString().trim();
    if (text.length < 3) {
      setSelection(null);
      return;
    }
    const anchor = sel.anchorNode;
    if (!anchor || !contentRef.current?.contains(anchor)) {
      setSelection(null);
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) {
      setSelection(null);
      return;
    }
    setAsking(false);
    setQuestion("");
    setSelection({
      text: text.slice(0, 4000),
      left: Math.min(Math.max(rect.left + rect.width / 2, 140), window.innerWidth - 140),
      top: Math.max(rect.top - 10, 56),
    });
  }, [canAsk]);

  // Shift+Arrow selections never fire mouseup.
  const handleKeyUp = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.shiftKey || event.key === "Shift") captureSelection();
    },
    [captureSelection],
  );

  const handleExplain = useCallback(async () => {
    if (!selection || !courseId) return;
    const excerpt = selection.text;
    clearSelection();
    window.getSelection()?.removeAllRanges();
    requestChatOpen(courseId);
    await sendMessage(
      courseId,
      t("chat.explainSelectionMessage").replace("{text}", excerpt),
      { explainSelection: true, selectionText: excerpt },
    );
  }, [clearSelection, courseId, selection, sendMessage, t]);

  const handleAsk = useCallback(async () => {
    if (!selection || !courseId) return;
    const prompt = question.trim();
    if (!prompt) return;
    const excerpt = selection.text;
    clearSelection();
    window.getSelection()?.removeAllRanges();
    requestChatOpen(courseId);
    await sendMessage(
      courseId,
      t("chat.askAboutSelectionMessage").replace("{question}", prompt),
      { askQuestion: true, questionText: prompt, selectionText: excerpt },
    );
  }, [clearSelection, courseId, question, selection, sendMessage, t]);

  // Escape dismisses the toolbar.
  useEffect(() => {
    if (!selection) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") clearSelection();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [clearSelection, selection]);

  return (
    <div
      ref={contentRef}
      className={className}
      onMouseUp={captureSelection}
      onKeyUp={handleKeyUp}
    >
      {children}

      {selection ? (
        <div
          role="dialog"
          aria-label={t("chat.selectionActions")}
          className="fixed z-50 w-72 -translate-x-1/2 -translate-y-full rounded-xl border border-border bg-popover p-2.5 shadow-xl"
          style={{ left: selection.left, top: selection.top }}
          onMouseDown={(event) => event.preventDefault()}
        >
          <p className="mb-2 max-h-9 overflow-hidden text-[11px] leading-snug text-muted-foreground">
            «{selection.text}»
          </p>
          {asking ? (
            <form
              className="flex items-center gap-1.5"
              onSubmit={(event) => {
                event.preventDefault();
                void handleAsk();
              }}
            >
              <input
                autoFocus
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder={t("chat.selectionQuestionPlaceholder")}
                aria-label={t("chat.selectionQuestionPlaceholder")}
                className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1.5 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
              <Button
                type="submit"
                size="xs"
                disabled={!question.trim()}
                className="shrink-0"
              >
                {t("chat.askSubmit")}
              </Button>
            </form>
          ) : (
            <div className="flex items-center gap-1.5">
              <Button
                size="xs"
                className="flex-1"
                onClick={() => void handleExplain()}
              >
                <Sparkles className="mr-1 size-3.5" aria-hidden="true" />
                {t("chat.explainSelectionButton")}
              </Button>
              <Button
                size="xs"
                variant="outline"
                className="flex-1"
                onClick={() => setAsking(true)}
              >
                <MessageCircleQuestion className="mr-1 size-3.5" aria-hidden="true" />
                {t("chat.askAboutSelectionButton")}
              </Button>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
