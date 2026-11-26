"use client";

import { lazy, useCallback, useMemo } from "react";
import { useT } from "@/lib/i18n-context";
import { useWorkspaceStore } from "@/store/workspace";
import { TabbedSection, type TabDef } from "./tabbed-section";

const QuizView = lazy(() =>
  import("./practice/quiz-view").then((m) => ({ default: m.QuizView })),
);
const FlashcardView = lazy(() =>
  import("./practice/flashcard-view").then((m) => ({ default: m.FlashcardView })),
);
interface PracticeSectionProps {
  courseId: string;
  aiActionsEnabled?: boolean;
  defaultTab?: PracticeTab;
  quizDifficultyHint?: "easy" | "medium" | "hard";
  quizModeHint?: "course_following" | "self_paced";
}

type PracticeTab = "quiz" | "flashcards";

export function PracticeSection({
  courseId,
  aiActionsEnabled = true,
  defaultTab = "quiz",
  quizDifficultyHint,
  quizModeHint,
}: PracticeSectionProps) {
  const t = useT();
  // The review tab is gone. It drove LECTOR, which is off in this deployment, so
  // it could only ever show "nothing to review" — a dead third tab next to two
  // working ones.
  const tabs: TabDef<PracticeTab>[] = useMemo(
    () => [
      { id: "quiz", label: t("course.quiz"), testId: "right-tab-quiz" },
      { id: "flashcards", label: t("course.cards"), testId: "right-tab-cards" },
    ],
    [t],
  );

  const practiceActiveTab = useWorkspaceStore((s) => s.practiceActiveTab) as PracticeTab | null;
  const clearPracticeTab = useCallback(() => {
    useWorkspaceStore.getState().setPracticeTab(null);
  }, []);

  return (
    <div role="region" aria-label="Practice">
    <TabbedSection
      tabs={tabs}
      defaultTab={tabs.some((t) => t.id === defaultTab) ? defaultTab : "quiz"}
      testId="practice-section"
      externalTab={practiceActiveTab}
      onExternalTabConsumed={clearPracticeTab}
    >
      {(activeTab) => (
        <>
          {activeTab === "quiz" ? (
            <QuizView
              courseId={courseId}
              aiActionsEnabled={aiActionsEnabled}
              modeHint={quizModeHint}
              difficultyHint={quizDifficultyHint}
            />
          ) : null}
          {activeTab === "flashcards" ? <FlashcardView courseId={courseId} aiActionsEnabled={aiActionsEnabled} /> : null}
        </>
      )}
    </TabbedSection>
    </div>
  );
}
