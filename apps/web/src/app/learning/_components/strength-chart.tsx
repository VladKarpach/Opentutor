"use client";

/**
 * Strength over time — TZ FR-12.1, "график силы знания во времени".
 *
 * The default line is the goal-wide average; picking a topic switches to that
 * topic's own curve. The data comes from the points recorded as answers were
 * graded, so the chart is exact rather than reconstructed from the answer log.
 */

import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { getStrengthHistory, type StrengthHistory } from "@/lib/api/learning";

/** Strength below this is the "needs work" zone the plan treats as weak. */
const WEAK_THRESHOLD = 0.7;

function formatDay(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

export function StrengthChart({ goalId }: { goalId: string }) {
  const [data, setData] = useState<StrengthHistory | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getStrengthHistory(goalId)
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [goalId]);

  const series = useMemo(() => {
    if (!data) return { points: [], isOverall: true, title: "Все темы" };

    if (!selected) {
      return {
        points: data.overall.map((p) => ({
          label: formatDay(p.at),
          strength: p.strength,
          lapses: p.lapses,
        })),
        isOverall: true,
        title: "Все темы",
      };
    }

    const topic = data.by_topic.find((t) => t.topic_id === selected);
    return {
      points: (topic?.points ?? []).map((p) => ({
        label: formatDay(p.at),
        strength: p.strength,
        lapses: p.was_lapse ? 1 : 0,
      })),
      isOverall: false,
      title: topic?.title ?? "Тема",
    };
  }, [data, selected]);

  if (loading) {
    return <div className="h-56 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  if (error) {
    return (
      <p className="text-sm text-destructive">Не удалось загрузить график: {error}</p>
    );
  }

  if (!data || data.total_points === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        График появится после первых ответов — каждая проверенная работа добавляет
        точку.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="text-xs">
          {data.total_points} точек · {data.topic_count} тем
        </Badge>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          aria-label="Выбор темы для графика"
          className="h-8 rounded-md border border-border bg-background px-2 text-sm text-foreground"
        >
          <option value="">Все темы (среднее)</option>
          {data.by_topic.map((topic) => (
            <option key={topic.topic_id} value={topic.topic_id}>
              {topic.title}
            </option>
          ))}
        </select>
      </div>

      {series.points.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Для выбранной темы ещё нет записанных ответов.
        </p>
      ) : (
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={series.points}
              margin={{ top: 8, right: 8, bottom: 0, left: -18 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--border)"
                vertical={false}
              />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                stroke="var(--border)"
                minTickGap={16}
              />
              <YAxis
                domain={[0, 1]}
                ticks={[0, 0.25, 0.5, 0.75, 1]}
                tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                stroke="var(--border)"
                width={52}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--popover)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelFormatter={(label) => `Дата: ${label}`}
                formatter={(value, name) => [`${Math.round(Number(value) * 100)}%`, name]}
              />
              <ReferenceLine
                y={WEAK_THRESHOLD}
                stroke="var(--warning)"
                strokeDasharray="4 4"
                label={{ value: "нужно доработать", position: "insideBottomRight", fontSize: 10 }}
              />
              <Line
                type="monotone"
                dataKey="strength"
                stroke={series.isOverall ? "var(--brand)" : "var(--info)"}
                strokeWidth={2}
                dot={{ r: 2.5 }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        {series.title}: средняя сила знания по всем темам либо по выбранной теме.
        Пунктирная линия — порог {Math.round(WEAK_THRESHOLD * 100)}%, ниже него тема
        считается слабой. Резкие провалы — это «подзабыл тему» (FR-9.5).
      </p>
    </div>
  );
}