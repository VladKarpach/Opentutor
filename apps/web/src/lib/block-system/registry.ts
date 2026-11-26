import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { BlockType, BlockSize } from "./types";

export interface BlockComponentProps {
  courseId: string;
  blockId: string;
  config: Record<string, unknown>;
  aiActionsEnabled: boolean;
}

export interface BlockRegistryEntry {
  type: BlockType;
  label: string;
  icon: string;
  description: string;
  defaultSize: BlockSize;
  defaultConfig: Record<string, unknown>;
  component: LazyExoticComponent<ComponentType<BlockComponentProps>>;
}

const entry = (
  type: BlockType,
  label: string,
  icon: string,
  description: string,
  defaultSize: BlockSize,
  defaultConfig: Record<string, unknown>,
  loader: () => Promise<{ default: ComponentType<BlockComponentProps> }>,
): BlockRegistryEntry => ({
  type,
  label,
  icon,
  description,
  defaultSize,
  defaultConfig,
  component: lazy(loader),
});

export const BLOCK_REGISTRY: Record<BlockType, BlockRegistryEntry> = {
  notes: entry(
    "notes", "Notes", "FileText",
    "AI-generated study notes",
    "large", {},
    () => import("@/components/blocks/blocks/notes-block"),
  ),
  quiz: entry(
    "quiz", "Quiz", "CircleHelp",
    "Practice questions and quizzes",
    "medium", { difficulty: "adaptive" },
    () => import("@/components/blocks/blocks/quiz-block"),
  ),
  flashcards: entry(
    "flashcards", "Flashcards", "Layers",
    "Spaced repetition flashcards",
    "medium", {},
    () => import("@/components/blocks/blocks/flashcards-block"),
  ),
  progress: entry(
    "progress", "Progress", "BarChart3",
    "Mastery and completion stats",
    "small", {},
    () => import("@/components/blocks/blocks/progress-block"),
  ),
  knowledge_graph: entry(
    "knowledge_graph", "Knowledge Graph", "GitBranch",
    "LOOM concept relationship map",
    "medium", {},
    () => import("@/components/blocks/blocks/knowledge-graph-block"),
  ),
  review: entry(
    "review", "Review", "RotateCcw",
    "LECTOR-driven spaced review",
    "medium", {},
    () => import("@/components/blocks/blocks/review-block"),
  ),
  plan: entry(
    "plan", "Study Plan", "CalendarDays",
    "Goals, tasks, and deadlines",
    "medium", {},
    () => import("@/components/blocks/blocks/plan-block"),
  ),
  wrong_answers: entry(
    "wrong_answers", "Weak Spots", "AlertTriangle",
    "Error patterns and misconceptions",
    "medium", {},
    () => import("@/components/blocks/blocks/wrong-answers-block"),
  ),
  forecast: entry(
    "forecast", "Forecast", "TrendingUp",
    "Learning trajectory forecast",
    "small", {},
    () => import("@/components/blocks/blocks/forecast-block"),
  ),
  agent_insight: entry(
    "agent_insight", "Agent Insight", "Sparkles",
    "Proactive suggestions from your AI tutor",
    "full", {},
    () => import("@/components/blocks/blocks/agent-insight-block"),
  ),
  summary: entry(
    "summary", "Daily Digest", "Newspaper",
    "Today's learning snapshot: mastery, quiz accuracy, review due",
    "small", {},
    () => import("@/components/blocks/blocks/summary-block"),
  ),
  theory: entry(
    "theory", "Теория / Theory", "Link",
    "Pinned documentation links and reading materials",
    "large", {},
    () => import("@/components/blocks/blocks/theory-block"),
  ),
  tasks: entry(
    "tasks", "Задания / Tasks", "FileText",
    "Your own uploaded assignments and tasks",
    "large", {},
    () => import("@/components/blocks/blocks/tasks-block"),
  ),
};

/**
 * Block types available for user to add manually (excludes agent_insight).
 *
 * "review" and "progress" are deliberately absent. Both report on the base
 * project's own bookkeeping — LECTOR concept mastery and per-node LearningProgress
 * — neither of which this deployment fills, so they could only render as empty
 * panels. The components still exist and a stored layout can still carry them.
 *
 * "knowledge_graph" is absent for a different reason: the graph is not in use, and
 * offering it would put an empty panel in front of the learner. Unlike the two
 * above it is also gone from the templates and from the /graph route.
 */
export const USER_ADDABLE_BLOCKS: BlockType[] = [
  "notes", "quiz", "flashcards", "plan",
  "wrong_answers", "forecast", "summary", "theory", "tasks",
];
