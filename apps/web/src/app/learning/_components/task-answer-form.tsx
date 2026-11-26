"use client";

/**
 * The control that matches the task, and the answer it produces.
 *
 * One component per kind of reply rather than one form with branches, because the
 * kinds are genuinely different things: a number needs a tolerance the learner can
 * see, an ordering needs the steps to be movable, a matching needs two columns that
 * cannot be lined up. Putting all of that behind one `if` would produce a form that
 * is wrong for every task it does not fit.
 *
 * Every one of them reports the same shape, so `practice-panel` does not have to
 * know which is which: whatever control was shown, `onAnswer` receives a text for
 * the record and a structured `value` for grading.
 */

import { useCallback, useMemo, useRef, useState } from "react";

import { Camera, Check, GripVertical, Loader2, Play, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n-context";

import {
  RunButton,
  RunnerUnavailable,
  type RunResult,
  type TaskTest,
  usePythonRunner,
} from "./python-runner";

export type TaskFormat =
  | "text"
  | "code"
  | "number"
  | "single_choice"
  | "multi_choice"
  | "true_false"
  | "ordering"
  | "matching"
  | "photo";

export interface TaskPayload {
  options?: string[];
  items?: string[];
  left?: string[];
  right?: string[];
  unit?: string;
  hint_text?: string;
  tolerance?: number | null;
  language?: string;
  starter_code?: string;
  tests?: TaskTest[];
}

export interface AnswerShape {
  /** What is stored and reviewed. Never empty — the schema requires it. */
  text: string;
  /** What the grader compares. Null for prose, which needs no structured answer. */
  value: unknown;
  /** What the browser established by running the learner's own code. */
  runResults?: { name: string; passed: boolean }[];
  runOutput?: string;
  /** A photo of a handwritten solution. When present the answer is graded by
   * looking at the image rather than reading the text. */
  image?: string;
}

/**
 * Fill `{name}` slots in a translated string.
 *
 * Done here rather than by teaching the locale context about parameters: the
 * context's contract is a plain key lookup and three call sites do not justify
 * widening it. Unknown slots are left as they are, so a missing value shows up in
 * the interface instead of rendering as the word "undefined".
 */
function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) =>
    key in values ? values[key] : whole,
  );
}

/** The letter shown beside each option. Matches what the API stores. */
const OPTION_KEYS = "abcdefgh";

function optionKey(index: number): string {
  return OPTION_KEYS[index] ?? String(index);
}

export interface AnswerFormProps {
  format: TaskFormat;
  payload: TaskPayload;
  disabled?: boolean;
  onAnswer: (answer: AnswerShape) => void;
  /** Shown above the control so the learner knows what kind of reply is wanted. */
  submitLabel: string;
}

// ── photo of a handwritten solution ──────────────────────────────────────

function PhotoAnswer({
  disabled,
  onAnswer,
  submitLabel,
}: AnswerFormProps) {
  const t = useT();
  const [image, setImage] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    async (file: File) => {
      // Read as a data URL, which is what the server expects and what the model
      // can be sent directly. Compressed first: a phone photo is several
      // megabytes, and the model does not need that resolution to read
      // handwriting.
      setReading(true);
      try {
        const dataUrl = await compressImage(file);
        setImage(dataUrl);
      } finally {
        setReading(false);
      }
    },
    [],
  );

  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void handleFile(file);
          event.target.value = "";
        }}
        data-testid="answer-photo-input"
      />

      {image ? (
        <div className="space-y-2">
          <div className="relative overflow-hidden rounded-lg border border-border">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={image}
              alt={t("taskFormat.photoAlt")}
              className="max-h-64 w-full object-contain"
              data-testid="answer-photo-preview"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setImage(null)}
              disabled={disabled}
            >
              {t("taskFormat.photoRemove")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => inputRef.current?.click()}
              disabled={disabled}
            >
              {t("taskFormat.photoReplace")}
            </Button>
          </div>
          <SubmitRow
            disabled={disabled}
            onClick={() =>
              onAnswer({
                text: t("taskFormat.photoNote"),
                value: null,
                image,
              })
            }
            label={submitLabel}
          />
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => inputRef.current?.click()}
            disabled={disabled || reading}
            data-testid="answer-photo"
          >
            {reading ? (
              <>
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                {t("taskFormat.photoReading")}
              </>
            ) : (
              <>
                <Camera className="size-3.5" aria-hidden="true" />
                {t("taskFormat.photo")}
              </>
            )}
          </Button>
          <span className="text-xs text-muted-foreground">
            {t("taskFormat.photoHint")}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * Compress a photo to something the model can read and the API will accept.
 *
 * A phone camera produces several megabytes; handwriting is legible at a fraction
 * of that, and the API caps the request. Drawn on a canvas rather than resized by
 * CSS, so the stored image is the compressed one.
 */
