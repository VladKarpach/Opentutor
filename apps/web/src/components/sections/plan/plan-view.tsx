"use client";

import { useEffect, useMemo, useReducer, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getReviewSession,
  getStudyPlans,
  listStudyGoals,
  createStudyGoal,
  updateStudyGoal,
  type ReviewItem,
  type StudyGoal,
  type StudyPlanResponse,
} from "@/lib/api";
import { SkeletonText } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { updateUnlockContext } from "@/lib/block-system/feature-unlock";
import type { LearningMode } from "@/lib/block-system/types";
import { useT, useTF } from "@/lib/i18n-context";

import { CourseFollowingTimeline } from "./_components/course-following-timeline";
import { SelfPacedPath } from "./_components/self-paced-path";
import { SavedPlansList } from "./_components/saved-plans-list";

// ── Goal form state ──

interface GoalFormState {
  visible: boolean;
  editingGoalId: string | null;
  title: string;
  objective: string;
  targetDate: string;
  saving: boolean;
}

const GOAL_FORM_INITIAL: GoalFormState = {
  visible: false,
  editingGoalId: null,
  title: "",
  objective: "",
  targetDate: "",
  saving: false,
};

type GoalFormAction =
  | { type: "reset" }
  | { type: "show" }
  | { type: "edit"; goal: StudyGoal }
  | { type: "set_field"; field: "title" | "objective" | "targetDate"; value: string }
  | { type: "set_saving"; saving: boolean };

function goalFormReducer(state: GoalFormState, action: GoalFormAction): GoalFormState {
  switch (action.type) {
    case "reset":
      return GOAL_FORM_INITIAL;
    case "show":
      return { ...GOAL_FORM_INITIAL, visible: true };
    case "edit":
      return {
        visible: true,
        editingGoalId: action.goal.id,
        title: action.goal.title,
        objective: action.goal.objective,
        targetDate: action.goal.target_date?.split("T")[0] ?? "",
        saving: false,
      };
    case "set_field":
      return { ...state, [action.field]: action.value };
    case "set_saving":
      return { ...state, saving: action.saving };
  }
}

// ── PlanView ──

interface PlanViewProps {
  courseId: string;
  learningMode?: LearningMode;
}

