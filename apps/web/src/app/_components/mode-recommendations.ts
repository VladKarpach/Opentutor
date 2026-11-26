import type { CourseProgress, StudyGoal } from "@/lib/api";
import type { LearningMode } from "@/lib/block-system/types";

export interface GoalDeadlineSnapshot<TGoal extends Pick<StudyGoal, "target_date" | "title"> = StudyGoal> {
  goal: TGoal;
  daysLeft: number;
}

export interface ModeSuggestionDecision {
  suggestedMode: LearningMode;
  recommendationKey: "exam_passed" | "error_rate" | "deadline" | "mastery";
  reason: string;
  signals: string[];
  approvalCta: string;
}

interface EvaluateModeSuggestionArgs {
  currentMode: LearningMode;
  deadlines: GoalDeadlineSnapshot<Pick<StudyGoal, "target_date" | "title">>[];
  progress: Pick<CourseProgress, "average_mastery" | "mastered" | "reviewed" | "in_progress"> | null;
  t: (key: string) => string;
  tf: (key: string, values: Record<string, number | string>) => string;
}

export function buildGoalDeadlineSnapshots<TGoal extends Pick<StudyGoal, "target_date" | "title">>(
  goals: TGoal[],
  nowMs = Date.now(),
): GoalDeadlineSnapshot<TGoal>[] {
  return goals
    .filter((goal) => goal.target_date)
    .map((goal) => ({
      goal,
      daysLeft: Math.ceil((new Date(goal.target_date!).getTime() - nowMs) / (1000 * 60 * 60 * 24)),
    }));
}

export function evaluateModeSuggestion({
  currentMode,
  deadlines,
  progress,
  t,
  tf,
}: EvaluateModeSuggestionArgs): ModeSuggestionDecision | null {
  const upcoming = deadlines
    .filter((deadline) => deadline.daysLeft >= 0 && deadline.daysLeft <= 7)
    .sort((a, b) => a.daysLeft - b.daysLeft)[0];
  const allDeadlinesPassed = deadlines.length > 0 && deadlines.every((deadline) => deadline.daysLeft < 0);

  const mastery = progress ? Math.round((progress.average_mastery ?? 0) * 100) : null;
  const totalAttempts = progress
    ? progress.mastered + progress.reviewed + progress.in_progress
    : 0;
  const errorRatePct =
    progress && totalAttempts > 10
      ? Math.round((progress.in_progress / totalAttempts) * 100)
      : null;

  // Self-paced is the default, but a near deadline or a high error rate is a
  // reason to switch to course following: the plan then reshapes itself around
  // the schedule and the weak spots instead of drifting past the deadline.
  if (currentMode === "self_paced" && upcoming && errorRatePct != null && errorRatePct > 40) {
    return {
      suggestedMode: "course_following",
      recommendationKey: "error_rate",
      reason: tf("course.modeSuggestion.errorRateDetailed", {
        rate: errorRatePct,
        days: upcoming.daysLeft,
      }),
      signals: [
        tf("course.modeSuggestion.signal.errorRate", { rate: errorRatePct }),
        tf("course.modeSuggestion.signal.deadline", { days: upcoming.daysLeft }),
      ],
      approvalCta: t("course.modeSuggestion.switchCourseFollowing"),
    };
  }

  if (currentMode === "self_paced" && upcoming) {
    return {
      suggestedMode: "course_following",
      recommendationKey: "deadline",
      reason: tf("course.modeSuggestion.deadline", {
        title: upcoming.goal.title,
        days: upcoming.daysLeft,
      }),
      signals: [tf("course.modeSuggestion.signal.deadline", { days: upcoming.daysLeft })],
      approvalCta: t("course.modeSuggestion.switchCourseFollowing"),
    };
  }

  // Once the schedule stops constraining, course following has nothing left to
  // hold on to, so the learner can move at their own pace.
  if (currentMode === "course_following" && allDeadlinesPassed) {
    return {
      suggestedMode: "self_paced",
      recommendationKey: "exam_passed",
      reason: t("course.modeSuggestion.examPassed"),
      signals: [t("course.modeSuggestion.signal.deadlinesPassed")],
      approvalCta: t("course.modeSuggestion.switchSelfPaced"),
    };
  }

  if (currentMode === "course_following" && mastery != null && mastery >= 85) {
    return {
      suggestedMode: "self_paced",
      recommendationKey: "mastery",
      reason: tf("course.modeSuggestion.mastery", { mastery }),
      signals: [tf("course.modeSuggestion.signal.mastery", { mastery })],
      approvalCta: t("course.modeSuggestion.switchSelfPaced"),
    };
  }

  return null;
}