function compressImage(file: File, maxDim = 1600, quality = 0.82): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        reject(new Error("no canvas"));
        return;
      }
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve(canvas.toDataURL("image/jpeg", quality));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("не удалось прочитать изображение"));
    };
    img.src = url;
  });
}

// ── prose ────────────────────────────────────────────────────────────────

function TextAnswer({
  disabled,
  onAnswer,
  submitLabel,
}: AnswerFormProps) {
  const t = useT();
  const [value, setValue] = useState("");

  return (
    <div className="space-y-2">
      <textarea
        className="min-h-[96px] w-full resize-y rounded-lg border border-border bg-background p-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={t("taskFormat.textPlaceholder")}
        disabled={disabled}
        aria-label={t("taskFormat.textAria")}
        data-testid="answer-text"
      />
      <SubmitRow
        disabled={disabled || value.trim().length === 0}
        onClick={() => onAnswer({ text: value.trim(), value: null })}
        label={submitLabel}
      />
    </div>
  );
}

// ── number ───────────────────────────────────────────────────────────────

function NumberAnswer({
  disabled,
  onAnswer,
  submitLabel,
  payload,
}: AnswerFormProps) {
  const t = useT();
  const [value, setValue] = useState("");

  const tolerance = payload.tolerance;
  // The tolerance is shown because a learner who does not know how precise to be
  // will round, and rounding is correct or incorrect depending on a number they
  // cannot see.
  const toleranceNote =
    tolerance && tolerance > 0
      ? fill(t("taskFormat.tolerance"), { value: String(tolerance) })
      : null;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="text"
          inputMode="decimal"
          className="w-40"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={payload.hint_text || t("taskFormat.numberPlaceholder")}
          disabled={disabled}
          aria-label={t("taskFormat.numberAria")}
          data-testid="answer-number"
        />
        {payload.unit && (
          <span className="text-sm text-muted-foreground">{payload.unit}</span>
        )}
      </div>
      {toleranceNote && (
        <p className="text-xs text-muted-foreground">{toleranceNote}</p>
      )}
      <SubmitRow
        disabled={disabled || value.trim().length === 0}
        onClick={() => onAnswer({ text: value.trim(), value: value.trim() })}
        label={submitLabel}
      />
    </div>
  );
}

// ── one of several ───────────────────────────────────────────────────────

function SingleChoiceAnswer({
  disabled,
  onAnswer,
  submitLabel,
  payload,
}: AnswerFormProps) {
  const t = useT();
  const options = payload.options ?? [];
  const [picked, setPicked] = useState<number | null>(null);

  return (
    <div className="space-y-2">
      <div role="radiogroup" aria-label={t("taskFormat.choiceAria")} className="space-y-1">
        {options.map((option, index) => (
          <label
            key={`${index}-${option}`}
            className="flex cursor-pointer items-start gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted/40 has-[:checked]:border-primary has-[:checked]:bg-muted/60"
          >
            <input
              type="radio"
              name="task-single-choice"
              className="mt-0.5"
              checked={picked === index}
              onChange={() => setPicked(index)}
              disabled={disabled}
              data-testid={`answer-option-${index}`}
            />
            <span className="text-muted-foreground">{optionKey(index)}.</span>
            <span className="text-foreground">{option}</span>
          </label>
        ))}
      </div>
      <SubmitRow
        disabled={disabled || picked === null}
        onClick={() =>
          picked !== null
            ? onAnswer({ text: options[picked] ?? "", value: [picked] })
            : undefined
        }
        label={submitLabel}
      />
    </div>
  );
}

// ── several of several ───────────────────────────────────────────────────

