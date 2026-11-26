"use client";

import { RuntimeAlert } from "@/components/shared/runtime-alert";
import { useDashboardData } from "./_hooks/use-dashboard-data";
import { CourseCardsSkeleton } from "./_components/dash-section";
import { DashboardSidebar } from "./_components/dashboard-sidebar";
import { CourseSpacesSection, DashboardEmptyState } from "./_components/dashboard-spaces";

/**
 * The one home page.
 *
 * It used to be a dashboard of ~12 panels layered over a course list, which made
 * it read as a different product from /learning: both showed courses, and the
 * panel that actually matters — the learner picture the planner is built from —
 * sat eighth, under four statistics nobody could act on.
 *
 * Now the page answers two questions in order: where are my courses. Goals live
 * at /learning, so they are deliberately not repeated here.
 */
export default function DashboardPage() {
  const { router, t, courses, loading, error, health } = useDashboardData();

  const navigate = (path: string) => router.push(path);

  return (
    <div className="min-h-screen bg-background">
      <div className="flex min-h-screen flex-col md:flex-row">
        <DashboardSidebar health={health} t={t} onNavigate={navigate} />

        <main className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-8 sm:px-6 md:px-10 md:py-12">
            <RuntimeAlert health={health} />

            {error && (
              <div className="rounded-2xl bg-destructive/5 px-5 py-4 text-sm text-destructive card-shadow">
                {t("dashboard.loadErrorPrefix")}: {error}
              </div>
            )}

            <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
              <div className="flex flex-col gap-1.5">
                <h1 className="text-2xl font-bold tracking-tight text-foreground">{t("dashboard.title")}</h1>
                <p className="text-sm text-muted-foreground">{t("dashboard.subtitle")}</p>
              </div>
              {/* Goals are the main thing being made, so creating one leads and
                  creating a course follows. */}
              <div className="flex shrink-0 flex-wrap gap-2 self-start sm:self-auto">
                {courses.length > 0 && (
                  <button
                    type="button"
                    onClick={() => router.push("/learning/new")}
                    className="h-10 rounded-full bg-brand px-5 text-sm font-medium text-brand-foreground transition-all hover:opacity-90 hover:shadow-md"
                  >
                    Новая цель
                  </button>
                )}
                <button
                  type="button"
                  data-testid="create-space-button"
                  onClick={() => router.push("/new")}
                  className="h-10 rounded-full border border-border px-5 text-sm font-medium text-foreground transition-all hover:bg-muted/60"
                >
                  + {t("dashboard.create")}
                </button>
              </div>
            </div>

            {loading && <CourseCardsSkeleton />}
            {courses.length > 0 && (
              <CourseSpacesSection courses={courses} onNavigate={navigate} t={t} />
            )}
            {!loading && courses.length === 0 && (
              <DashboardEmptyState onNavigate={navigate} t={t} />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
