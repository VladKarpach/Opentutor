import { Suspense } from "react";

import { GoalSetup } from "../_components/goal-setup";

export default function NewGoalPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6 md:px-10">
        {/* GoalSetup reads ?course= so the goal is created inside a course. */}
        <Suspense fallback={<div className="h-64 animate-pulse rounded-2xl bg-muted" />}>
          <GoalSetup />
        </Suspense>
      </div>
    </div>
  );
}