"use client";

/**
 * The knowledge graph is not in use, so the route is kept but does not draw one.
 *
 * Deleting the file would break every link that still points here — the sidebar,
 * the block palette's stored layouts, old bookmarks — into a 404 that says
 * nothing about why. Redirecting sends the learner to the course they came from
 * and names the reason. When the graph is switched on, restoring the page is a
 * git revert of this file.
 */

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useT } from "@/lib/i18n-context";

export default function GraphPage() {
  const params = useParams();
  const router = useRouter();
  const t = useT();
  const courseId = params.id as string;

  useEffect(() => {
    router.replace(`/course/${courseId}`);
  }, [courseId, router]);

  return (
    <div
      className="flex-1 flex items-center justify-center p-8 text-center"
      role="status"
    >
      <p className="text-sm text-muted-foreground">{t("graph.hidden")}</p>
    </div>
  );
}