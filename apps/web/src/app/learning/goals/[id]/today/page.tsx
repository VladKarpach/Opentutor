"use client";

import { use } from "react";
import { useSearchParams } from "next/navigation";

import { DailySessionView } from "../../../_components/daily-session";

/**
 * The second way into learning: instead of opening the workspace and choosing a
 * tab, the learner opens this page and starts. The session itself is assembled
 * by the backend from the existing plan.
 *
 * `fresh=1` is used after taking a future day early: today's session has new
 * steps, so the old saved position must not hide them.
 */
export default function DailySessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const searchParams = useSearchParams();
  const fresh = searchParams.get("fresh") === "1";
  return <DailySessionView goalId={id} fresh={fresh} />;
}
