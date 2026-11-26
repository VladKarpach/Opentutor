import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * The `silent` call option.
 *
 * Background: `request` raises a global error toast before throwing, so a caller
 * that deliberately falls back on failure (`.catch(...)`) still flashed a scary
 * toast. That is what put "LECTOR semantic review is experimental…" on screen
 * even though every call site handled the error correctly.
 */

const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: { error: (...args: unknown[]) => toastError(...args) },
}));

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

vi.mock("@/lib/auth", () => ({
  buildAuthHeaders: () => new Headers(),
}));

import { request, ApiError } from "./client";

function notFound(detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status: 404,
    headers: { "Content-Type": "application/json" },
  });
}

const LECTOR_MESSAGE = "LECTOR semantic review is experimental. Set ENABLE_EXPERIMENTAL_LECTOR=true to enable.";

describe("request silent option", () => {
  beforeEach(() => {
    toastError.mockReset();
    mockFetch.mockReset();
  });

  it("shows a toast by default on a failed request", async () => {
    mockFetch.mockResolvedValue(notFound(LECTOR_MESSAGE));

    await expect(request("/progress/courses/x/review-session")).rejects.toThrow(ApiError);

    expect(toastError).toHaveBeenCalledTimes(1);
  });

  it("suppresses the toast when silent is set", async () => {
    mockFetch.mockResolvedValue(notFound(LECTOR_MESSAGE));

    await expect(
      request("/progress/courses/x/review-session", undefined, { silent: true }),
    ).rejects.toThrow(ApiError);

    expect(toastError).not.toHaveBeenCalled();
  });

  it("still throws when silent, so the caller fallback runs", async () => {
    mockFetch.mockResolvedValue(notFound(LECTOR_MESSAGE));

    const result = await request(
      "/progress/courses/x/review-session",
      undefined,
      { silent: true },
    ).catch(() => "fallback");

    expect(result).toBe("fallback");
  });

  it("keeps the error message intact for the caller", async () => {
    mockFetch.mockResolvedValue(notFound(LECTOR_MESSAGE));

    await expect(
      request("/progress/courses/x/review-session", undefined, { silent: true }),
    ).rejects.toThrow(LECTOR_MESSAGE);
  });

  it("does not suppress toasts on success", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ count: 0, items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await request("/progress/courses/x/review-session", undefined, { silent: true });

    expect(toastError).not.toHaveBeenCalled();
  });
});

describe("getReviewSession and LECTOR ordering are quiet", () => {
  it("review-session is fetched silently", async () => {
    const { getReviewSession } = await import("./progress");
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ course_id: "c", count: 0, items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await getReviewSession("c");
    expect(toastError).not.toHaveBeenCalled();
  });
});