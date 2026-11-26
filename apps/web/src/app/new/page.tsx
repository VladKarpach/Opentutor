"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2 } from "lucide-react";

import { useT } from "@/lib/i18n-context";
import { useCourseStore } from "@/store/course";
import { useWorkspaceStore } from "@/store/workspace";
import { buildLayoutFromMode } from "@/lib/block-system/templates";
import { persistCourseSpaceLayoutLocally } from "@/lib/block-system/layout-sync";
import type { LearningMode } from "@/lib/block-system/types";

const DEFAULT_LEARNING_MODE: LearningMode = "course_following";

/**
 * Create a space — the whole form is a name.
 *
 * Uploading, URL scraping, mode selection and feature toggles all made this page
 * a wizard that stood between a returning learner and an empty course they could
 * already start using. Materials are added from the workspace's materials panel
 * (/course/[id]), and the learning mode can be changed there too, so nothing is
 * lost by asking for only the one thing that cannot be changed trivially later.
 */
export default function NewProjectPage() {
  const router = useRouter();
  const t = useT();
  const addCourse = useCourseStore((s) => s.addCourse);

  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleCreate(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (submitting) return;

    const trimmed = name.trim();
    if (!trimmed) {
      setError(t("new.projectNameRequired"));
      return;
    }
    if (trimmed.length > 100) {
      setError(t("new.projectNameTooLong"));
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const layout = buildLayoutFromMode(DEFAULT_LEARNING_MODE);
      const course = await addCourse(trimmed, undefined, {
        learning_mode: DEFAULT_LEARNING_MODE,
        spaceLayout: layout,
      });

      // Prime the workspace state so the first paint on /course is mode-correct.
      useWorkspaceStore.getState().loadBlocks(layout);
      persistCourseSpaceLayoutLocally(course.id, layout);

      router.push(`/course/${course.id}`);
    } catch (err) {
      // A bare network failure ("Failed to fetch") is not actionable for the
      // learner — the API client already raises its own toast for HTTP errors,
      // so keep the inline message human and localized.
      const message = err instanceof Error && err.message !== "Failed to fetch"
        ? err.message
        : t("new.createFailed");
      setError(message);
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center p-6">
      <div className="w-full max-w-md flex flex-col gap-6 animate-fade-in">
        <button
          type="button"
          data-testid="new-back-mode"
          onClick={() => router.push("/")}
          className="flex w-fit items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
          {t("settings.back")}
        </button>

        <div className="flex flex-col gap-1.5">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {t("new.createTitle")}
          </h1>
          <p className="text-sm text-muted-foreground">{t("new.nameOnlySubtitle")}</p>
        </div>

        <form
          onSubmit={handleCreate}
          className="flex flex-col gap-4 rounded-2xl bg-card p-6 card-shadow"
        >
          <div className="flex flex-col gap-2">
            <label htmlFor="project-name" className="font-semibold text-sm text-foreground">
              {t("new.projectName")}
            </label>
            <input
              id="project-name"
              data-testid="project-name-input"
              autoFocus
              autoComplete="off"
              maxLength={100}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (error) setError(null);
              }}
              placeholder={t("new.projectNamePlaceholder")}
              className={`h-11 w-full rounded-lg border bg-background px-4 text-sm text-foreground focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20 ${
                error ? "border-destructive" : "border-border"
              }`}
            />
            {error && (
              <p data-testid="project-name-error" className="text-xs text-destructive">
                {error}
              </p>
            )}
          </div>

          <button
            type="submit"
            data-testid="create-space"
            disabled={submitting}
            className="flex h-11 items-center justify-center gap-2 rounded-lg bg-brand text-sm font-semibold text-brand-foreground transition-all hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting && <Loader2 className="size-4 animate-spin" />}
            {t("new.create")}
          </button>
        </form>
      </div>
    </div>
  );
}
