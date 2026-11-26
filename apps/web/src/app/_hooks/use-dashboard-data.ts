"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useCourseStore } from "@/store/course";
import { getHealthStatus, type HealthStatus } from "@/lib/api";
import { ttlCache } from "@/lib/cache";
import { useT } from "@/lib/i18n-context";
import { initStudyNotifications } from "@/lib/study-notifications";

/**
 * Data for the home page.
 *
 * This used to fetch and aggregate a dozen dashboard panels — weekly report,
 * mastery overview, knowledge density, agent notifications, pending approvals,
 * mode recommendations, cross-course deadlines and review sessions. None of them
 * carried working data: they described an agent-driven multi-course workspace
 * that this deployment does not run, so they rendered empty cards or stale
 * figures while adding a dozen requests to every page load.
 *
 * What is left is what the home page actually shows: the course list, the
 * runtime health badge and the first-run setup redirect. The learner picture
 * ("что уже известно о вас") is fetched by LearnerContextCard itself, so it is
 * not duplicated here.
 */
export function useDashboardData() {
  const router = useRouter();
  const t = useT();
  const { courses, loading, error } = useCourseStore();

  const [health, setHealth] = useState<HealthStatus | null>(
    () => ttlCache.get<HealthStatus>("dash:health") ?? null,
  );

  // Onboarding + single-course redirect (only after initial fetch completes)
  const [fetchTriggered, setFetchTriggered] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!fetchTriggered || loading) return; // wait for initial fetch to complete

    // If courses exist, mark onboarded and skip setup.
    if (courses.length > 0) {
      try { window.localStorage.setItem("opentutor_onboarded", "true"); } catch { /* quota */ }
      return;
    }

    // No courses after fetch — check onboarded flag
    const onboarded = window.localStorage.getItem("opentutor_onboarded");
    if (!onboarded) {
      router.replace("/setup");
    }
  }, [router, courses, loading, fetchTriggered]);

  // Load courses and health + init study notifications
  useEffect(() => {
    useCourseStore.getState().fetchCourses().then(() => setFetchTriggered(true));
    const refreshHealth = () =>
      getHealthStatus()
        .then((d) => { ttlCache.set("dash:health", d, 30_000); setHealth(d); })
        .catch((e) => console.error("[Dashboard] health check failed:", e));
    refreshHealth();
    const id = setInterval(refreshHealth, 30_000);
    const cleanupNotif = initStudyNotifications();
    return () => { clearInterval(id); cleanupNotif(); };
  }, []);

  return { router, t, courses, loading, error, health };
}