function MultiChoiceAnswer({
  disabled,
  onAnswer,
  submitLabel,
  payload,
}: AnswerFormProps) {
  const t = useT();
  const options = payload.options ?? [];
  const [picked, setPicked] = useState<Set<number>>(new Set());

  const toggle = (index: number) => {
    setPicked((previous) => {
      const next = new Set(previous);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const chosen = useMemo(() => [...picked].sort((a, b) => a - b), [picked]);

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">{t("taskFormat.multiHint")}</p>
      <div className="space-y-1">
        {options.map((option, index) => (
          <label
            key={`${index}-${option}`}
            className="flex cursor-pointer items-start gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted/40 has-[:checked]:border-primary has-[:checked]:bg-muted/60"
          >
            <input
              type="checkbox"
              className="mt-0.5"
              checked={picked.has(index)}
              onChange={() => toggle(index)}
              disabled={disabled}
              data-testid={`answer-option-${index}`}
            />
            <span className="text-muted-foreground">{optionKey(index)}.</span>
            <span className="text-foreground">{option}</span>
          </label>
        ))}
      </div>
      <SubmitRow
        disabled={disabled || chosen.length === 0}
        onClick={() =>
          onAnswer({
            text: chosen.map((index) => options[index] ?? "").join("; "),
            value: chosen,
          })
        }
        label={submitLabel}
      />
    </div>
  );
}

// ── true or false ────────────────────────────────────────────────────────

function TrueFalseAnswer({
  disabled,
  onAnswer,
  submitLabel,
}: AnswerFormProps) {
  const t = useT();
  const [answer, setAnswer] = useState<boolean | null>(null);

  return (
    <div className="space-y-2">
      <div role="radiogroup" aria-label={t("taskFormat.trueFalseAria")} className="flex gap-2">
        {[true, false].map((value) => (
          <Button
            key={String(value)}
            type="button"
            size="sm"
            variant={answer === value ? "default" : "outline"}
            onClick={() => setAnswer(value)}
            disabled={disabled}
            data-testid={`answer-tf-${value ? "true" : "false"}`}
          >
            {value ? t("taskFormat.true") : t("taskFormat.false")}
          </Button>
        ))}
      </div>
      <SubmitRow
        disabled={disabled || answer === null}
        onClick={() =>
          answer === null
            ? undefined
            : onAnswer({
                text: answer ? t("taskFormat.true") : t("taskFormat.false"),
                value: answer,
              })
        }
        label={submitLabel}
      />
    </div>
  );
}

// ── put these in order ───────────────────────────────────────────────────

function OrderingAnswer({
  disabled,
  onAnswer,
  submitLabel,
  payload,
}: AnswerFormProps) {
  const t = useT();
  const items = useMemo(() => payload.items ?? [], [payload]);
  const [order, setOrder] = useState<number[]>(items.map((_, index) => index));

  const move = (from: number, to: number) => {
    if (to < 0 || to >= order.length) return;
    setOrder((previous) => {
      const next = [...previous];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  };

  return (
    <div className="space-y-2">
      <ol className="space-y-1" data-testid="answer-ordering">
        {order.map((itemIndex, position) => (
          <li
            key={items[itemIndex]}
            className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm"
          >
            <GripVertical className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="w-5 shrink-0 text-muted-foreground">{position + 1}.</span>
            <span className="flex-1 text-foreground">{items[itemIndex]}</span>
            <Button
              type="button"
              size="xs"
              variant="ghost"
              disabled={disabled || position === 0}
              onClick={() => move(position, position - 1)}
              aria-label={t("taskFormat.moveUp")}
              data-testid="order-move-up"
            >
              ↑
            </Button>
            <Button
              type="button"
              size="xs"
              variant="ghost"
              disabled={disabled || position === order.length - 1}
              onClick={() => move(position, position + 1)}
              aria-label={t("taskFormat.moveDown")}
              data-testid="order-move-down"
            >
              ↓
            </Button>
          </li>
        ))}
      </ol>
      <SubmitRow
        disabled={disabled || order.length === 0}
        onClick={() =>
          onAnswer({
            text: order.map((index) => items[index] ?? "").join(" → "),
            value: order,
          })
        }
        label={submitLabel}
      />
    </div>
  );
}

// ── match the columns ────────────────────────────────────────────────────

function MatchingAnswer({
  disabled,
  onAnswer,
  submitLabel,
  payload,
}: AnswerFormProps) {
  const t = useT();
  const left = useMemo(() => payload.left ?? [], [payload]);
  const right = useMemo(() => payload.right ?? [], [payload]);
  const [joined, setJoined] = useState<Record<number, string>>({});

  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-2" data-testid="answer-matching">
        <div className="space-y-1">
          {left.map((item, index) => (
            <div
              key={item}
              className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm"
            >
              <span className="flex-1 text-foreground">{item}</span>
              <select
                className="max-w-[60%] rounded border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                value={joined[index] ?? ""}
                onChange={(event) =>
                  setJoined((previous) => ({ ...previous, [index]: event.target.value }))
                }
                disabled={disabled}
                aria-label={fill(t("taskFormat.matchFor"), { value: item })}
                data-testid={`answer-match-${index}`}
              >
                <option value="">—</option>
                {right.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      </div>
      <SubmitRow
        disabled={disabled || Object.keys(joined).length < left.length}
        onClick={() => {
          const pairs: Record<string, string> = {};
          for (const [index, value] of Object.entries(joined)) {
            const key = left[Number(index)];
            if (key && value) pairs[key] = value;
          }
          onAnswer({
            text: Object.entries(pairs)
              .map(([key, value]) => `${key} — ${value}`)
              .join("; "),
            value: pairs,
          });
        }}
        label={submitLabel}
      />
    </div>
  );
}

// ── write code, run it, check it ─────────────────────────────────────────

function CodeAnswer({ disabled, onAnswer, submitLabel, payload }: AnswerFormProps) {
  const t = useT();
  const tests = useMemo(() => payload.tests ?? [], [payload]);
  const [code, setCode] = useState(payload.starter_code ?? "");
  const [results, setResults] = useState<RunResult[] | null>(null);
  const [output, setOutput] = useState("");
  const { state, runTests } = usePythonRunner();

  // Tab must indent rather than leave the field: a code editor that loses focus on
  // Tab is unusable for anyone typing by touch.
  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Tab") return;
    event.preventDefault();
    const target = event.currentTarget;
    const { selectionStart, selectionEnd } = target;
    const next = `${code.slice(0, selectionStart)}    ${code.slice(selectionEnd)}`;
    setCode(next);
    requestAnimationFrame(() => {
      target.selectionStart = target.selectionEnd = selectionStart + 4;
    });
  }, [code]);

  const handleRun = useCallback(async () => {
    const outcome = await runTests(code, tests);
    if (!outcome) return;
    setResults(outcome.results);
    setOutput(outcome.output);
  }, [code, runTests, tests]);

  const passed = results?.filter((result) => result.passed).length ?? 0;

  return (
    <div className="space-y-2">
      <textarea
        className="min-h-[200px] w-full resize-y rounded-lg border border-border bg-muted/30 p-3 font-mono text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={t("taskFormat.codePlaceholder")}
        disabled={disabled}
        spellCheck={false}
        aria-label={t("taskFormat.codeAria")}
        data-testid="answer-code"
      />

      <div className="flex flex-wrap items-center gap-2">
        <RunButton
          state={state}
          onRun={() => void handleRun()}
          disabled={disabled || tests.length === 0 || code.trim().length === 0}
        />
        {state.status === "unavailable" && <RunnerUnavailable reason={state.reason} />}
        {results && (
          <span className="text-xs text-muted-foreground" data-testid="run-summary">
            {fill(t("taskFormat.runSummary"), {
              passed: String(passed),
              total: String(results.length),
            })}
          </span>
        )}
      </div>

      {results && results.length > 0 && (
        <ul className="space-y-1" data-testid="run-results">
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

      {tests.length > 0 && (
        <details className="rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs">
          <summary className="cursor-pointer text-muted-foreground">
            {fill(t("taskFormat.checks"), { count: String(tests.length) })}
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

      <SubmitRow
        disabled={disabled || code.trim().length === 0}
        onClick={() =>
          onAnswer({
            text: code,
            value: null,
            runResults: results?.map(({ name, passed: ok }) => ({ name, passed: ok })),
            runOutput: output,
          })
        }
        label={submitLabel}
        icon={<Play className="size-3.5" aria-hidden="true" />}
      />
    </div>
  );
}

// ── the switch ───────────────────────────────────────────────────────────

function SubmitRow({
  disabled,
  onClick,
  label,
  icon,
}: {
  disabled?: boolean;
  onClick: () => void;
  label: string;
  icon?: React.ReactNode;
}) {
  return (
    <Button type="button" size="sm" onClick={onClick} disabled={disabled}>
      {icon}
      {label}
    </Button>
  );
}

const FORMS: Record<TaskFormat, (props: AnswerFormProps) => React.ReactElement> = {
  text: TextAnswer,
  code: CodeAnswer,
  number: NumberAnswer,
  single_choice: SingleChoiceAnswer,
  multi_choice: MultiChoiceAnswer,
  true_false: TrueFalseAnswer,
  ordering: OrderingAnswer,
  matching: MatchingAnswer,
  // A photo is not a format of its own but an alternative way to answer the prose
  // and code formats, so it is offered alongside them rather than instead of them.
  photo: PhotoAnswer,
};

export function TaskAnswerForm(props: AnswerFormProps) {
  const Form = FORMS[props.format] ?? TextAnswer;
  return <Form {...props} />;
}