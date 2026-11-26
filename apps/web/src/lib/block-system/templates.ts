import type { BlockInstance, SpaceLayout, BlockType, BlockSize, LearningMode } from "./types";

/** Helper to create a block instance for template definitions. */
function b(
  type: BlockType,
  size: BlockSize,
  config: Record<string, unknown> = {},
  position = 0,
): Omit<BlockInstance, "id"> & { id: string } {
  return {
    id: "", // will be assigned at apply time
    type,
    position,
    size,
    config,
    visible: true,
    source: "template",
  };
}

export interface TemplateDefinition {
  id: string;
  name: string;
  description: string;
  /** Default learning mode attached to this template. */
  defaultMode: LearningMode;
  blocks: Array<Omit<BlockInstance, "id"> & { id: string }>;
  columns: 1 | 2 | 3;
}

export const TEMPLATES: Record<string, TemplateDefinition> = {
  stem_student: {
    id: "stem_student",
    name: "STEM Student",
    description: "Step-by-step notes, adaptive quizzes, knowledge graph",
    defaultMode: "course_following",
    columns: 2,
    blocks: [
      b("notes", "large", { note_format: "step_by_step" }),
      b("quiz", "medium", { difficulty: "adaptive" }),
      b("knowledge_graph", "medium"),
    ],
  },
  humanities_scholar: {
    id: "humanities_scholar",
    name: "Humanities Scholar",
    description: "Rich narrative notes, review sessions, reading progress",
    defaultMode: "course_following",
    columns: 2,
    blocks: [
      
      b("notes", "large", { note_format: "summary" }),
    ],
  },
  visual_learner: {
    id: "visual_learner",
    name: "Visual Learner",
    description: "Knowledge graph prominent, mind map notes, visual aids",
    defaultMode: "self_paced",
    columns: 2,
    blocks: [
      
      b("knowledge_graph", "large"),
      b("notes", "medium", { note_format: "mind_map" }),
      b("quiz", "medium", { difficulty: "adaptive" }),
    ],
  },
  quick_reviewer: {
    id: "quick_reviewer",
    name: "Quick Reviewer",
    description: "Quiz-heavy, flashcards, error analysis",
    defaultMode: "course_following",
    columns: 2,
    blocks: [
      
      b("quiz", "large", { difficulty: "hard" }),
      b("flashcards", "medium"),
      b("wrong_answers", "medium"),
    ],
  },
  blank_canvas: {
    id: "blank_canvas",
    name: "Blank Canvas",
    description: "Start from scratch and build your own space.",
    defaultMode: "self_paced",
    columns: 2,
    blocks: [],
  },
};

const TEMPLATE_DISPLAY_ORDER = [
  "stem_student",
  "humanities_scholar",
  "visual_learner",
  "quick_reviewer",
  "blank_canvas",
] as const;

/** Templates shown in onboarding/template picker. */
export const TEMPLATE_LIST = TEMPLATE_DISPLAY_ORDER.map((id) => TEMPLATES[id]);

// ── Learning Mode Definitions ──

export interface LearningModeDefinition {
  id: LearningMode;
  name: string;
  description: string;
  icon: string;
  /** Default block layout for this mode. */
  blocks: Array<Omit<BlockInstance, "id"> & { id: string }>;
  columns: 1 | 2 | 3;
}

export const LEARNING_MODES: Record<LearningMode, LearningModeDefinition> = {
  course_following: {
    id: "course_following",
    name: "Course Following",
    description: "Timeline-driven. Deadlines, lecture notes, and syllabus tracking.",
    icon: "GraduationCap",
    columns: 2,
    blocks: [
      
      b("notes", "large"),
      b("quiz", "medium"),
      b("flashcards", "medium"),
    ],
  },
  self_paced: {
    id: "self_paced",
    name: "Self-Paced",
    description: "Exploration-driven. Notes, flashcards, and progress tracking.",
    icon: "Compass",
    columns: 2,
    blocks: [
      b("notes", "large"),
      b("flashcards", "medium"),
    ],
  },
};

export const LEARNING_MODE_LIST = Object.values(LEARNING_MODES);

/** Generate a SpaceLayout from a template, assigning unique IDs and positions. */
export function buildLayoutFromTemplate(templateId: string): SpaceLayout | null {
  const template = TEMPLATES[templateId];
  if (!template) return null;

  const blocks: BlockInstance[] = template.blocks.map((block, index) => ({
    ...block,
    id: `${templateId}-${block.type}-${index}`,
    position: index,
  }));

  return {
    templateId,
    blocks,
    columns: template.columns,
    mode: template.defaultMode,
  };
}

/** Generate a SpaceLayout from a learning mode, assigning unique IDs and positions. */
export function buildLayoutFromMode(mode: LearningMode): SpaceLayout {
  const def = LEARNING_MODES[mode];
  const blocks: BlockInstance[] = def.blocks.map((block, index) => ({
    ...block,
    id: `mode-${mode}-${block.type}-${index}`,
    position: index,
  }));

  return {
    templateId: null,
    blocks,
    columns: def.columns,
    mode,
  };
}
