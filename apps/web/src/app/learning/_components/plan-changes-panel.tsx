"use client";

/**
 * What the plan changed and why — TZ FR-10.4.
 *
 * The plan is recalculated after every check (FR-10.1), which used to happen
 * silently: the learner saw different dates with no explanation and no way to
 * keep the old ones. Each replan that actually moved something is now recorded,
 * shown here with a per-change line, and can be accepted or declined.
 *
 * Declining keeps the rows the learner had agreed to and flags them, so the next
 * replan leaves them alone instead of re-applying the same change.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  acceptPlanRevision,
  listPlanRevisions,
  rejectPlanRevision,
  type PlanRevision,
} from "@/lib/api/learning";
import { cn } from "@/lib/utils";

const KIND_TONE: Record<string, string> = {
  added: "bg-success-muted text-success",
  removed: "bg-destructive/10 text-destructive",
  moved: "bg-warning-muted text-warning",
  minutes: "bg-brand-muted text-brand",
};

const KIND_LABEL: Record<string, string> = {
  added: "добавлено",
  removed: "убрано",
  moved: "перенесено",
  minutes: "изменён объём",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "на ваше решение",
  accepted: "принято",
  rejected: "отклонено",
};

function when(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function PlanChangesPanel({
  goalId,
  refreshKey,
}: {
  goalId: string;
  /** Bump after an answer so a fresh replan shows up without a manual refresh. */
  refreshKey?: number;
}) {
  const [revisions, setRevisions] = useState<PlanRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await listPlanRevisions(goalId);
      setRevisions(data.revisions);
    } catch (error) {
      toast.error("Не удалось загрузить изменения плана", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function decide(revision: PlanRevision, accept: boolean) {
    setBusyId(revision.id);
    try {
      if (accept) await acceptPlanRevision(goalId, revision.id);
      else await rejectPlanRevision(goalId, revision.id);
      toast.success(accept ? "Изменение принято" : "Изменение отклонено");
      await load();
    } catch (error) {
      toast.error("Не удалось сохранить решение", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusyId(null);
    }
  }

  if (loading) {
    return <div className="h-24 animate-pulse rounded-2xl bg-muted" aria-busy="true" />;
  }

  if (revisions.length === 0) {
    return (
      <section className="card-shadow rounded-2xl border border-border bg-card p-5">
        <h2 className="text-base font-semibold tracking-tight text-foreground">
          Что изменилось в плане
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Пока изменений не было. Когда план пересчитается и что-то сдвинется,
          здесь появится список с причиной — и кнопки, чтобы отклонить.
        </p>
      </section>
    );
  }

  const pending = revisions.filter((r) => r.status === "pending");
  const rest = revisions.filter((r) => r.status !== "pending");
  const visible = showAll ? [...pending, ...rest] : [...pending, ...rest.slice(0, 2)];

  return (
    <section className="card-shadow space-y-3 rounded-2xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold tracking-tight text-foreground">
            Что изменилось в плане
          </h2>
          {pending.length > 0 && (
            <p className="text-xs text-muted-foreground">
              нерешённых изменений: {pending.length}
            </p>
          )}
        </div>
        {revisions.length > 3 && (
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            {showAll ? "Свернуть" : `Показать все (${revisions.length})`}
          </button>
        )}
      </div>

      <ul className="space-y-3">
        {visible.map((revision) => (
          <li
            key={revision.id}
            className={cn(
              "rounded-xl border p-4",
              revision.status === "pending"
                ? "border-brand/40 bg-brand-muted/30"
                : "border-border bg-background opacity-80",
            )}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-foreground">{revision.summary}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge
                    variant={
                      revision.status === "pending"
                        ? "default"
                        : "secondary"
                    }
                    className="text-xs"
                  >
                    {STATUS_LABEL[revision.status] ?? revision.status}
                  </Badge>
                  {when(revision.created_at)}
                </p>
              </div>
            </div>

            <ul className="mt-3 space-y-1.5">
              {revision.changes.slice(0, 6).map((change, i) => (
                <li key={`${revision.id}-${i}`} className="flex flex-wrap items-start gap-2">
                  <Badge
                    className={cn(
                      "shrink-0 text-[10px]",
                      KIND_TONE[change.kind] ?? "bg-muted text-muted-foreground",
                    )}
                  >
                    {KIND_LABEL[change.kind] ?? change.kind}
                  </Badge>
                  <span className="text-xs text-foreground">{change.text}</span>
                </li>
              ))}
              {revision.changes.length > 6 && (
                <li className="text-xs text-muted-foreground">
                  и ещё {revision.changes.length - 6}
                </li>
              )}
            </ul>

            {revision.status === "pending" && (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  size="xs"
                  variant="secondary"
                  disabled={busyId === revision.id}
                  onClick={() => void decide(revision, false)}
                >
                  Отклонить
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={busyId === revision.id}
                  onClick={() => void decide(revision, true)}
                >
                  Оставить
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}