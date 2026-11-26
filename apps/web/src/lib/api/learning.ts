/**
 * API client for the adaptive learning endpoints (TZ section 10).
 *
 * Mirrors the existing `src/lib/api/*` modules: thin typed wrappers over
 * `request()` / `requestBlob()`, so CSRF headers, auth and retry come for free.
 */

import { request, requestBlob } from "./client";
import { getLocale } from "../i18n";

// ── Goals (TZ /goals) ──

export type GoalDepth = "overview" | "confident" | "deep";
export type GoalStatus = "active" | "paused" | "completed" | "archived";

/**
 * How far a goal has got, so the course page can say where the learner is.
 *
 * `exam_passed` clears `current_topic_title`: once the goal is finished there is
 * no next topic to open.
 */
/**
 * A topic note: a summary of the explanation the learner already read.
 *
 * Topics with no explanation are absent from the index rather than listed empty —
 * they have nothing to summarise yet, which is different from a failed note.
 */
export interface TopicNoteIndexItem {
  topic_id: string;
  topic_title: string;
  goal_id: string;
  goal_topic: string;
  course_id: string | null;
  order_index: number;
  explanation_chars: number;
  /** What a note here would be written from: explanation, description or title. */
  note_source: "explanation" | "description" | "title";
  has_note: boolean;
  note_markdown: string | null;
}

export interface TopicNotesIndex {
  topics: TopicNoteIndexItem[];
}

export async function getTopicNotes(options?: {
  courseId?: string;
  goalId?: string;
}): Promise<TopicNotesIndex> {
  const params = new URLSearchParams();
  if (options?.courseId) params.set("course_id", options.courseId);
  if (options?.goalId) params.set("goal_id", options.goalId);
  const qs = params.toString();
  return request(`/learning/notes${qs ? `?${qs}` : ""}`);
}

export async function writeTopicNote(
  topicId: string,
  noteFormat: string = "bullet_point",
): Promise<{
  topic_id: string;
  topic_title: string;
  goal_id: string;
  markdown: string;
  cached: boolean;
}> {
  return request(`/learning/topics/${topicId}/notes`, {
    method: "POST",
    body: JSON.stringify({ note_format: noteFormat }),
  });
}

export interface GoalProgress {
  topics_total: number;
  topics_done: number;
  exam_status: "none" | "in_progress" | "completed";
  exam_score: number | null;
  exam_passed: boolean;
  current_topic_title: string | null;
  progress_percent: number;
}

export interface LearningGoal extends GoalProgress {
  id: string;
  topic: string;
  deadline: string | null;
  minutes_per_day: number;
  depth: GoalDepth;
  status: GoalStatus;
  /** The goal the guided "Сегодня" mode opens. At most one per learner. */
  is_active?: boolean;
  clarification_needed: boolean;
  /** Set when the goal was closed, manually or by a passed exam. */
  completed_at: string | null;
  /** The course workspace this goal belongs to, when it has one. */
  course_id: string | null;
  course_name: string | null;
  topic_count: number;
  days_left: number | null;
  created_at: string | null;
  updated_at: string | null;
}

/** TZ section 9: what the system already knows about this learner. */
export interface LearnerContext {
  goals_seen: number;
  topics_touched: number;
  answers_total: number;
  answered_recently: number;
  known: string[];
  weak: string[];
  in_progress: string[];
  prior_goal_topics: string[];
  error_profile: Record<string, number>;
  dominant_error: string;
  mean_score: number;
  course_title: string;
  course_goals: number;
  course_documents: number;
  /** The rendered block handed to the model. */
  summary: string;
}

export interface CourseGoals {
  course_id: string;
  course_name: string | null;
  goals: LearningGoal[];
}

export interface CreateGoalPayload {
  topic: string;
  deadline?: string | null;
  days?: number;
  minutes_per_day?: number;
  depth?: GoalDepth;
  time_share?: number;
  /** Attach the goal to a course workspace so it can use that course's materials. */
  course_id?: string | null;
}

export interface UpdateGoalPayload {
  topic?: string;
  deadline?: string;
  minutes_per_day?: number;
  depth?: GoalDepth;
  status?: GoalStatus;
}

