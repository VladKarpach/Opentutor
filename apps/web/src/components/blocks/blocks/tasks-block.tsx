"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Download, FileText, Loader2, Trash2, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  deleteTaskFile,
  downloadTaskFile,
  listTaskFiles,
  listTaskSubmissions,
  submitTaskAnswer,
  uploadTaskFile,
  type TaskFile,
  type TaskSubmission,
} from "@/lib/api/user-content";
import { useT, useTF } from "@/lib/i18n-context";
import { useWorkspaceStore } from "@/store/workspace";
import type { BlockComponentProps } from "@/lib/block-system/registry";

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return "";
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function verdictColor(verdict: string): string {
  switch (verdict) {
    case "correct":
      return "text-success border-success/20 bg-success/10";
    case "partial":
      return "text-warning border-warning/20 bg-warning/10";
    case "incorrect":
      return "text-destructive border-destructive/20 bg-destructive/10";
    default:
      return "text-muted-foreground";
  }
}

function verdictLabel(t: ReturnType<typeof useT>, verdict: string): string {
  switch (verdict) {
    case "correct":
      return t("tasks.verdictCorrect");
    case "partial":
      return t("tasks.verdictPartial");
    case "incorrect":
      return t("tasks.verdictIncorrect");
    default:
      return verdict;
  }
}

