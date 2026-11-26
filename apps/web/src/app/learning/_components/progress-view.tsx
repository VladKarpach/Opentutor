"use client";

/**
 * Progress — TZ screen 9, FR-12.
 *
 * Topic map with the strength the repetition engine keeps, the readiness
 * forecast from section 6.7, and the error analysis from section 6.6. Also the
 * dependency graph (FR-5.5), including a visible warning when the graph is not
 * acyclic (FR-5.2).
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { ErrorAnalysisPanel } from "@/app/learning/_components/error-analysis-panel";
import { StrengthChart } from "@/app/learning/_components/strength-chart";
import {
  getProgress,
  getTopicGraph,
  type ErrorSummary,
  type Forecast,
  type TopicGraph,
  type TopicProgress,
  type TopicStatus,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<TopicStatus, string> = {
  not_started: "не начато",
  weak: "забыто",
  learning: "в работе",
  known: "освоено",
};

const STATUS_COLOR: Record<TopicStatus, string> = {
  not_started: "bg-muted",
  weak: "bg-destructive",
  learning: "bg-info",
  known: "bg-success",
};

const FORECAST_TONE: Record<Forecast["level"], string> = {
  high: "bg-success-muted text-success",
  medium: "bg-warning-muted text-warning",
  low: "bg-destructive/10 text-destructive",
};

const FORECAST_LABEL: Record<Forecast["level"], string> = {
  high: "Высокая",
  medium: "Средняя",
  low: "Низкая",
};

const ERROR_LABEL: Record<string, string> = {
  concept_confusion: "пу��аете понятия",
  missing_step: "пропускаете шаг",
  wrong_application: "ошибки в применении",
  incomplete: "неполные ответы",
};

export function ProgressView({ goalId }: { goalId: string }) {
  const [topics, setTopics] = useState<TopicProgress[]>([]);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [errorSummary, setErrorSummary] = useState<ErrorSummary | null>(null);
  const [graph, setGraph] = useState<TopicGraph | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [progress, graphData] = await Promise.all([
          getProgress(goalId),
          getTopicGraph(goalId),
        ]);
        if (cancelled) return;
        setTopics(progress.topics);
        setForecast(progress.forecast);
        setErrorSummary(progress.error_summary);
        setGraph(graphData);
      } catch (error) {
        if (!cancelled) {
          toast.error("Не удалось загрузить прогресс", {
            description: error instanceof Error ? error.message : undefined,
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [goalId]);

  if (loading) {
    return <div className="h-48 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  const orderIndex = new Map((graph?.order ?? []).map((id, i) => [id, i]));

  return (
    <div className="space-y-6">
      {/* FR-12.4 readiness forecast */}
      {forecast && (
        <section className="card-shadow rounded-2xl border border-border bg-card p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold tracking-tight text-foreground">
              Прогноз готовности
            </h2>
            <Badge className={FORECAST_TONE[forecast.level]}>
              {FORECAST_LABEL[forecast.level]} · {Math.round(forecast.probability * 100)}%
            </Badge>
          </div>
          <ul className="mt-3 space-y-1.5">
            {forecast.reasons.map((reason) => (
              <li key={reason} className="text-sm text-muted-foreground">
                {reason}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* FR-12.1 topic map */}
      <section className="card-shadow rounded-2xl border border-border bg-card p-6">
        <h2 className="text-lg font-semibold tracking-tight text-foreground">
          Карта тем
        </h2>
        {topics.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            Тем пока нет. Добавьте их, чтобы увидеть прогресс.
          </p>
        ) : (
          <ul className="mt-4 space-y-3">
            {[...topics]
              .sort((a, b) => (orderIndex.get(a.topic_id) ?? 0) - (orderIndex.get(b.topic_id) ?? 0))
              .map((topic) => (
                <li key={topic.topic_id}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-sm font-medium text-foreground">
                      {topic.title}
                    </span>
                    <span className="flex items-center gap-2">
                      <span
                        className={cn(
                          "h-2 w-2 rounded-full",
                          STATUS_COLOR[topic.status],
                        )}
                        aria-hidden="true"
                      />
                      <span className="text-xs text-muted-foreground">
                        {STATUS_LABEL[topic.status]} · {Math.round(topic.strength * 100)}%
                      </span>
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn("h-full rounded-full", STATUS_COLOR[topic.status])}
                      style={{ width: `${Math.round(topic.strength * 100)}%` }}
                    />
                  </div>
                  {topic.lapses > 0 && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      забыто раз: {topic.lapses}
                    </p>
                  )}
                </li>
              ))}
          </ul>
        )}
      </section>

      {/* FR-12.1 strength over time */}
      <section className="card-shadow rounded-2xl border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold tracking-tight text-foreground">
          Сила знания во времени
        </h2>
        <StrengthChart goalId={goalId} />
      </section>

      {/* FR-12.3 error analysis */}
      <section className="card-shadow rounded-2xl border border-border bg-card p-6">
        <h2 className="mb-4 text-lg font-semibold tracking-tight text-foreground">
          Анализ типичных ошибок
        </h2>
        <ErrorAnalysisPanel goalId={goalId} />
      </section>

      {/* Counts-only fallback, shown when the panel has nothing stored yet. */}
      {errorSummary && (errorSummary.window ?? 0) > 0 && (
        <section className="card-shadow rounded-2xl border border-border bg-card p-6">
          <h2 className="text-lg font-semibold tracking-tight text-foreground">
            Сводка по ошибкам
          </h2>
          <p className="mt-3 text-sm text-foreground">{errorSummary.observation}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(errorSummary.counts)
              .filter(([, count]) => count > 0)
              .map(([type, count]) => (
                <Badge key={type} variant="secondary">
                  {ERROR_LABEL[type] ?? type}: {count}
                </Badge>
              ))}
          </div>
        </section>
      )}

      {/* FR-5.2 cycle check surfaced rather than silently ignored */}
      {graph && graph.cycle && (
        <section className="rounded-2xl border border-destructive/40 bg-destructive/5 p-6">
          <h2 className="text-lg font-semibold tracking-tight text-destructive">
            В графе зависимостей есть цикл
          </h2>
          <p className="mt-2 text-sm text-foreground">
            {graph.cycle.join(" → ")}. Порядок изучения может не соблюдать все
            предпосылки — проверьте связи между темами.
          </p>
        </section>
      )}
    </div>
  );
}