"use client";

import { TopicNotes } from "@/components/sections/notes/topic-notes";
import type { BlockComponentProps } from "@/lib/block-system/registry";

export default function NotesBlock({ courseId, aiActionsEnabled }: BlockComponentProps) {
  return (
    <div role="region" aria-label="Notes">
      {/* Scoped to this course: its goals, not the course's chapter tree. */}
      <TopicNotes courseId={courseId} aiActionsEnabled={aiActionsEnabled} />
    </div>
  );
}
