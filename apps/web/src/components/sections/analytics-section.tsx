"use client";

import { lazy } from "react";
import { useT } from "@/lib/i18n-context";
import { TabbedSection, type TabDef } from "./tabbed-section";

// The "progress" and "graph" tabs are gone, not hidden: the progress panel reads
// the base project's per-node LearningProgress, which this deployment does not
// fill, and the knowledge graph is not in use. Their views still exist and are
// still rendered elsewhere — the goal page's progress tab and the graph blocks —
// so nothing is deleted here.
const ForecastView = lazy(() =>
  import("./analytics/forecast-view").then((m) => ({ default: m.ForecastView })),
);
const ProfileView = lazy(() =>
  import("./analytics/profile-view").then((m) => ({ default: m.ProfileView })),
);
const MisconceptionView = lazy(() =>
  import("./analytics/misconception-view").then((m) => ({ default: m.MisconceptionView })),
);
const ReviewSummaryView = lazy(() =>
  import("./analytics/review-summary-view").then((m) => ({ default: m.ReviewSummaryView })),
);
const AgentTimeline = lazy(() =>
  import("./agent-timeline").then((m) => ({ default: m.AgentTimeline })),
);

interface AnalyticsSectionProps {
  courseId: string;
  defaultTab?: AnalyticsTab;
}

type AnalyticsTab = "review" | "blindspots" | "forecast" | "agent" | "profile";

export function AnalyticsSection({ courseId, defaultTab = "profile" }: AnalyticsSectionProps) {
  const t = useT();
  const tabs: TabDef<AnalyticsTab>[] = [
    { id: "review", label: t("analytics.tab.review"), testId: "right-tab-review" },
    { id: "blindspots", label: t("analytics.tab.blindspots"), testId: "right-tab-blindspots" },
    { id: "forecast", label: t("analytics.tab.forecast"), testId: "right-tab-forecast" },
    { id: "agent", label: t("analytics.tab.agent"), testId: "right-tab-agent" },
    { id: "profile", label: t("analytics.tab.profile") },
  ];

  return (
    <div role="region" aria-label="Analytics">
    <TabbedSection
      tabs={tabs}
      defaultTab={tabs.some((tab) => tab.id === defaultTab) ? defaultTab : "profile"}
      testId="analytics-section"
    >
      {(activeTab) => (
        <>
          {activeTab === "review" ? <ReviewSummaryView courseId={courseId} /> : null}
          {activeTab === "blindspots" ? <MisconceptionView courseId={courseId} /> : null}
          {activeTab === "forecast" ? <ForecastView courseId={courseId} /> : null}
          {activeTab === "agent" ? <AgentTimeline courseId={courseId} /> : null}
          {activeTab === "profile" ? <ProfileView courseId={courseId} /> : null}
        </>
      )}
    </TabbedSection>
    </div>
  );
}
