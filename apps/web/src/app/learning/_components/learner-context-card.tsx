"use client";

/**
 * What the system already knows about this learner — TZ section 9.
 *
 * Shown on the goal page on purpose: the same context is handed to the model
 * when it splits the goal into subtopics and writes tasks, so the learner can
 * see exactly why the plan looks the way it does.
 */

import { useEffect, useState } from "react";

import { MarkdownRenderer } from "@/components/shared/markdown-renderer";
import { Badge } from "@/components/ui/badge";
import { getLearnerContext, type LearnerContext } from "@/lib/api/learning";

const ERROR_LABEL: Record<string, string> = {
  concept_confusion: "путаете понятия",
  missing_step: "пропускаете шаг",
  wrong_application: "ошибки в применении",
  incomplete: "неполные ответы",
};

function Group({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {items.map((item) => (
          <Badge key={item} variant="secondary" className="text-xs">
            {item}
          </Badge>
        ))}
      </div>
    </div>
  );
}

/**
 * What the system already knows about this learner — TZ section 9.
 *
 * Global, not per goal: it aggregates every goal the learner has, because that
 * is the whole point. The same context is handed to the model when it splits a
 * goal into subtopics and writes tasks, and the planner shortens topics it finds
 * already covered elsewhere — so the learner should be able to see the same
 * picture the model gets, from anywhere rather than only inside one goal.
 */
export function LearnerContextCard({
  goalId,
  courseId,
  compact = false,
}: {
  goalId?: string;
  courseId?: string;
  /** Tighter layout for the goal page, where space is shared with the plan. */
  compact?: boolean;
}) {
  const [ctx, setCtx] = useState<LearnerContext | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getLearnerContext({ goalId, courseId })
      .then((data) => {
        if (!cancelled) setCtx(data);
      })
      .catch(() => {
        // The panel is informational; a failure must not break the page.
        if (!cancelled) setCtx(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [goalId, courseId]);

  if (loading) {
    return <div className="h-24 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  // A brand-new learner: nothing to show, and nothing to tell the model.
  if (!ctx || ctx.answers_total === 0) {
    return (
      <section className="card-shadow rounded-2xl border border-border bg-card p-5">
        <h2 className="text-base font-semibold tracking-tight text-foreground">
          Что уже известно о вас
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Пока это первая цель — накопления нет. Со временем здесь появятся ваши
          сильные и слабые темы, а план и задания будут учитывать их.
        </p>
      </section>
    );
  }

  const errors = Object.entries(ctx.error_profile ?? {})
    .filter(([, n]) => n > 0)
    // Most frequent first. The object came over as whatever order the database
    // produced, so "the most common mistake" was not actually on top.
    .sort((a, b) => b[1] - a[1]);

  return (
    <section className="card-shadow space-y-4 rounded-2xl border border-border bg-card p-5">
      <div>
        <h2 className="text-base font-semibold tracking-tight text-foreground">
          Что уже известно о вас
        </h2>
        <p className="text-xs text-muted-foreground">
          Общая картина по всем целям и курсам. Из неё строится план: уже освоенное
          планируется коротким повторением, а не с нуля.
        </p>
      </div>

      {/* The description the planner itself works from, in words. The groups below
          are the same content split into lists; this is what a person reads first
          when they open the profile. */}
      {ctx.summary ? (
        <div
          className="rounded-xl bg-muted/40 p-3 text-sm leading-relaxed text-foreground"
          data-testid="learner-summary"
        >
          <MarkdownRenderer content={ctx.summary} />
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Badge variant="outline" className="text-xs">
          целей: {ctx.goals_seen}
        </Badge>
        <Badge variant="outline" className="text-xs">
          тем пройдено: {ctx.topics_touched}
        </Badge>
        <Badge variant="outline" className="text-xs">
          ответов: {ctx.answers_total}
        </Badge>
        {ctx.answered_recently > 0 && (
          <Badge variant="outline" className="text-xs">
            средний балл: {Math.round(ctx.mean_score * 100)}%
          </Badge>
        )}
        {ctx.course_title && (
          <Badge variant="secondary" className="text-xs">
            курс: {ctx.course_title}
            {ctx.course_documents > 0 ? ` · ${ctx.course_documents} док.` : ""}
          </Badge>
        )}
      </div>

      <Group label="Уже освоено" items={compact ? ctx.known.slice(0, 4) : ctx.known} />
      <Group label="В работе" items={compact ? ctx.in_progress.slice(0, 3) : ctx.in_progress} />
      <Group label="Слабые места" items={compact ? ctx.weak.slice(0, 3) : ctx.weak} />

      {errors.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground">
            Самые частые ошибки
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {errors.map(([type, count]) => (
              <Badge key={type} className="bg-destructive/10 text-destructive">
                {ERROR_LABEL[type] ?? type}: {count}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}