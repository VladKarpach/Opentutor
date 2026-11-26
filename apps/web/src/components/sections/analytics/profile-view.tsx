"use client";

/**
 * The profile: what is already known about this learner.
 *
 * It used to be a management screen — preferences, signals, memories, each with a
 * dismiss button, plus session counters read from the browser's local storage.
 * None of that describes the person. The learner opening "Профиль" is asking what
 * the system has worked out about them, and the answer is one thing: the learner
 * context, the same picture the planner is built from. The editable rows also had
 * a real cost — dismissing a preference or a memory there changed what the planner
 * was given, on a screen that did not say so.
 *
 * The endpoints stay wired up in lib/api; nothing else uses them, and restoring
 * this screen is a git revert.
 */

import { LearnerContextCard } from "@/app/learning/_components/learner-context-card";

export function ProfileView({ courseId }: { courseId: string }) {
  return (
    <div
      className="flex-1 overflow-y-auto scrollbar-thin p-4 space-y-4"
      data-testid="profile-view"
    >
      <LearnerContextCard courseId={courseId} />
    </div>
  );
}