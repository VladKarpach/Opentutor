"use client";

/**
 * The code practice window.
 *
 * A coding task is not answered by typing a sentence, so it gets its own window
 * rather than a field inside the ordinary task form: room for the editor, the
 * task, the checks and the verdict at once.
 *
 * The order is deliberate. The learner's code is run in *their* browser first
 * (Pyodide, see python-runner.tsx). A failing check means the task is not done,
 * so the window asks for a fix and keeps the code — nothing is sent to the
 * server yet. Only a run where every check passed can be submitted; the server
 * then reads the code to judge whether it really answers the task and not just
 * the checks. If the server rejects it, the same "fix it and try again" loop
 * repeats. A rejected attempt never closes the window as solved.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { Check, Loader2, Play, Sparkles, X } from "lucide-react";
import { toast } from "sonner";

import { MarkdownRenderer } from "@/components/shared/markdown-renderer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  submitAnswer,
  type AnswerResult,
  type Question,
} from "@/lib/api/learning";
import { useT } from "@/lib/i18n-context";

import { usePythonRunner, type RunResult } from "./python-runner";

/** A graded solution counts as solved from here up. */
const PASS_SCORE = 0.7;

export interface CodeTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The coding task to work on. Null while one is being generated. */
  question: Question | null;
  /** Fired once the server accepted a passing solution. */
  onSolved?: (result: AnswerResult, question: Question) => void;
  /** Ask the parent for another task for the same topic. */
  onRequestNew?: () => void;
  /** True while the parent is generating a task. */
  generating?: boolean;
}

