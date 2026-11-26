"use client";

import Link from "next/link";

import { GlobalCalendar } from "../_components/global-calendar";

export default function LearningCalendarPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 md:px-10">
        <Link
          href="/learning"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Все цели
        </Link>
        <div className="mt-4">
          <GlobalCalendar />
        </div>
      </div>
    </div>
  );
}
