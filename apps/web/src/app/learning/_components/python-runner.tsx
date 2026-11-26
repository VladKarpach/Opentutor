"use client";

/**
 * Running the learner's Python in their own browser.
 *
 * The code is executed here, on their machine, through Pyodide — CPython compiled
 * to WebAssembly. Nothing they write is sent anywhere to be run, which is why this
 * exists: server-side execution is switched off in this project and is not going to
 * be switched on, so the alternative to a real run would be a model reading the
 * code and guessing.
 *
 * What that buys: a wrong answer fails where the learner can see it, with the real
 * output, in about a second. What it costs: a one-off download of a few megabytes
 * the first time, and Python only. Both are said out loud rather than hidden.
 *
 * The trust boundary, stated plainly because it is not where one might assume.
 * This is *not* a security boundary and cannot be made into one: the code runs on
 * the learner's own machine, so anyone determined can send results that do not
 * match what they actually ran. Two things follow, and both are deliberate.
 *
 * * The reported pass/fail is used, because for an ordinary learner it is right,
 *   and a run that never happens is worth much less than a run that does.
 * * The code is reviewed by a model on the server as well, so faking the run does
 *   not also fake the answer. The two checks fail independently, which is the only
 *   property that actually holds here.
 *
 * A browser without WebAssembly, a blocked CDN, or an offline machine gets no
 * runner. The task then degrades to checking without running, which works and says
 * so.
 */

import { useCallback, useRef, useState } from "react";

import { Loader2, Play, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n-context";

/** Where Pyodide is fetched from. Overridable, so it can be vendored for offline use. */
export const PYODIDE_CDN =
  process.env.NEXT_PUBLIC_PYODIDE_URL ??
  "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";

/**
 * One check as the task describes it. `expected` is here on purpose: it is the
 * specification of the function, not the answer to the task, and the learner is
 * shown it either way.
 */
export interface TaskTest {
  name: string;
  call: string;
  expected: string;
}

export interface RunResult {
  name: string;
  passed: boolean;
  /** What the check produced or why it failed. Shown to the learner only. */
  detail: string;
}

interface PyodideApi {
  runPythonAsync(code: string): Promise<unknown>;
}

export type RunnerState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready" }
  | { status: "unavailable"; reason: string };

export interface RunOutcome {
  results: RunResult[];
  output: string;
}

/**
 * Compare what a check produced with what it should produce.
 *
 * Textual, with one concession to arithmetic: `3` and `3.0` are the same number.
 * Everything else is compared as written, because a learner looking at
 * `0.30000000000000004` next to `0.3` learns something, and a silent "equal"
 * teaches them that the difference was ignored.
 */
function compare(got: unknown, expected: string): boolean {
  if (got === null || got === undefined) return false;
  const want = String(expected).trim();
  const have = String(got).trim();
  if (have === want) return true;

  const haveNumber = Number(have.replace(",", "."));
  const wantNumber = Number(want.replace(",", "."));
  const bothNumeric =
    Number.isFinite(haveNumber) && Number.isFinite(wantNumber) && have !== "" && want !== "";
  if (bothNumeric) return Math.abs(haveNumber - wantNumber) < 1e-9;

  return have.toLowerCase() === want.toLowerCase();
}

export function usePythonRunner(): {
  state: RunnerState;
  runTests: (code: string, tests: TaskTest[]) => Promise<RunOutcome | null>;
} {
  const [state, setState] = useState<RunnerState>({ status: "idle" });
  const apiRef = useRef<PyodideApi | null>(null);
  const loadingRef = useRef<Promise<PyodideApi | null> | null>(null);

  // Loaded once, on demand. A second press while it is still downloading waits for
  // the same download instead of starting another.
  const ensure = useCallback(async (): Promise<PyodideApi | null> => {
    if (apiRef.current) return apiRef.current;
    if (loadingRef.current) return loadingRef.current;

    setState({ status: "loading" });
    loadingRef.current = (async () => {
      try {
        const pyodide = (await import(
          /* webpackIgnore: true */ `${PYODIDE_CDN}pyodide.mjs`
        )) as {
          loadPyodide: (options: { indexURL: string }) => Promise<PyodideApi>;
        };
        const api = await pyodide.loadPyodide({ indexURL: PYODIDE_CDN });
        apiRef.current = api;
        setState({ status: "ready" });
        return api;
      } catch (error) {
        // Offline, blocked, or no WebAssembly. Said plainly, because the learner is
        // about to press "check" and needs to know why nothing will happen.
        setState({
          status: "unavailable",
          reason:
            error instanceof Error
              ? error.message
              : "не удалось загрузить среду выполнения",
        });
        return null;
      } finally {
        loadingRef.current = null;
      }
    })();

    return loadingRef.current;
  }, []);

  const runTests = useCallback(
    async (code: string, tests: TaskTest[]): Promise<RunOutcome | null> => {
      const api = await ensure();
      if (!api) return null;

      const results: RunResult[] = [];
      const lines: string[] = [];

      for (const test of tests) {
        try {
          // Every check is a separate execution. A check that hangs or kills the
          // interpreter must not take the others with it, nor leave the runner
          // unusable for the next attempt.
          await api.runPythonAsync(
            [
              "__opentutor_value__ = None",
              code,
              `__opentutor_value__ = (${test.call})`,
            ].join("\n"),
          );
          const value = await api.runPythonAsync("__opentutor_value__");
          const passed = compare(value, test.expected);
          results.push({
            name: test.name,
            passed,
            detail: passed ? `→ ${String(value)}` : `получено ${String(value)}, ожидалось ${test.expected}`,
          });
          lines.push(`${test.name}: ${String(value)}`);
        } catch (error) {
          const detail =
            error instanceof Error ? error.message.split("\n")[0] : String(error);
          results.push({ name: test.name, passed: false, detail: `ошибка — ${detail}` });
          lines.push(`${test.name}: ошибка — ${detail}`);
        }
      }

      return { results, output: lines.join("\n") };
    },
    [ensure],
  );

  return { state, runTests };
}

/** Shown in place of the run button when there is no runner and no way to get one. */
export function RunnerUnavailable({ reason }: { reason: string }) {
  const t = useT();
  return (
    <p
      className="flex items-start gap-2 rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground"
      data-testid="runner-unavailable"
    >
      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      <span>
        {t("taskFormat.runUnavailable")}{" "}
        <span className="opacity-70">({reason})</span>
      </span>
    </p>
  );
}

/** The button that starts loading Pyodide, and says so while it does. */
export function RunButton({
  state,
  onRun,
  disabled,
}: {
  state: RunnerState;
  onRun: () => void;
  disabled?: boolean;
}) {
  const t = useT();
  const loading = state.status === "loading";

  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={onRun}
      disabled={disabled || loading}
      data-testid="task-run"
    >
      {loading ? (
        <>
          <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
          {t("taskFormat.runLoading")}
        </>
      ) : (
        <>
          <Play className="size-3.5" aria-hidden="true" />
          {t("taskFormat.run")}
        </>
      )}
    </Button>
  );
}