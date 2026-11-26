"use client";

/**
 * Which goal and which topic the cards and tests are drawn from.
 *
 * Both are the learner's to choose. Guessing the goal meant cards arriving about a
 * subject they were not working on, with no way to tell from the cards themselves
 * — the goal first, then the topic of that goal, is what makes the choice visible.
 */

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2 } from "lucide-react";

import {
  getFlashcardScope,
  type FlashcardScope,
  type FlashcardScopeGoal,
  type FlashcardScopeTopic,
} from "@/lib/api/practice";
import { useT } from "@/lib/i18n-context";

export function TopicScopePanel({
  courseId,
  /** Goal the caller already fixed (a quiz tied to one goal). */
  goalId,
  /** The learner's choice. Undefined means "follow the goal's own rule". */
  selectedTopicId,
  onTopicChange,
  onGoalChange,
  /** Whether the goal picker is offered. Off when only one goal exists. */
  showGoalPicker = true,
}: {
  courseId: string;
  goalId?: string;
  selectedTopicId?: string;
  onTopicChange?: (topicId: string | undefined) => void;
  onGoalChange?: (goalId: string) => void;
  showGoalPicker?: boolean;
}) {
  const t = useT();
  const [scope, setScope] = useState<FlashcardScope | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");

  useEffect(() => {
    // The first state write happens after the await, never before it: setting
    // "loading" synchronously inside the effect is a second render pass for a
    // state the component is already in.
    //
    // `live` drops the answer if the course changed or the panel closed while it
    // was in flight. Without it a slow first request can overwrite a fast second
    // one and leave the learner choosing topics from the previous course.
    let live = true;
    void (async () => {
      try {
        const next = await getFlashcardScope(courseId, goalId);
        if (!live) return;
        setScope(next);
        setState("ready");
      } catch {
        // A failed request is not the same as having no goal. Showing "you need a
        // goal" here would send the learner off to create one when the problem is
        // that the request did not come back.
        if (!live) return;
        setState("failed");
      }
    })();
    return () => {
      live = false;
    };
  }, [courseId, goalId]);

  const topics = useMemo<FlashcardScopeTopic[]>(
    () => scope?.topics ?? [],
    [scope],
  );
  const goals = useMemo<FlashcardScopeGoal[]>(
    () => scope?.goals ?? [],
    [scope],
  );

  // Still loading, or the answer never arrived: render nothing rather than a
  // flash of "no goal".
  if (state !== "ready") return null;

  // Cards need a goal. Saying so is more useful than a button that would produce
  // nothing, and it points at the action that fixes it.
  if (!scope?.goal_id) {
    return (
      <section className="card-shadow rounded-2xl border border-border bg-card p-5">
        <h3 className="text-base font-semibold tracking-tight text-foreground">
          {t("topic.needGoal")}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">{t("topic.needGoalHint")}</p>
      </section>
    );
  }

  return (
    <section className="card-shadow space-y-3 rounded-2xl border border-border bg-card p-4">
      {/* The goal comes first because it decides which topics there are to choose
          from. Offered whenever the course has more than one, whatever the
          caller's own default. */}
      {showGoalPicker && goals.length > 1 && onGoalChange ? (
        <div>
          <label
            htmlFor="scope-goal"
            className="text-sm font-medium text-foreground"
          >
            {t("topic.goalLabel")}
          </label>
          <select
            id="scope-goal"
            value={scope.goal_id}
            onChange={(e) => onGoalChange(e.target.value)}
            className="mt-2 h-10 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground"
          >
            {goals.map((goal) => (
              <option key={goal.goal_id} value={goal.goal_id}>
                {goal.goal_topic}
                {goal.topics_total > 0
                  ? ` · ${goal.topics_total}`
                  : ` · ${t("topic.goalNoTopics")}`}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div>
        <label
          htmlFor="flashcard-topic"
          className="text-sm font-medium text-foreground"
        >
          {t("topic.pickerLabel")}
        </label>
        <select
          id="flashcard-topic"
          value={selectedTopicId ?? ""}
          onChange={(e) => onTopicChange?.(e.target.value || undefined)}
          disabled={!onTopicChange}
          className="mt-2 h-10 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground disabled:opacity-60"
        >
          <option value="">
            {t("topic.pickerAuto").replace("{title}", scope.topic_title ?? "")}
          </option>
          {topics.map((topic) => (
            <option key={topic.topic_id} value={topic.topic_id}>
              {topic.topic_title}
            </option>
          ))}
        </select>
      </div>

      {scope.exam_passed ? (
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <CheckCircle2 className="size-3 text-success" aria-hidden="true" />
          {t("topic.examPassedHint").replace("{count}", String(scope.topics_total))}
        </p>
      ) : null}
    </section>
  );
}