export function CodeTaskDialog({
  open,
  onOpenChange,
  question,
  onSolved,
  onRequestNew,
  generating = false,
}: CodeTaskDialogProps) {
  const t = useT();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-4 text-brand" aria-hidden="true" />
            {t("codeTask.title")}
          </DialogTitle>
          <DialogDescription>{t("codeTask.description")}</DialogDescription>
        </DialogHeader>

        {generating ? (
          <p
            className="flex items-center gap-2 py-10 text-sm text-muted-foreground"
            data-testid="code-task-generating"
          >
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            {t("codeTask.generating")}
          </p>
        ) : question ? (
          <CodeWorkspace
            key={question.id}
            question={question}
            onSolved={onSolved}
            onRequestNew={onRequestNew}
          />
        ) : (
          <p className="py-6 text-sm text-muted-foreground" data-testid="code-task-empty">
            {t("codeTask.empty")}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}

function CodeWorkspace({
  question,
  onSolved,
  onRequestNew,
}: {
  question: Question;
  onSolved?: (result: AnswerResult, question: Question) => void;
  onRequestNew?: () => void;
}) {
  const t = useT();
  const tests = useMemo(() => question.payload?.tests ?? [], [question]);
  const language = question.payload?.language ?? "python";
  const [code, setCode] = useState(question.payload?.starter_code ?? "");
  const [results, setResults] = useState<RunResult[] | null>(null);
  const [grade, setGrade] = useState<AnswerResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"edit" | "failed" | "passed">("edit");
  const { state: runnerState, runTests } = usePythonRunner();

  const hasTests = tests.length > 0;
  const runnerUnavailable = runnerState.status === "unavailable";

  // Editing invalidates the previous run: a verdict about old code must not let
  // new code be submitted without being run.
  const handleCodeChange = useCallback((value: string) => {
    setCode(value);
    setResults(null);
    setGrade(null);
    setPhase("edit");
  }, []);

  const handleCheck = useCallback(async () => {
    const outcome = await runTests(code, tests);
    if (!outcome) return;
    setResults(outcome.results);
    setGrade(null);
    const allPassed =
      outcome.results.length > 0 && outcome.results.every((result) => result.passed);
    setPhase(allPassed ? "passed" : "failed");
  }, [code, runTests, tests]);

  const handleSubmit = useCallback(async () => {
    setBusy(true);
    try {
      const graded = await submitAnswer(question.id, {
        text: code,
        value: null,
        run_results: results?.map(({ name, passed }) => ({ name, passed })),
      });
      setGrade(graded);
      if (graded.final_score >= PASS_SCORE) {
        setPhase("passed");
        onSolved?.(graded, question);
        toast.success(t("codeTask.solved"));
      } else {
        // The code ran but the review did not accept it as an answer to the task.
        setPhase("failed");
      }
    } catch (error) {
      toast.error(t("codeTask.submitFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }, [code, onSolved, question, results, t]);

  const allPassed = phase === "passed";
  const canSubmit =
    code.trim().length > 0 &&
    !busy &&
    !grade &&
    (!hasTests || runnerUnavailable || allPassed);
  const canCheck = hasTests && !runnerUnavailable && !busy && code.trim().length > 0;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-muted/30 px-4 py-3">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="text-[10px] uppercase">
            {language}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {t("codeTask.taskLabel")}
          </span>
        </div>
        <MarkdownRenderer
          content={question.text}
          className="text-sm leading-relaxed text-foreground"
        />
      </div>

      <CodeEditor
        value={code}
        onChange={handleCodeChange}
        disabled={busy}
        language={language}
        ariaLabel={t("codeTask.editorLabel")}
      />

      {hasTests && (
        <details className="rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs">
          <summary className="cursor-pointer text-muted-foreground">
            {t("codeTask.checks")} ({tests.length})
          </summary>
          <ul className="mt-1 space-y-0.5 font-mono text-muted-foreground">
            {tests.map((test) => (
              <li key={test.name}>
                {test.call} → {test.expected}
              </li>
            ))}
          </ul>
        </details>
      )}

      {results && (
        <ul className="space-y-1" data-testid="code-task-results">
          {results.map((result) => (
            <li
              key={result.name}
              className="flex items-start gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs"
            >
              {result.passed ? (
                <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-600" aria-hidden="true" />
              ) : (
                <X className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-hidden="true" />
              )}
              <span className="min-w-0 flex-1">
                <span className="text-foreground">{result.name}</span>
                <span className="ml-1 text-muted-foreground">{result.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {!hasTests && (
        <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {t("codeTask.noTests")}
        </p>
      )}
      {runnerUnavailable && hasTests && (
        <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {t("codeTask.runnerUnavailable")}
        </p>
      )}

      {!grade && phase === "failed" && (
        <p
          className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          data-testid="code-task-retry"
        >
          {t("codeTask.checksFailed")}
        </p>
      )}
      {!grade && phase === "passed" && (
        <p
          className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300"
          data-testid="code-task-passed"
        >
          {t("codeTask.checksPassed")}
        </p>
      )}

      {grade && (
        <GradePanel grade={grade} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        {!grade && hasTests && !runnerUnavailable && (
          <Button
            type="button"
            size="sm"
            onClick={() => void handleCheck()}
            disabled={!canCheck}
            data-testid="code-task-check"
          >
            {runnerState.status === "loading" ? (
              <>
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                {t("codeTask.loading")}
              </>
            ) : (
              <>
                <Play className="size-3.5" aria-hidden="true" />
                {t("codeTask.check")}
              </>
            )}
          </Button>
        )}

        {!grade && canSubmit && (
          <Button
            type="button"
            size="sm"
            variant={allPassed ? "default" : "outline"}
            onClick={() => void handleSubmit()}
            disabled={busy}
            data-testid="code-task-submit"
          >
            {busy ? (
              <>
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                {t("codeTask.submitting")}
              </>
            ) : (
              t("codeTask.submit")
            )}
          </Button>
        )}

        {grade && grade.final_score < PASS_SCORE && (
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setGrade(null);
              setPhase("failed");
            }}
            data-testid="code-task-fix"
          >
            {t("codeTask.retry")}
          </Button>
        )}

        {onRequestNew && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onRequestNew}
            disabled={busy}
            data-testid="code-task-new"
          >
            {t("codeTask.newTask")}
          </Button>
        )}
      </div>

      {!grade && hasTests && !runnerUnavailable && phase !== "passed" && (
        <p className="text-xs text-muted-foreground">{t("codeTask.unsolvedHint")}</p>
      )}
    </div>
  );
}

function GradePanel({ grade }: { grade: AnswerResult }) {
  const t = useT();
  const passed = grade.final_score >= PASS_SCORE;
  return (
    <div
      className="space-y-2 rounded-xl border border-border bg-card px-4 py-3"
      data-testid="code-task-grade"
    >
      <div className="flex items-center gap-3">
        <span className="text-2xl font-bold text-foreground">
          {Math.round(grade.final_score * 100)}
        </span>
        <Badge
          className={
            passed
              ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
              : "bg-destructive/10 text-destructive"
          }
        >
          {passed ? t("codeTask.solved") : t("codeTask.notSolved")}
        </Badge>
      </div>
      {grade.feedback && (
        <MarkdownRenderer
          content={grade.feedback}
          className="text-sm text-muted-foreground"
        />
      )}
      {grade.expected_answer && !passed && (
        <details className="rounded-lg border border-border bg-background px-3 py-2 text-xs">
          <summary className="cursor-pointer text-muted-foreground">
            {t("taskFormat.expected")}
          </summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] text-foreground">
            {grade.expected_answer}
          </pre>
        </details>
      )}
    </div>
  );
}

/**
 * A plain textarea with a line-number gutter and Tab that indents.
 *
 * Deliberately not a full editor component: the task is a single function most
 * of the time, and a heavy editor would cost more than it returns. Tab must not
 * move focus, which is the one thing a bare textarea gets wrong for code.
 */
function CodeEditor({
  value,
  onChange,
  disabled,
  language,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  language: string;
  ariaLabel: string;
}) {
  const gutterRef = useRef<HTMLPreElement>(null);
  const lineCount = value.length === 0 ? 1 : value.split("\n").length;
  const numbers = useMemo(
    () => Array.from({ length: lineCount }, (_, index) => index + 1).join("\n"),
    [lineCount],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== "Tab") return;
      event.preventDefault();
      const target = event.currentTarget;
      const { selectionStart, selectionEnd } = target;
      const next = `${value.slice(0, selectionStart)}    ${value.slice(selectionEnd)}`;
      onChange(next);
      requestAnimationFrame(() => {
        target.selectionStart = target.selectionEnd = selectionStart + 4;
      });
    },
    [onChange, value],
  );

  return (
    <div className="flex overflow-hidden rounded-lg border border-border bg-muted/30">
      <pre
        ref={gutterRef}
        aria-hidden="true"
        className="select-none overflow-hidden border-r border-border bg-muted/40 px-2 py-3 text-right font-mono text-xs leading-5 text-muted-foreground"
      >
        {numbers}
      </pre>
      <textarea
        className="min-h-[220px] w-full resize-y bg-transparent p-3 font-mono text-sm leading-5 text-foreground focus:outline-none disabled:opacity-50"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        onScroll={(event) => {
          if (gutterRef.current) {
            gutterRef.current.scrollTop = event.currentTarget.scrollTop;
          }
        }}
        placeholder={`# ${language}`}
        disabled={disabled}
        spellCheck={false}
        aria-label={ariaLabel}
        data-testid="code-task-editor"
      />
    </div>
  );
}
