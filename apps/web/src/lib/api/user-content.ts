/**
 * API client for user-owned content blocks.
 *
 * Theory links (documentation URLs) and task files the learner uploads
 * themselves — scoped to (user, course), never agent-generated.
 */

import { API_BASE, buildSecureHeaders, buildSecureRequestInit, parseApiError, request, requestBlob } from "./client";

export interface TheoryLink {
  id: string;
  course_id: string;
  title: string;
  url: string;
  description: string | null;
  source_type: string;
  created_at: string | null;
}

export interface TaskFile {
  id: string;
  course_id: string | null;
  goal_id: string | null;
  filename: string;
  mime_type: string | null;
  size_bytes: number | null;
  created_at: string | null;
}

export interface TaskSubmission {
  id: string;
  task_file_id: string;
  verdict: string;
  score: number;
  feedback: string | null;
  error_category: string | null;
  submitted_at: string | null;
  remedial_task: { question: string; correct_answer?: string; explanation?: string; core_concept?: string } | null;
  block_completed: boolean;
}

/** List the learner's pinned theory links for a course, newest first. */
export async function listTheoryLinks(courseId: string): Promise<TheoryLink[]> {
  const qs = new URLSearchParams({ course_id: courseId }).toString();
  return request<TheoryLink[]>(`/user-content/theory?${qs}`);
}

/** Pin a documentation / theory URL to a course. */
export async function addTheoryLink(
  courseId: string,
  payload: { title: string; url: string; description?: string },
): Promise<TheoryLink> {
  return request<TheoryLink>("/user-content/theory", {
    method: "POST",
    body: JSON.stringify({ course_id: courseId, ...payload }),
  });
}

/** Remove one of the learner's pinned theory links. */
export async function deleteTheoryLink(theoryId: string): Promise<void> {
  await request<void>(`/user-content/theory/${theoryId}`, { method: "DELETE" });
}

/** Upload a task / assignment file for a learning goal. */
export async function uploadTaskFile(
  goalId: string,
  file: File,
): Promise<TaskFile> {
  const form = new FormData();
  form.append("file", file);
  form.append("goal_id", goalId);

  const res = await fetch(`${API_BASE}/user-content/tasks`, {
    ...buildSecureRequestInit({
      method: "POST",
      includeJsonContentType: false,
      body: form,
    }),
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

/** List the learner's uploaded task files for a goal, newest first. */
export async function listTaskFiles(goalId: string): Promise<TaskFile[]> {
  const qs = new URLSearchParams({ goal_id: goalId }).toString();
  return request<TaskFile[]>(`/user-content/tasks?${qs}`);
}

/** Remove one of the learner's uploaded task files. */
export async function deleteTaskFile(taskId: string): Promise<void> {
  await request<void>(`/user-content/tasks/${taskId}`, { method: "DELETE" });
}

/** Download / preview an uploaded task file. */
export async function downloadTaskFile(taskId: string): Promise<{ blob: Blob; fileName: string | null; contentType: string }> {
  return requestBlob(`/user-content/tasks/${taskId}/file`);
}

/** Submit an answer to a task file and receive AI grading + optional remedial task. */
export async function submitTaskAnswer(
  taskId: string,
  payload: { user_answer: string },
): Promise<TaskSubmission> {
  return request<TaskSubmission>(`/user-content/tasks/${taskId}/submit`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** List submissions for a task file, newest first. */
export async function listTaskSubmissions(taskId: string): Promise<TaskSubmission[]> {
  const qs = new URLSearchParams().toString();
  return request<TaskSubmission[]>(`/user-content/tasks/${taskId}/submissions`);
}
