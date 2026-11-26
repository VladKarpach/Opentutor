"use client";

import { useCallback } from "react";
import { useParams } from "next/navigation";
import { WorkspaceHeader } from "@/components/shell/workspace-header";
import { IngestionProgress } from "@/components/shared/ingestion-progress";
import { CourseGoalsPanel } from "@/components/course/course-goals-panel";
import { CourseMaterialsPanel } from "@/components/course/course-materials-panel";
import { VideoChannelsPanel } from "@/components/course/video-channels-panel";
import { SyncSettingsPanel } from "@/components/course/sync-settings-panel";
import { useT } from "@/lib/i18n-context";
import { useCourseData } from "./_components/use-course-data";

export default function CoursePage() {
  const params = useParams();
  const t = useT();
  const courseId = params.id as string;

  const { course } = useCourseData(courseId);

  const handleIngestionComplete = useCallback(() => {
    // Intentionally empty - no block grid on course page
  }, []);

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <WorkspaceHeader courseName={course?.name || t("course.defaultTitle")} courseId={courseId} />

      <div className="px-5 pt-4 max-w-5xl mx-auto w-full space-y-3">
        <SyncSettingsPanel courseId={courseId} />
        <IngestionProgress
          courseId={courseId}
          onIngestionComplete={handleIngestionComplete}
        />
      </div>

      <main className="flex-1 max-w-5xl mx-auto w-full px-5 py-8 space-y-6">
        <CourseMaterialsPanel courseId={courseId} />
        <VideoChannelsPanel courseId={courseId} courseName={course?.name} />
        <CourseGoalsPanel courseId={courseId} courseName={course?.name} />
      </main>
    </div>
  );
}