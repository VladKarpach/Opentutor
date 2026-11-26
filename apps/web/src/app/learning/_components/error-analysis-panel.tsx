"use client";

/**
 * Error analysis — TZ FR-12.3, section 6.6.
 *
 * Section 6.6 splits the work: the counts are computed in code, the wording and
 * the recommendations come from the model. This panel shows both, plus the
 * weakest topics so the advice points somewhere concrete.
 *
 * A summary is written automatically every N answers; the button forces one now.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  getErrorAnalysis,
  regenerateErrorSummary,
  type ErrorAnalysis,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";

const ERROR_LABEL: Record<string, string> = {
  concept_confusion: "путаете понятия",
  missing_step: "пропускаете шаг",
  wrong_application: "ошибки в применении",
  incomplete: "неполные ответы",
};

function scoreTone(score: number): string {
  if (score >= 0.7) return "text-success";
  if (score >= 0.5) return "text-warning";
  return "text-destructive";
}

export function ErrorAnalysisPanel({ goalId }: { goalId: string }) {
  const [data, setData] = useState<ErrorAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await getErrorAnalysis(goalId));
    } catch (error) {
      toast.error("Не удалось загрузить анализ ошибок", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleRegenerate() {
    setBusy(true);
    try {
      await regenerateErrorSummary(goalId);
      await load();
      toast.success("Сводка обновлена");
    } catch (error) {
      toast.error("Не удалось обновить сводку", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="h-40 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  if (!data || (data.stats.window === 0 && data.summaries.length === 0)) {
    return (
      <p className="text-sm text-muted-foreground">
        Анализ появится после первых проверенных ответов.
      </p>
    );
  }

  const stats = data.stats;
  const latest = data.summaries[0];
  const weakTopics = (stats.by_topic ?? []).filter((t) => t.weak).slice(0, 5);
  const confusions = latest?.stats?.confusions ?? stats.confusions ?? [];

  return (
    <div className="space-y-5">
      {/* The model's own wording for the newest period. */}
      {latest?.summary_text && (
        <div className="rounded-xl bg-brand-muted px-4 py-3">
          <p className="text-sm text-foreground">{latest.summary_text}</p>
          {latest.recommendations.length > 0 && (
            <ul className="mt-2 space-y-1">
              {latest.recommendations.map((line) => (
                <li key={line} className="text-sm text-muted-foreground">
                  — {line}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Deterministic counts, always present even if the LLM wording failed. */}
      <div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(stats.counts ?? {})
            .filter(([, count]) => count > 0)
            .map(([type, count]) => (
              <Badge key={type} variant="secondary">
                {ERROR_LABEL[type] ?? type}: {count}
              </Badge>
            ))}
          {Object.values(stats.counts ?? {}).every((c) => c === 0) && (
            <span className="text-sm text-muted-foreground">
              Ошибок не зафиксировано — ответы без единой пометки об ошибке.
            </span>
          )}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Средняя оценка за последние {stats.window} ответов:{" "}
          {Math.round(stats.mean_score * 100)}%
        </p>
      </div>

      {/* FR-12.3: "often confuses X and Y" — the model names the pair. */}
      {confusions.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-sm font-semibold text-foreground">Путаете</h3>
          <ul className="space-y-1">
            {confusions.map((item) => (
              <li key={item} className="text-sm text-muted-foreground">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Weakest topics, so the advice has a target. */}
      {weakTopics.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-sm font-semibold text-foreground">Слабые темы</h3>
          <ul className="space-y-1.5">
            {weakTopics.map((topic) => (
              <li key={topic.topic_id}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm text-foreground">{topic.title}</span>
                  <span className={cn("text-sm font-medium", scoreTone(topic.mean_score))}>
                    {Math.round(topic.mean_score * 100)}%
                  </span>
                </div>
                {topic.dominant_error && (
                  <p className="text-xs text-muted-foreground">
                    чаще всего: {ERROR_LABEL[topic.dominant_error] ?? topic.dominant_error}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button size="xs" variant="outline" onClick={handleRegenerate} disabled={busy}>
          {busy ? "Считаем…" : "Обновить сводку"}
        </Button>
        {data.due && !busy && (
          <span className="text-xs text-muted-foreground">
            Накопилось достаточно ответов для новой сводки.
          </span>
        )}
        {data.summaries.length > 1 && (
          <span className="text-xs text-muted-foreground">
            Всего сводок: {data.summaries.length}
          </span>
        )}
      </div>
    </div>
  );
}