export function PlanView({
  courseId,
  learningMode,
}: PlanViewProps) {
  const t = useT();
  const tf = useTF();
  const mode: LearningMode = learningMode ?? "course_following";

  const [goals, setGoals] = useState<StudyGoal[]>([]);
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [savedPlans, setSavedPlans] = useState<StudyPlanResponse[]>([]);
  const [loadingContext, setLoadingContext] = useState(true);

  // ── Goal creation/editing ──
  const [goalForm, dispatchGoalForm] = useReducer(goalFormReducer, GOAL_FORM_INITIAL);

  const handleSaveGoal = async () => {
    if (!goalForm.title.trim() || !goalForm.objective.trim()) return;
    dispatchGoalForm({ type: "set_saving", saving: true });
    try {
      if (goalForm.editingGoalId) {
        const updated = await updateStudyGoal(goalForm.editingGoalId, {
          title: goalForm.title.trim(),
          objective: goalForm.objective.trim(),
          target_date: goalForm.targetDate || null,
        });
        setGoals((prev) => prev.map((g) => (g.id === updated.id ? updated : g)));
        toast.success(t("plan.goals.updated"));
      } else {
        const created = await createStudyGoal({
          title: goalForm.title.trim(),
          objective: goalForm.objective.trim(),
          course_id: courseId,
          target_date: goalForm.targetDate || undefined,
        });
        setGoals((prev) => [created, ...prev]);
        if (goalForm.targetDate) {
          updateUnlockContext(courseId, { hasDeadline: true });
        }
        toast.success(t("plan.goals.created"));
      }
      dispatchGoalForm({ type: "reset" });
    } catch (error) {
      toast.error((error as Error).message || t("plan.goals.saveFailed"));
    } finally {
      dispatchGoalForm({ type: "set_saving", saving: false });
    }
  };

  const handleCompleteGoal = async (goalId: string) => {
    try {
      const updated = await updateStudyGoal(goalId, { status: "completed" });
      setGoals((prev) => prev.map((g) => (g.id === updated.id ? updated : g)));
      toast.success(t("plan.goals.completed"));
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  useEffect(() => {
    let cancelled = false;
    setLoadingContext(true);

    Promise.all([
      listStudyGoals(courseId).catch(() => [] as StudyGoal[]),
      getReviewSession(courseId, 30).then((r) => r.items).catch(() => [] as ReviewItem[]),
      getStudyPlans(courseId).catch(() => [] as StudyPlanResponse[]),
    ])
      .then(([nextGoals, nextReviewItems, nextPlans]) => {
        if (cancelled) return;
        setGoals(nextGoals);
        setReviewItems(nextReviewItems);
        setSavedPlans(nextPlans);
        if (nextGoals.some((g) => g.target_date)) {
          updateUnlockContext(courseId, { hasDeadline: true });
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingContext(false);
      });

    return () => {
      cancelled = true;
    };
  }, [courseId]);

  const modeLabel = useMemo(() => t(`mode.${mode}`), [mode, t]);
  const modeDesc = useMemo(() => t(`mode.${mode}.desc`), [mode, t]);

  return (
    <div role="region" aria-label={t("plan.ariaLabel")} className="flex-1 flex flex-col overflow-hidden" data-testid="study-plan-panel">
      <div className="px-3 py-2 border-b border-border/60 flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-foreground">{modeLabel}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{modeDesc}</p>
        </div>
        {!goalForm.visible ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => dispatchGoalForm({ type: "show" })}
          >
            {t("plan.goals.add")}
          </Button>
        ) : null}
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin p-4">
        {goalForm.visible ? (
          <div className="mb-4 p-3 border border-border rounded-md space-y-2 bg-muted/30">
            <Input
              value={goalForm.title}
              onChange={(e) => dispatchGoalForm({ type: "set_field", field: "title", value: e.target.value })}
              placeholder={t("plan.goals.titlePlaceholder")}
              className="text-sm"
            />
            <Input
              value={goalForm.objective}
              onChange={(e) => dispatchGoalForm({ type: "set_field", field: "objective", value: e.target.value })}
              placeholder={t("plan.goals.objectivePlaceholder")}
              className="text-sm"
            />
            <Input
              type="date"
              value={goalForm.targetDate}
              onChange={(e) => dispatchGoalForm({ type: "set_field", field: "targetDate", value: e.target.value })}
              className="text-sm"
            />
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="ghost" onClick={() => dispatchGoalForm({ type: "reset" })} disabled={goalForm.saving}>
                {t("plan.goals.cancel")}
              </Button>
              <Button
                size="sm"
                onClick={() => void handleSaveGoal()}
                disabled={goalForm.saving || !goalForm.title.trim() || !goalForm.objective.trim()}
              >
                {goalForm.saving ? "..." : goalForm.editingGoalId ? t("plan.goals.update") : t("plan.goals.create")}
              </Button>
            </div>
          </div>
        ) : null}

        {loadingContext ? (
          <div role="status" aria-label={t("plan.loadingLabel")}>
            <SkeletonText lines={3} />
          </div>
        ) : null}

        {!loadingContext && goals.length > 0 ? (
          <div className="mb-4 space-y-2">
            {goals.filter((g) => g.status === "active").map((goal) => (
              <div key={goal.id} className="flex items-start gap-2 p-2 rounded border border-border/60 text-sm">
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{goal.title}</p>
                  <p className="text-xs text-muted-foreground truncate">{goal.objective}</p>
                  {goal.target_date ? (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {new Date(goal.target_date).toLocaleDateString()}
                    </p>
                  ) : null}
                </div>
                <Button size="sm" variant="ghost" className="h-6 px-1.5 text-xs" onClick={() => dispatchGoalForm({ type: "edit", goal })}>
                  {t("plan.goals.edit")}
                </Button>
                <Button size="sm" variant="ghost" className="h-6 px-1.5 text-xs" onClick={() => void handleCompleteGoal(goal.id)}>
                  {t("plan.goals.done")}
                </Button>
              </div>
            ))}
          </div>
        ) : null}

        {!loadingContext && mode === "course_following" ? (
          <CourseFollowingTimeline goals={goals} t={t} tf={tf} />
        ) : null}
        {!loadingContext && mode === "self_paced" ? (
          <SelfPacedPath goals={goals} reviewItems={reviewItems} t={t} />
        ) : null}

        {!loadingContext && savedPlans.length > 0 ? (
          <div className="mt-4">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
              {t("plan.savedPlans")}
            </h4>
            <SavedPlansList plans={savedPlans} t={t} tf={tf} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