/** The remedial (similar) task generated after a partial/incorrect verdict. */
function RemedialTaskCard({
  remedial,
  taskFileId,
  loadSubmissions,
  blockId,
  t,
}: {
  remedial: {
    question: string;
    correct_answer?: string;
    explanation?: string;
    core_concept?: string;
  };
  taskFileId: string;
  loadSubmissions: () => Promise<void>;
  blockId: string;
  t: ReturnType<typeof useT>;
}) {
  const tf = useTF();
  const updateBlockConfig = useWorkspaceStore((s) => s.updateBlockConfig);
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!answer.trim()) return;
      setSubmitting(true);
      try {
        const result = await submitTaskAnswer(taskFileId, {
          user_answer: answer,
        });
        setAnswer("");
        await loadSubmissions();
        if (result.block_completed && blockId) {
          await updateBlockConfig(blockId, { completed: true });
        }
        toast(result.feedback ?? t("tasks.remedialChecked"), {
          duration: 5000,
        });
      } catch (error) {
        toast.error(t("tasks.submitFailed"), {
          description: error instanceof Error ? error.message : undefined,
        });
      } finally {
        setSubmitting(false);
      }
    },
    [answer, taskFileId, loadSubmissions, blockId, updateBlockConfig, t]
  );

  return (
    <div className="mt-3 p-4 rounded-lg border border-warning/30 bg-warning/50">
      <h4 className="text-sm font-semibold text-warning mb-2 flex items-center gap-1">
        <AlertTriangle className="size-4" />
        {t("tasks.remedialTitle")}
      </h4>
      <p className="text-sm text-foreground mb-3">{remedial.question}</p>
      {remedial.core_concept && (
        <p className="text-xs text-muted-foreground mb-3">
          {tf("tasks.remedialConcept", { concept: remedial.core_concept })}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <textarea
          placeholder={t("tasks.remedialAnswerPlaceholder")}
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          disabled={submitting}
          className="min-h-[100px] w-full border border-input bg-input/40 rounded-lg px-3 py-2 text-sm text-foreground disabled:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Button type="submit" disabled={submitting || !answer.trim()}>
          {submitting ? t("tasks.checking") : t("tasks.checkRemedial")}
        </Button>
      </form>
    </div>
  );
}

/** One uploaded task file with its answer form, submissions and remedial task. */
function TaskFileCard({
  taskFile,
  blockId,
  onDelete,
}: {
  taskFile: TaskFile;
  blockId: string;
  onDelete: () => void;
}) {
  const t = useT();
  const updateBlockConfig = useWorkspaceStore((s) => s.updateBlockConfig);
  const [submissions, setSubmissions] = useState<TaskSubmission[]>([]);
  const [submissionsLoaded, setSubmissionsLoaded] = useState(false);
  const [answer, setAnswer] = useState("");
  const [grading, setGrading] = useState(false);

  const latest = submissions[0];

  const loadSubmissions = useCallback(async () => {
    if (submissionsLoaded) return;
    setSubmissionsLoaded(true);
    try {
      const data = await listTaskSubmissions(taskFile.id);
      setSubmissions(data);
    } catch {
      setSubmissions([]);
    }
  }, [taskFile.id, submissionsLoaded]);

  const handleDownload = useCallback(async () => {
    try {
      const { blob, fileName } = await downloadTaskFile(taskFile.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName ?? taskFile.filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(t("tasks.downloadFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }, [taskFile.id, taskFile.filename, t]);

  const handleDelete = useCallback(async () => {
    try {
      await deleteTaskFile(taskFile.id);
      onDelete();
    } catch (error) {
      toast.error(t("tasks.deleteFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }, [taskFile.id, t, onDelete]);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!answer.trim()) return;
    setGrading(true);
    try {
      const result = await submitTaskAnswer(taskFile.id, {
        user_answer: answer,
      });
      setAnswer("");
      await loadSubmissions();
      if (result.block_completed && blockId) {
        await updateBlockConfig(blockId, { completed: true });
      }
      toast(
        result.verdict === "correct"
          ? t("tasks.submissionCorrect")
          : result.verdict === "partial"
            ? t("tasks.submissionPartial")
            : t("tasks.submissionIncorrect"),
        {
          description: result.feedback,
          duration: 6000,
        }
      );
    } catch (error) {
      toast.error(t("tasks.submitFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setGrading(false);
    }
  }, [answer, taskFile.id, loadSubmissions, blockId, updateBlockConfig, t]);

  return (
    <div className="flex min-h-0 flex-col rounded-2xl border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-border/60 p-4">
        <div className="flex items-center gap-2">
          <FileText className="size-4 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm font-medium text-foreground truncate" title={taskFile.filename}>
            {taskFile.filename}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={handleDownload}
            aria-label={t("tasks.download")}
            className="text-muted-foreground hover:text-brand transition-colors p-1"
          >
            <Download className="size-4" />
          </button>
          <button
            type="button"
            onClick={handleDelete}
            aria-label={t("tasks.remove")}
            className="text-muted-foreground hover:text-destructive transition-colors p-1"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>

      <div className="p-4">
        {latest && latest.block_completed ? (
          <div className="flex items-center gap-2 mb-3 p-3 rounded-lg border border-success/30 bg-success/10 text-success">
            <Check className="size-4" />
            <span className="text-sm font-medium">{t("tasks.blockCompleted")}</span>
          </div>
        ) : null}

        {/* Answer form */}
        <form onSubmit={handleSubmit} className="mb-4 flex flex-col gap-3">
          <div className="relative">
            <textarea
              placeholder={t("tasks.answerPlaceholder")}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              disabled={grading}
              className="min-h-[120px] w-full border border-input bg-input/40 rounded-lg px-3 py-2 text-sm text-foreground disabled:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            {grading && (
              <Loader2
                className="absolute right-3 top-3 size-4 text-muted-foreground animate-spin"
                aria-hidden="true"
              />
            )}
          </div>
          <Button type="submit" disabled={grading || !answer.trim()}>
            {grading ? t("tasks.checking") : t("tasks.checkAnswer")}
          </Button>
        </form>

        {/* Submissions history */}
        {submissionsLoaded && submissions.length > 0 && (
          <div className="mb-4">
            <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
              {t("tasks.attempts")}
            </h4>
            <ul className="space-y-2">
              {submissions.map((s) => (
                <li
                  key={s.id}
                  className={`p-3 rounded-lg border border-border/50 ${verdictColor(s.verdict)}`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-xs font-medium">{verdictLabel(t, s.verdict)}</span>
                    <span className="text-[10px] text-muted-foreground">
                      {s.score}/100
                    </span>
                  </div>
                  {s.feedback && (
                    <p className="text-sm text-foreground">{s.feedback}</p>
                  )}
                  {s.remedial_task && !s.block_completed && (
                    <RemedialTaskCard
                      remedial={s.remedial_task}
                      taskFileId={taskFile.id}
                      loadSubmissions={loadSubmissions}
                      blockId={blockId}
                      t={t}
                    />
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default function TasksBlock({ courseId, blockId }: BlockComponentProps) {
  const t = useT();
  const [files, setFiles] = useState<TaskFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState<File | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await listTaskFiles(courseId);
      setFiles(data);
    } catch {
      setFiles([]);
    }
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleUpload = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    try {
      await uploadTaskFile(courseId, file);
      setFile(null);
      void load();
    } catch (error) {
      toast.error(t("tasks.uploadFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setUploading(false);
    }
  }, [courseId, file, load, t]);

  return (
    <section className="card-shadow flex min-h-0 flex-col rounded-2xl border border-border bg-card p-5">
      <header className="flex items-center justify-between gap-3 border-b border-border/60 pb-3">
        <div className="flex items-center gap-2">
          <FileText className="size-4 text-muted-foreground" aria-hidden="true" />
          <h3 className="text-base font-semibold tracking-tight text-foreground">{t("tasks.title")}</h3>
        </div>
        <p className="text-xs text-muted-foreground">{t("tasks.hint")}</p>
      </header>

      {/* Upload form */}
      <form onSubmit={handleUpload} className="mb-4 flex flex-col gap-3 border-b border-border/60 pb-3">
        <div className="flex items-center gap-3">
          <Input
            type="file"
            accept=".pdf,.doc,.docx,.txt,.md,.html,.zip"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setFile(f);
            }}
            disabled={uploading}
            className="flex-1"
          />
          {file && (
            <span className="text-xs text-muted-foreground truncate" title={file.name}>
              {file.name}
            </span>
          )}
          <Button type="submit" disabled={uploading || !file}>
            {uploading ? t("tasks.uploading") : t("tasks.upload")}
          </Button>
        </div>
      </form>

      {/* File list */}
      <div className="overflow-y-auto min-h-0">
        {files.length === 0 ? (
          <div className="flex h-48 flex-col items-center justify-center gap-3 p-8 text-center text-muted-foreground">
            <p>{t("tasks.empty")}</p>
          </div>
        ) : (
          <ul className="space-y-2 py-1">
            {files.map((taskFile) => (
              <TaskFileCard
                key={taskFile.id}
                taskFile={taskFile}
                blockId={blockId}
                onDelete={() => void load()}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
