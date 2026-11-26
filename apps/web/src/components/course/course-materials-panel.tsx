"use client";

/**
 * The course's literature: what the explanations will be written from.
 *
 * Upload could previously only happen while creating a course or from the chat's
 * paperclip, so a learner who finished setup had nowhere to add the book they
 * are actually studying from — and nothing they added was reachable from the
 * explanation anyway. This is the place that is obviously missing.
 *
 * Parsing happens in the background, so the list refreshes on its own: a file
 * appears here before its text is searchable, which is why the panel says so
 * rather than pretending the upload finished the work.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, FileText, Link2, Loader2, Upload } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  listCourseFiles,
  scrapeUrl,
  uploadFile,
  type CourseFile,
} from "@/lib/api/courses";
import { useT } from "@/lib/i18n-context";

const ACCEPT = ".pdf,.pptx,.ppt,.docx,.doc,.html,.htm,.txt,.md";

export function CourseMaterialsPanel({ courseId }: { courseId: string }) {
  const t = useT();
  const [files, setFiles] = useState<CourseFile[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [url, setUrl] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setFiles(await listCourseFiles(courseId));
    } catch {
      // A failed listing is not the same as having no literature, so this stays
      // out of the way rather than claiming the course is empty.
      setFiles(null);
    }
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Ingestion is background work: keep asking until the new file shows up.
  useEffect(() => {
    if (busy || files === null) return;
    const timer = setInterval(() => void load(), 4000);
    return () => clearInterval(timer);
  }, [busy, files, load]);

  async function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const picked = event.target.files?.[0];
    event.target.value = "";
    if (!picked) return;

    setBusy(true);
    setProgress(0);
    try {
      const result = await uploadFile(courseId, picked, (pct) => setProgress(pct));
      if (result.duplicate) {
        toast.success(t("materials.alreadyUploaded"), {
          description: picked.name,
        });
      } else {
        toast.success(t("materials.uploaded"), { description: picked.name });
      }
      // The list must not wait for the next poll tick: with a duplicate there is
      // no new row to wait for, and leaving `busy` set disabled the control and
      // stopped the polling effect altogether.
      await load();
    } catch (error) {
      toast.error(t("materials.uploadFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  async function handleUrl() {
    const trimmed = url.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await scrapeUrl(courseId, trimmed);
      setUrl("");
      toast.success(t("materials.urlAccepted"));
    } catch (error) {
      toast.error(t("materials.urlFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="card-shadow rounded-2xl border border-border bg-card p-5"
      data-testid="course-materials"
    >
      <div className="flex flex-wrap items-center gap-2">
        <BookOpen className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h2 className="text-base font-semibold tracking-tight text-foreground">
          {t("materials.title")}
        </h2>
        {files !== null && files.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {t("materials.count").replace("{count}", String(files.length))}
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("materials.hint")}</p>

      <div className="mt-4 flex flex-wrap gap-2">
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={handleFile}
          data-testid="materials-file-input"
        />
        <Button
          size="sm"
          variant="outline"
          onClick={() => fileInput.current?.click()}
          disabled={busy}
        >
          {busy && progress !== null ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              {progress}%
            </>
          ) : (
            <>
              <Upload className="size-4" aria-hidden="true" />
              {t("materials.upload")}
            </>
          )}
        </Button>
      </div>

      <div className="mt-2 flex gap-2">
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleUrl();
          }}
          placeholder="https://..."
          aria-label={t("materials.urlLabel")}
          className="max-w-md"
        />
        <Button
          size="sm"
          variant="secondary"
          onClick={() => void handleUrl()}
          disabled={busy || !url.trim()}
        >
          <Link2 className="size-4" aria-hidden="true" />
          {t("materials.addUrl")}
        </Button>
      </div>

      {files === null ? null : files.length === 0 ? (
        <p className="mt-4 text-xs text-muted-foreground">{t("materials.empty")}</p>
      ) : (
        <ul className="mt-4 space-y-1">
          {files.map((file) => (
            <li
              key={file.id}
              className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-2 text-sm"
            >
              <FileText
                className="size-4 shrink-0 text-muted-foreground"
                aria-hidden="true"
              />
              <span className="min-w-0 flex-1 truncate">
                {file.filename ?? file.id}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}