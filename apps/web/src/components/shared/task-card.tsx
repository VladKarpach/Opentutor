"use client";

/**
 * A single task card used across the unified "Мои задачи" screen.
 *
 * Supports all four item types returned by the backend review/today/overdue
 * endpoints: review, plan_item, wrong_answer, video.
 *
 * Mobile-friendly: stacked layout on small screens, large touch targets,
 * full-width action button on mobile for easy thumb access.
 */

import Link from "next/link";
import {
  AlertTriangle,
  Clock3,
  ExternalLink,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { DueReviewItem } from "@/lib/api/learning";

const TYPE_LABEL: Record<string, string> = {
  review: "Повторение",
  plan_item: "План на сегодня",
  wrong_answer: "Ошибка",
  video: "Видео",
};

const TYPE_BADGE_VARIANT: Record<string, string> = {
  review: "outline",
  plan_item: "outline",
  wrong_answer: "destructive",
  video: "outline",
};

const TYPE_ICON: Record<string, typeof Sparkles> = {
  review: Clock3,
  plan_item: Clock3,
  wrong_answer: AlertTriangle,
  video: RefreshCw,
};

interface TaskCardProps {
  item: DueReviewItem;
}

const ACTION_LABEL: Record<string, string> = {
  review: "Повторить",
  retry: "Разобрать",
  continue: "Продолжить",
  watch: "Открыть",
};

function isExternalUrl(url: string): boolean {
  return url.startsWith("http://") || url.startsWith("https://");
}

export function TaskCard({ item }: TaskCardProps) {
  const Icon = TYPE_ICON[item.type] ?? Sparkles;
  const actionLabel =
    ACTION_LABEL[item.action ?? ""] ??
    (item.type === "review"
      ? "Повторить"
      : item.type === "wrong_answer"
        ? "Разобрать"
        : item.type === "plan_item"
          ? "Продолжить"
          : "Открыть");
  const href = item.action_href ?? `/learning/goals/${item.goal_id}`;

  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-xl border p-4 transition-colors",
        "border-border bg-card",
        // Larger touch target on mobile: full-height card
        "min-h-[88px] md:min-h-auto",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <div className="flex items-center gap-2 shrink-0">
            <Icon className="size-4 shrink-0 text-muted-foreground group-hover:text-brand transition-colors" />
            <Badge
              variant={
                TYPE_BADGE_VARIANT[item.type] as
                  | "default"
                  | "link"
                  | "secondary"
                  | "destructive"
                  | "outline"
                  | "ghost"
              }
              className="text-xs"
            >
              {TYPE_LABEL[item.type]}
            </Badge>
          </div>
          <h4 className="text-sm font-medium text-foreground mt-0.5 leading-tight">
            {item.title}
          </h4>
          {item.reason && (
            <p className="text-xs text-muted-foreground mt-1 leading-relaxed line-clamp-2">
              {item.reason}
            </p>
          )}
          {item.minutes && (
            <p className="text-[10px] text-muted-foreground mt-1">
              {item.minutes} мин
            </p>
          )}
          {item.day && (
            <p className="text-[10px] text-muted-foreground mt-1">
              {item.day}
            </p>
          )}
          {item.due_at && (
            <p className="text-[10px] text-muted-foreground mt-1">
              до {new Date(item.due_at).toLocaleDateString("ru-RU", {
                day: "numeric",
                month: "short",
              })}
            </p>
          )}
        </div>
        <div className="flex shrink-0">
          {isExternalUrl(href) ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => window.open(href, "_blank")}
              className="w-auto md:w-auto"
            >
              <ExternalLink className="mr-1 size-3.5" /> {actionLabel}
            </Button>
          ) : (
            <Link
              href={href}
              className="px-3 py-1.5 rounded-md border border-border text-xs text-brand hover:bg-brand/5 hover:text-brand-foreground transition-colors group-hover:bg-brand/10 whitespace-nowrap"
            >
              {actionLabel}
            </Link>
          )}
        </div>
      </div>
      <div className="mt-2 text-[10px] text-muted-foreground">
        {item.goal_title}
      </div>
    </div>
  );
}