export async function listGoals(status?: GoalStatus): Promise<LearningGoal[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/learning/goals${query}`);
}

export async function createGoal(payload: CreateGoalPayload): Promise<LearningGoal> {
  return request("/learning/goals", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getGoal(goalId: string): Promise<LearningGoal> {
  return request(`/learning/goals/${goalId}`);
}

/**
 * Choose which goal the guided daily mode opens. Goals with status "active"
 * are all still in progress; this is the one "Сегодня" should work on.
 */
export async function activateGoal(goalId: string): Promise<LearningGoal> {
  return request(`/learning/goals/${goalId}/activate`, { method: "POST" });
}

/** One difference between the old and the new plan (FR-10.4). */
export interface PlanChange {
  kind: "added" | "removed" | "moved" | "minutes" | string;
  topic_id: string;
  topic_title: string;
  item_type: string;
  before: string | number | null;
  after: string | number | null;
  delta_minutes: number;
  reason: string;
  text: string;
}

export interface PlanRevision {
  id: string;
  goal_id: string;
  trigger: string;
  summary: string;
  status: "pending" | "accepted" | "rejected";
  changes: PlanChange[];
  created_at: string | null;
  resolved_at: string | null;
}

export async function listPlanRevisions(
  goalId: string,
  status?: PlanRevision["status"],
): Promise<{ goal_id: string; revisions: PlanRevision[]; pending_count: number }> {
  const query = status ? `?status=${status}` : "";
  return request(`/learning/goals/${goalId}/plan/revisions${query}`);
}

/** FR-10.4: decline the change; the affected rows stay as they were. */
export async function rejectPlanRevision(
  goalId: string,
  revisionId: string,
): Promise<PlanRevision> {
  return request(`/learning/goals/${goalId}/plan/revisions/${revisionId}/reject`, {
    method: "POST",
  });
}

export async function acceptPlanRevision(
  goalId: string,
  revisionId: string,
): Promise<PlanRevision> {
  return request(`/learning/goals/${goalId}/plan/revisions/${revisionId}/accept`, {
    method: "POST",
  });
}

/** How far the learner has got through one subtopic's task series. */
export interface DrillProgress {
  drill_id: string | null;
  required: number;
  answered: number;
  done: boolean;
  prepared: boolean;
  status: string;
  answered_question_ids: string[];
  remaining_question_ids: string[];
}

/**
 * A subtopic together with its plan dates and practice progress. One row, so
 * "what to study" and "when" never need two tabs.
 */
export interface ScheduledTopic {
  topic_id: string;
  title: string;
  description: string | null;
  order_index: number;
  estimated_minutes: number;
  first_day: string | null;
  last_day: string | null;
  study_days: string[];
  planned_minutes: number;
  strength: number | null;
  status: TopicStatus | null;
  next_review_at: string | null;
  drill: DrillProgress;
  /** Control assignments scheduled for this topic, one per required task. */
  practice_items: Array<{
    id: string;
    day: string;
    title: string;
    planned_minutes: number;
    status: string;
    reason: string | null;
  }>;
}

export interface GoalSchedule {
  goal_id: string;
  topics: ScheduledTopic[];
  exam_unlocked: boolean;
  topics_total: number;
  topics_done: number;
  pending_exam: { topic_id: string; title: string; reason: string; required: number; answered: number }[];
}

export async function getGoalSchedule(goalId: string): Promise<GoalSchedule> {
  return request(`/learning/goals/${goalId}/schedule`);
}

// ── Guided daily session ──
//
// The plan still owns the dates. This is the runnable slice of one day:
// review -> theory -> practice, assembled without choosing a topic by hand.

export type DailyStepKind =
  | "review"
  | "theory"
  | "practice"
  | "exam_prep"
  | "exam";

export interface DailyStep {
  kind: DailyStepKind;
  /** Optional: the task-only and exam days are not tied to one topic. */
  topic_id: string | null;
  title: string;
  description: string | null;
  minutes: number;
  reason: string | null;
  item_id: string | null;
  has_explanation: boolean;
  /** The saved explanation the learner already read, when there is one. */
  theory: string | null;
  strength: number | null;
  status: string | null;
  practice_ready: boolean;
}

export interface DailySession {
  goal_id: string;
  goal_title: string;
  date: string;
  /** Can be later than `date` when the plan has a rest day. */
  scheduled_for: string;
  minutes_per_day: number;
  total_minutes: number;
  steps: DailyStep[];
  /** How many days the plan moved because the learner fell behind. */
  shifted_days: number;
  carried_topics: string[];
  completed: boolean;
  empty_reason: string | null;
  message: string | null;
  next_session_at: string | null;
  remaining_topics: number;
}

/**
 * Prepare today's session. A POST because it may move overdue plan items to
 * today; calling it again on the same day is idempotent.
 */
export async function prepareDailySession(goalId: string): Promise<DailySession> {
  return request(`/learning/goals/${goalId}/daily/prepare`, { method: "POST" });
}

export interface DailyAgenda {
  date: string;
  /** The explicit daily-mode choice, highlighted in the agenda. */
  active_goal_id: string | null;
  /** One runnable session per goal that has work in today's calendar column. */
  sessions: DailySession[];
  total_minutes: number;
}

/**
 * Prepare every goal that has work today. The global calendar can place more
 * than one goal on the same day, so "Сегодня" cannot be limited to the active
 * goal. Overdue plans are carried forward exactly as in the single-goal flow.
 */
export async function prepareDailyAgenda(): Promise<DailyAgenda> {
  return request("/learning/daily/prepare", { method: "POST" });
}

/**
 * Close the session for the topics that were actually finished.
 *
 * Topics the learner did not pass are left pending, so tomorrow's preparation
 * carries them forward and shifts the plan.
 */
export async function completeDailySession(
  goalId: string,
  payload: { day: string; topic_ids: string[]; item_ids?: string[] },
): Promise<{ goal_id: string; date: string; completed_items: number }> {
  return request(`/learning/goals/${goalId}/daily/complete`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface PullDayResult {
  goal_id: string;
  from_day: string;
  moved_items: number;
  review_minutes: number;
  review_topic_id: string | null;
  remaining_items: number;
  message: string | null;
}

/**
 * Take a future day's lesson now. The original day is not emptied: the server
 * leaves a shortened review there (40% of the moved time). Today's own pending
 * work must be finished first, otherwise the request is rejected.
 */
export async function pullDailyDay(
  goalId: string,
  day?: string,
): Promise<PullDayResult> {
  return request(`/learning/goals/${goalId}/daily/pull`, {
    method: "POST",
    body: JSON.stringify({ day: day ?? null }),
  });
}

export interface DrillResult {
  topic_id: string;
  title: string;
  drill: DrillProgress;
  questions: Question[];
}

/** Prepare a subtopic's task series (2-3 tasks, easiest first). Idempotent. */
export async function prepareDrill(
  topicId: string,
  language = getLocale(),
): Promise<DrillResult> {
  return request(`/learning/topics/${topicId}/drill?language=${language}`, {
    method: "POST",
  });
}

/** Goals attached to a course workspace. */
export async function listCourseGoals(courseId: string): Promise<CourseGoals> {
  return request(`/learning/courses/${courseId}/goals`);
}

/** One course and the goals inside it. */
export interface CourseGoalsGroup {
  course_id: string;
  course_name: string;
  goal_count: number;
  goals: LearningGoal[];
}

/**
 * The whole learning tree: courses with their goals, plus anything not yet
 * attached to a course. Goals live inside courses, so this is what the UI
 * renders rather than one flat mixed list.
 */
export interface LearningIndex {
  courses: CourseGoalsGroup[];
  unattached: LearningGoal[];
  total_goals: number;
  /**
   * The learner's explicit choice for "Сегодня". Null on an installation that
   * has never chosen; the UI then keeps its old nearest-deadline fallback.
   */
  active_goal_id: string | null;
}

export async function getLearningIndex(): Promise<LearningIndex> {
  return request("/learning/index");
}

/** Goals that have overdue or missed plan items. */
export interface OverdueGoal {
  goal_id: string;
  goal_title: string;
  course_id: string | null;
  overdue_count: number;
  missed_count: number;
  pending_past_count: number;
  earliest_overdue_day: string | null;
}

/** Request to shift overdue goals forward. */
export interface CascadeReplanRequest {
  goal_ids: string[];
  days_to_shift: number;
}

/** Result of shifting one goal. */
export interface CascadeReplanResult {
  goal_id: string;
  goal_title: string;
  items_shifted: number;
  new_first_day: string | null;
}

/** Response of cascade replan. */
export interface CascadeReplanResponse {
  shifted: CascadeReplanResult[];
  total_shifted: number;
}

/** List goals with overdue work. */
export async function getOverdueGoals(): Promise<OverdueGoal[]> {
  return request("/learning/goals/overdue");
}

/** Shift selected overdue goals forward by N days. */
export async function cascadeReplanGoals(
  body: CascadeReplanRequest,
): Promise<CascadeReplanResponse> {
  return request("/learning/goals/cascade-replan", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Move a goal into a course (or detach it with null). */
export async function attachGoalToCourse(
  goalId: string,
  courseId: string | null,
): Promise<LearningGoal> {
  return request(`/learning/goals/${goalId}`, {
    method: "PATCH",
    body: JSON.stringify({ course_id: courseId }),
  });
}

/**
 * What the model already knows about this learner, across all of their goals.
 * Empty `summary` means a brand-new account and there is nothing to send.
 */
export async function getLearnerContext(
  options: { goalId?: string; courseId?: string } = {},
): Promise<LearnerContext> {
  const params = new URLSearchParams();
  if (options.goalId) params.set("goal_id", options.goalId);
  if (options.courseId) params.set("course_id", options.courseId);
  const query = params.toString();
  return request(`/learning/context${query ? `?${query}` : ""}`);
}

export async function updateGoal(
  goalId: string,
  payload: UpdateGoalPayload,
): Promise<LearningGoal> {
  return request(`/learning/goals/${goalId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteGoal(goalId: string): Promise<void> {
  return request(`/learning/goals/${goalId}`, { method: "DELETE" });
}

/** FR-2.2: ask whether the topic is too broad to plan around. */
export interface ClarifyResult {
  goal_id: string;
  clarification_needed: boolean;
  prompt: string | null;
  topic_count: number;
}

export async function clarifyGoal(goalId: string): Promise<ClarifyResult> {
  return request(`/learning/goals/${goalId}/clarify`, { method: "POST" });
}

// ── Time allocation (FR-2.3) ──

export interface AllocationEntry {
  goal_id: string;
  topic: string;
  minutes_per_day: number;
}

export interface AllocationResult {
  minutes_per_day: number;
  total_allocated: number;
  allocation: AllocationEntry[];
}

export async function getAllocation(
  goalIds: string[],
  minutesPerDay: number,
): Promise<AllocationResult> {
  const params = goalIds
    .map((id) => `goal_id=${encodeURIComponent(id)}`)
    .join("&");
  return request(
    `/learning/allocation?${params}&minutes_per_day=${minutesPerDay}`,
  );
}

// ── Topics (TZ /topics/{id}) ──

export type ManualStatus = "known" | "skip";

export interface LearningTopic {
  id: string;
  goal_id: string;
  title: string;
  description: string | null;
  estimated_minutes: number;
  order_index: number;
  manual_status: ManualStatus | null;
  strength: number | null;
  status: TopicStatus | null;
  next_review_at: string | null;
  /**
   * Whether an explanation exists. A note and a quiz are both refused without
   * one, so this is what the topic board needs to show which topics can be
   * worked on at all.
   */
  has_explanation: boolean | null;
}

export async function listTopics(goalId: string): Promise<LearningTopic[]> {
  return request(`/learning/goals/${goalId}/topics`);
}

export async function addTopics(
  goalId: string,
  topics: string[],
): Promise<LearningTopic[]> {
  return request(`/learning/goals/${goalId}/topics`, {
    method: "POST",
    body: JSON.stringify({ topics }),
  });
}

/**
 * TZ 6.1 step 1: let the model split the goal into subtopics.
 *
 * Both the diagnostic (FR-4) and the planner (FR-6) refuse to run on a goal with
 * no subtopics, so this is the first step after creating one.
 */
export async function generateTopics(
  goalId: string,
  options?: { depth?: GoalDepth; language?: string },
): Promise<LearningTopic[]> {
  return request(`/learning/goals/${goalId}/topics/generate`, {
    method: "POST",
    body: JSON.stringify(options ?? {}),
  });
}

export async function getTopic(topicId: string): Promise<LearningTopic> {
  return request(`/learning/topics/${topicId}`);
}

/** FR-5.6: mark a topic as already known or to be skipped. */
export async function updateTopic(
  topicId: string,
  payload: {
    title?: string;
    estimated_minutes?: number;
    manual_status?: ManualStatus | null;
  },
): Promise<LearningTopic> {
  return request(`/learning/topics/${topicId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

/** FR-7.1 explanation; FR-7.2 "explain it differently". */
export interface ExplainResult {
  topic_id: string;
  style: string;
  text: string;
  /** The uploaded file the text was written from; null for "AI only". */
  source_label?: string | null;
}

export async function explainTopic(
  topicId: string,
  style = "short",
  regenerate = false,
  options?: {
    /** A course file name; when omitted the server keeps automatic matching. */
    sourceFile?: string | null;
    /** False asks for a text written without any uploaded literature. */
    ground?: boolean;
  },
): Promise<ExplainResult> {
  return request(`/learning/topics/${topicId}/explain`, {
    method: "POST",
    body: JSON.stringify({
      style,
      regenerate,
      language: getLocale(),
      source_file: options?.sourceFile ?? null,
      ground: options?.ground ?? true,
    }),
  });
}

// ── Comprehension check ──
//
// A free-recall dialogue, not a multiple-choice quiz: the learner explains the
// topic, the server diagnoses each key unit, and the next question is either a
// probe or a short re-teach of one unit.

export type ComprehensionUnitStatus =
  | "understood"
  | "partial"
  | "missing"
  | "misconception"
  | "surface_memorization"
  | "missing_prerequisite";

export interface ComprehensionUnit {
  id: string;
  label: string;
  status: ComprehensionUnitStatus | string | null;
  /** An exact excerpt of the learner's answer. */
  evidence: string | null;
  why: string | null;
  /** One concrete consequence of applying this mental model to a new case. */
  consequence?: string | null;
  /** An exact excerpt of the explanation/source, never a paraphrase. */
  source_quote: string | null;
}

export interface ComprehensionGap {
  unit_id: string;
  label: string;
  kind: string;
  why: string;
  /** The learner's exact words that show the discrepancy. */
  evidence: string;
  /** An exact excerpt of the explanation/source that settles it. */
  source_quote: string;
  /** What would go wrong if this reasoning were applied. */
  consequence: string;
}

export interface ComprehensionReteach {
  unit_id: string;
  title: string;
  why_wrong: string;
  explanation: string;
  example: string | null;
  source_quote: string | null;
}

export interface ComprehensionNext {
  /** question | reteach | done */
  kind: string;
  question: string | null;
  reteach: ComprehensionReteach | null;
}

export interface ComprehensionVerdict {
  summary: string;
  score: number;
  units: ComprehensionUnit[];
  gaps: ComprehensionGap[];
  target_unit_id: string | null;
  target_status: ComprehensionUnitStatus | string | null;
  next_review_at: string | null;
  interval_days: number | null;
}

export interface ComprehensionStart {
  session_id: string;
  topic_id: string;
  status: string;
  step: number;
  max_steps: number;
  question: string;
  resumed: boolean;
  score?: number;
  verdict?: ComprehensionVerdict | null;
  next?: ComprehensionNext | null;
}

export interface ComprehensionTurn {
  session_id: string;
  topic_id: string;
  status: string;
  step: number;
  max_steps: number;
  score: number;
  verdict: ComprehensionVerdict | null;
  next: ComprehensionNext | null;
}

export interface ComprehensionLatest {
  checked: boolean;
  has_explanation: boolean;
  session_id: string | null;
  status: string | null;
  score: number | null;
  summary: string | null;
  gaps: ComprehensionGap[];
  next_review_at: string | null;
  interval_days: number | null;
  completed_at: string | null;
}

export async function startComprehension(
  topicId: string,
  language = getLocale(),
): Promise<ComprehensionStart> {
  return request(`/learning/topics/${topicId}/comprehension/start`, {
    method: "POST",
    body: JSON.stringify({ language }),
  });
}

export async function answerComprehension(
  topicId: string,
  sessionId: string,
  text: string,
  language = getLocale(),
): Promise<ComprehensionTurn> {
  return request(
    `/learning/topics/${topicId}/comprehension/${sessionId}/answer`,
    {
      method: "POST",
      body: JSON.stringify({ text, language }),
    },
  );
}

export async function getLatestComprehension(
  topicId: string,
): Promise<ComprehensionLatest> {
  return request(`/learning/topics/${topicId}/comprehension/latest`);
}

/** What the bulk explanation run did, and what it left alone. */
export interface ExplainMissingResult {
  queued: number;
  already_explained: number;
  message: string;
}

/** One passage of the learner's uploaded literature. */
export interface MaterialPassage {
  node_id: string;
  title: string | null;
  source_file: string | null;
  level: number | null;
  matched_terms: string[];
  excerpt: string;
}

/**
 * What a topic can be explained from.
 *
 * `course_has_material` separates "nothing matched this topic" from "you have
 * uploaded nothing" — the same empty list, and opposite things to do about it.
 */
export interface MaterialCandidates {
  topic_id: string;
  course_id: string | null;
  source_node_id: string | null;
  course_has_material: boolean;
  automatic: MaterialPassage[];
  source_label: string;
}

/** Passages that match this topic, and the one it is pinned to. */
export async function getTopicMaterial(
  topicId: string,
): Promise<MaterialCandidates> {
  return request(`/learning/topics/${topicId}/material`);
}

/** One uploaded file offered as a source for the explanation. */
export interface TopicSourceFile {
  name: string;
  nodes: number;
}

export interface TopicSources {
  topic_id: string;
  course_id: string | null;
  files: TopicSourceFile[];
}

/**
 * The course's uploaded files, for the generation-time source picker.
 *
 * A file is a unit the learner recognises ("the statistics book"); picking one
 * replaces the automatic keyword match, which cannot tell a Python method name
 * from the same word in an unrelated English textbook.
 */
export async function getTopicSources(topicId: string): Promise<TopicSources> {
  return request(`/learning/topics/${topicId}/sources`);
}

/**
 * Pin a topic to one passage, or pass an empty id to go back to matching.
 *
 * The pin is checked against the course server-side, so an id from elsewhere is
 * rejected rather than silently accepted.
 */
export async function setTopicSource(
  topicId: string,
  sourceNodeId: string | null,
): Promise<LearningTopic> {
  return request(`/learning/topics/${topicId}`, {
    method: "PATCH",
    body: JSON.stringify({ source_node_id: sourceNodeId ?? "" }),
  });
}

/** A video link the learner put beside this topic. */
export interface TopicResource {
  id: string;
  topic_id: string;
  kind: string;
  url: string;
  title: string | null;
  note: string | null;
}

export async function listTopicResources(topicId: string): Promise<TopicResource[]> {
  return request(`/learning/topics/${topicId}/resources`);
}

/**
 * Store a link the learner supplied.
 *
 * There is no search behind this and that is deliberate: a model asked for a
 * video URL invents a well-formed address for a video that does not exist, and
 * the learner only finds out by clicking something that looked trustworthy.
 */
export async function addTopicResource(
  topicId: string,
  payload: { url: string; title?: string; note?: string },
): Promise<TopicResource> {
  return request(`/learning/topics/${topicId}/resources`, {
    method: "POST",
    body: JSON.stringify({ ...payload, kind: "video" }),
  });
}

export async function deleteTopicResource(
  topicId: string,
  resourceId: string,
): Promise<void> {
  return request(
    `/learning/topics/${topicId}/resources/${resourceId}`,
    { method: "DELETE" },
  );
}

/**
 * Queue an explanation for every topic of the goal that has none.
 *
 * Returns as soon as the work is queued: one explanation takes seconds and a
 * goal can hold a dozen topics, so awaiting them all would hold the connection
 * open for minutes. Poll {@link listTopics} to see them arrive.
 */
export async function explainMissingTopics(
  goalId: string,
  style = "detailed",
): Promise<ExplainMissingResult> {
  return request(`/learning/goals/${goalId}/topics/explain-missing`, {
    method: "POST",
    body: JSON.stringify({ style, language: getLocale() }),
  });
}

// ── Plan (TZ /goals/{id}/plan) ──

export type PlanItemType = "study" | "review" | "practice" | "exam";
export type PlanItemStatus = "pending" | "done" | "skipped" | "missed";

export interface PlanItem {
  id: string;
  topic_id: string | null;
  title: string;
  item_type: PlanItemType;
  day: string;
  planned_minutes: number;
  actual_minutes: number | null;
  status: PlanItemStatus;
  reason: string | null;
  rejected: boolean;
}

export interface Plan {
  goal_id: string;
  days: Record<string, PlanItem[]>;
  total_minutes: number;
  available_minutes: number;
  feasible: boolean;
  speed: number;
  warnings: string[];
  suggestions: string[];
}

export async function getPlan(goalId: string): Promise<Plan> {
  return request(`/learning/goals/${goalId}/plan`);
}

/** U7: replan. Deterministic server-side, so it works without an LLM. */
export async function buildPlan(
  goalId: string,
  options?: { days?: number; minutes_per_day?: number; speed?: number; mode?: string },
): Promise<Plan> {
  return request(`/learning/goals/${goalId}/plan`, {
    method: "POST",
    body: JSON.stringify(options ?? {}),
  });
}

/** FR-10.5: gentler plan after a gap. */
export async function catchUpPlan(
  goalId: string,
  options?: { days?: number; minutes_per_day?: number; mode?: string },
): Promise<Plan> {
  return request(`/learning/goals/${goalId}/plan/catch-up`, {
    method: "POST",
    body: JSON.stringify(options ?? {}),
  });
}

export interface PullAheadResult {
  /** The copy on the target day, keeping the original full time. */
  pulled: PlanItem;
  /** The original item, now halved (minimum 5 minutes) on its own day. */
  original: PlanItem;
}

/**
 * Take one task from a future day to today.
 *
 * The original stays where it was with half the time (never below 5 minutes),
 * so the future day still contains a short consolidation. Practice/control
 * assignments can be taken this way; the final exam cannot.
 */
export async function pullPlanItemAhead(
  goalId: string,
  itemId: string,
  targetDay?: string,
): Promise<PullAheadResult> {
  return request(`/learning/goals/${goalId}/plan/${itemId}/pull-ahead`, {
    method: "POST",
    body: JSON.stringify({ target_day: targetDay ?? null }),
  });
}

/** FR-6.6 / FR-10.4: mark done, reschedule, or reject a change. */
export async function updatePlanItem(
  goalId: string,
  itemId: string,
  payload: {
    status?: PlanItemStatus;
    day?: string;
    planned_minutes?: number;
    rejected?: boolean;
  },
): Promise<PlanItem> {
  return request(`/learning/goals/${goalId}/plan/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function appendTopicToPlan(
  goalId: string,
  itemId: string,
  topicId: string,
  payload?: { day?: string; minutes?: number },
): Promise<PlanItem> {
  return request(`/learning/goals/${goalId}/plan/${itemId}/add-topic`, {
    method: "POST",
    body: JSON.stringify({ topic_id: topicId, ...(payload ?? {}) }),
  });
}

/** FR-15.1: download the plan as a calendar file. */
export async function downloadPlanIcs(goalId: string): Promise<{ blob: Blob; fileName: string }> {
  const { blob, fileName } = await requestBlob(`/learning/goals/${goalId}/export.ics`);
  return { blob, fileName: fileName ?? `opentutor-plan-${goalId}.ics` };
}

// ── Diagnostic (FR-4) ──

export interface DiagnosticTurn {
  session_id: string;
  turn_index: number;
  subtopic_id: string;
  subtopic_title: string;
  question: string;
  technique: string | null;
  difficulty: number;
  done: boolean;
}

export interface DiagnosticEstimate {
  id: string;
  title: string;
  estimate: number;
  uncertainty: number;
  probes?: number;
  band: string;
}

export interface DiagnosticState {
  session_id: string;
  status: "in_progress" | "completed" | "skipped";
  reply_count: number;
  finished_reason: string | null;
  estimates: DiagnosticEstimate[];
  aggregate_uncertainty: number;
}

export async function startDiagnostic(
  goalId: string,
  options?: { subtopic_ids?: string[]; max_replies?: number },
): Promise<DiagnosticTurn> {
  return request(`/learning/goals/${goalId}/diagnostic/start`, {
    method: "POST",
    body: JSON.stringify(options ?? {}),
  });
}

export async function replyToDiagnostic(
  goalId: string,
  sessionId: string,
  text: string,
): Promise<DiagnosticTurn> {
  return request(`/learning/goals/${goalId}/diagnostic/${sessionId}/reply`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export async function getDiagnosticState(
  goalId: string,
  sessionId: string,
): Promise<DiagnosticState> {
  return request(`/learning/goals/${goalId}/diagnostic/${sessionId}`);
}

/** FR-4.5: the learner corrects the inferred level. */
export async function correctDiagnostic(
  goalId: string,
  sessionId: string,
  estimates: Record<string, number>,
): Promise<DiagnosticState> {
  return request(`/learning/goals/${goalId}/diagnostic/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ estimates }),
  });
}

// ── Questions and answers (FR-8) ──

/**
 * What one check on a code task looks like from the learner's side.
 *
 * `expected` is the specification of what the function must return, not the answer
 * to the task — the answer is the code. The reference solution is never sent.
 */
export interface TaskTest {
  name: string;
  call: string;
  expected: string;
}

/** Per-format data. Never carries the correct answer; see the API for why. */
export interface TaskPayload {
  options?: string[];
  items?: string[];
  left?: string[];
  right?: string[];
  unit?: string;
  hint_text?: string;
  tolerance?: number | null;
  language?: string;
  starter_code?: string;
  tests?: TaskTest[];
}

export interface Question {
  id: string;
  topic_id: string;
  topic_title: string | null;
  /** The pedagogical intent: find the error, apply it, compare. */
  question_type: string;
  /** How the learner replies. A different axis from question_type. */
  format: string;
  text: string;
  criteria: Record<string, string> | null;
  hints: string[];
  difficulty: number;
  source_label: string | null;
  payload: TaskPayload | null;
}

export type ConfidenceLabel = "confident" | "unsure" | "guessing";

export interface AnswerResult {
  id: string;
  question_id: string;
  /** What the learner achieved. */
  raw_score: number;
  final_score: number;
  hint_penalty: number;
  breakdown: Record<string, number>;
  feedback: string | null;
  error_types: string[];
  next_review_at: string | null;
  interval_days: number | null;
  topic_status: TopicStatus | null;
  /**
   * What the memory system learned: the score after the learner's own confidence
   * was taken into account. Lower than `final_score` for a right answer they were
   * guessing at. Absent when the backend predates it.
   */
  mastery_score?: number | null;
  confidence_label?: string | null;
  /** The right answer for a task answered wrongly, so it can be fixed. */
  expected_answer?: string | null;
  /**
   * True when the verdict came from comparing values rather than from a model.
   * "The system checked this itself" and "a model read this" are different claims
   * and the UI says which one happened.
   */
  checked_without_model?: boolean;
}

export async function generateQuestion(
  topicId: string,
  options?: { difficulty?: number; language?: string; format?: string },
): Promise<Question> {
  return request(`/learning/topics/${topicId}/question`, {
    method: "POST",
    body: JSON.stringify({ language: getLocale(), ...options }),
  });
}

/** FR-8.6: escalating hints. Requesting one lowers the grade. */
export async function getHint(questionId: string, level: 1 | 2 | 3): Promise<string> {
  const res = await request<{ level: number; hint: string }>(
    `/learning/questions/${questionId}/hints/${level}`,
  );
  return res.hint;
}

export async function submitAnswer(
  questionId: string,
  payload: {
    text: string;
    /** The structured answer for the non-prose formats; null for prose. */
    value?: unknown;
    /**
     * What the learner's own browser established by running their code. Only the
     * pass/fail per named check is used server-side — the output text is not read,
     * so a client cannot buy a passing grade by inventing a clean run.
     */
    run_results?: { name: string; passed: boolean }[];
    /** A photo of a handwritten solution as a base64 data URL. When present the
     * answer is graded by looking at the image rather than reading the text. */
    image?: string;
    confidence?: ConfidenceLabel;
    hints_used?: number;
    answer_time_ms?: number;
  },
): Promise<AnswerResult> {
  return request(`/learning/questions/${questionId}/answer`, {
    method: "POST",
    body: JSON.stringify({ language: getLocale(), ...payload }),
  });
}

/** FR-8.7: challenge a grade. */
export async function disputeAnswer(answerId: string, note: string): Promise<AnswerResult> {
  return request(`/learning/answers/${answerId}/dispute`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
}

// ── Review and exam (U6, U8) ──

export async function getNextReviewQuestion(goalId: string): Promise<Question> {
  return request(`/learning/goals/${goalId}/review`, { method: "POST" });
}

// ── Unified global review / inbox (FR-9.3) ──────────────────────────

export interface DueReviewItem {
  type: "review" | "wrong_answer" | "plan_item" | "video";
  goal_id: string;
  goal_title: string;
  topic_id: string | null;
  topic_title: string | null;
  title: string;
  due_at: string | null;
  day: string | null;
  minutes: number | null;
  reason: string | null;
  status: string | null;
  action: string | null;
  action_href: string | null;
}

export interface DueReviewResponse {
  date: string;
  total: number;
  items: DueReviewItem[];
}

/** All items needing attention across every goal of the learner. */
export async function getDueReview(): Promise<DueReviewResponse> {
  return request("/learning/review/due");
}

export interface ExamTopic {
  topic_id: string;
  title: string;
  strength: number;
  focus: "weak" | "reinforce";
}

export interface ExamComposition {
  goal_id: string;
  weak_share: number;
  topics: ExamTopic[];
  suggested_questions: number;
}

export async function planExam(goalId: string): Promise<ExamComposition> {
  return request(`/learning/goals/${goalId}/exam`, { method: "POST" });
}

// ── Taking the exam (FR-11) ──

export type ExamMode = "training" | "strict";

export interface ExamQuestion {
  exam_id: string;
  index: number;
  total: number;
  question_id: string;
  topic_id: string;
  topic_title: string | null;
  text: string;
  question_type: string;
  hints_allowed: boolean;
  seconds_left: number | null;
  done: boolean;
}

export interface ExamAnswer {
  exam_id: string;
  question_id: string;
  raw_score: number;
  final_score: number;
  hint_penalty: number;
  feedback: string | null;
  error_types: string[];
  /** Strict mode withholds the grade until the exam is finished. */
  revealed: boolean;
  done: boolean;
  next_question: ExamQuestion | null;
}

export interface ExamResult {
  exam_id: string;
  mode: ExamMode;
  status: string;
  total_score: number;
  answered: number;
  total_questions: number;
  by_topic: { topic_id: string; title: string; score: number }[];
  weak_topics: string[];
  recommendations: string[];
  suggestion: string | null;
  /** True when this result closed the goal. */
  goal_completed: boolean;
  pass_threshold: number;
}

export async function startExam(
  goalId: string,
  mode: ExamMode = "training",
  force = false,
): Promise<ExamQuestion> {
  return request(`/learning/goals/${goalId}/exam/start`, {
    method: "POST",
    body: JSON.stringify({ mode, force }),
  });
}

export async function getExamQuestion(
  goalId: string,
  examId: string,
  index = 0,
): Promise<ExamQuestion> {
  return request(
    `/learning/goals/${goalId}/exam/${examId}?index=${index}`,
  );
}

export async function answerExamQuestion(
  goalId: string,
  examId: string,
  index: number,
  payload: { text: string; hints_used?: number },
): Promise<ExamAnswer> {
  return request(
    `/learning/goals/${goalId}/exam/${examId}/answer?index=${index}`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function getExamResult(
  goalId: string,
  examId: string,
): Promise<ExamResult> {
  return request(`/learning/goals/${goalId}/exam/${examId}/result`);
}

// ── Progress (FR-12) ──

export type TopicStatus = "not_started" | "weak" | "learning" | "known";

export interface TopicProgress {
  topic_id: string;
  title: string;
  strength: number;
  status: TopicStatus;
  interval_days: number;
  next_review_at: string | null;
  repetitions: number;
  lapses: number;
}

export interface Forecast {
  level: "high" | "medium" | "low";
  probability: number;
  reasons: string[];
  required_speed: number;
  feasible: boolean;
}

export interface ErrorSummary {
  window: number;
  counts: Record<string, number>;
  mean_score: number;
  dominant_error: string;
  observation: string;
}

export interface Progress {
  goal_id: string;
  topic_count: number;
  topics: TopicProgress[];
  by_status: Record<string, number>;
  forecast: Forecast;
  error_summary: ErrorSummary | null;
}

export async function getProgress(goalId: string): Promise<Progress> {
  return request(`/learning/goals/${goalId}/progress`);
}

// ── FR-12.1: strength over time ──

export interface StrengthPoint {
  at: string | null;
  strength: number;
  interval_days: number;
  was_lapse: boolean;
}

export interface AverageStrengthPoint {
  at: string;
  strength: number;
  samples: number;
  lapses: number;
}

export interface TopicStrengthSeries {
  topic_id: string;
  title: string;
  points: StrengthPoint[];
  current: number;
}

export interface StrengthHistory {
  goal_id: string;
  overall: AverageStrengthPoint[];
  by_topic: TopicStrengthSeries[];
  total_points: number;
  topic_count: number;
}

export async function getStrengthHistory(
  goalId: string,
  options: { topicId?: string; days?: number } = {},
): Promise<StrengthHistory> {
  const params = new URLSearchParams();
  if (options.topicId) params.set("topic_id", options.topicId);
  if (options.days) params.set("days", String(options.days));
  const query = params.toString();
  return request(
    `/learning/goals/${goalId}/progress/strength${query ? `?${query}` : ""}`,
  );
}

// ── FR-12.3: error analysis ──

export interface WeakTopicStat {
  topic_id: string;
  title: string;
  mean_score: number;
  answers: number;
  dominant_error: string;
  weak: boolean;
}

export interface ErrorStats {
  window: number;
  counts: Record<string, number>;
  mean_score: number;
  dominant_error: string;
  observation: string;
  total?: number;
  by_topic?: WeakTopicStat[];
  weak_topics?: string[];
  confusions?: string[];
}

export interface StoredErrorSummary {
  id: string;
  goal_id: string;
  period_start: string | null;
  period_end: string | null;
  summary_text: string | null;
  recommendations: string[];
  answer_count: number;
  stats: ErrorStats;
  generated_at: string | null;
}

export interface ErrorAnalysis {
  goal_id: string;
  stats: ErrorStats;
  summaries: StoredErrorSummary[];
  due: boolean;
}

export async function getErrorAnalysis(goalId: string): Promise<ErrorAnalysis> {
  return request(`/learning/goals/${goalId}/progress/errors`);
}

/** FR-12.3: write a summary now instead of waiting for the next N answers. */
export async function regenerateErrorSummary(
  goalId: string,
): Promise<StoredErrorSummary> {
  return request(`/learning/goals/${goalId}/progress/errors`, { method: "POST" });
}

/** FR-12.2: filtered answer history. */
export async function getAnswerHistory(
  goalId: string,
  filters?: {
    topic_id?: string;
    min_score?: number;
    max_score?: number;
    error_type?: string;
    disputed?: boolean;
    limit?: number;
  },
): Promise<AnswerResult[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters ?? {})) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const query = params.toString();
  return request(`/learning/goals/${goalId}/answers${query ? `?${query}` : ""}`);
}

// ── Dependency graph (FR-5.5) ──

export interface GraphNode {
  id: string;
  title: string;
  status: TopicStatus;
  strength: number;
}

export interface TopicGraph {
  goal_id: string;
  nodes: GraphNode[];
  edges: { prerequisite: string; topic: string; weight: number }[];
  order: string[];
  cycle: string[] | null;
}

export async function getTopicGraph(goalId: string): Promise<TopicGraph> {
  return request(`/learning/goals/${goalId}/graph`);
}

// ── Profile (FR-1.2, FR-1.3) ──

export interface Profile {
  timezone: string;
  language: string;
  preferred_study_time: string | null;
  notifications_enabled: boolean;
  push_enabled: boolean;
  email_enabled: boolean;
  quiet_hours_start: number | null;
  quiet_hours_end: number | null;
  reminder_interval_days: number;
  missed_reminders: number;
  gamification_enabled: boolean;
}

export async function getProfile(): Promise<Profile> {
  return request("/learning/profile");
}

export async function updateProfile(payload: Partial<Profile>): Promise<Profile> {
  return request("/learning/profile", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}