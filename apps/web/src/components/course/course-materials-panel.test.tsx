import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { render, screen } from "@/test-utils";

import { CourseMaterialsPanel } from "./course-materials-panel";

/**
 * Uploading a file that is already in the course is a no-op on the server, but
 * the screen still has to leave its upload state. The first version only reset
 * the busy flag on failure, so a duplicate (or any successful upload) left the
 * button disabled and stopped the refresh interval — the panel looked stuck.
 */

const { listCourseFiles, uploadFile, scrapeUrl } = vi.hoisted(() => ({
  listCourseFiles: vi.fn(),
  uploadFile: vi.fn(),
  scrapeUrl: vi.fn(),
}));

vi.mock("@/lib/api/courses", () => ({
  listCourseFiles,
  uploadFile,
  scrapeUrl,
}));

vi.mock("@/lib/i18n-context", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

describe("CourseMaterialsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listCourseFiles.mockResolvedValue([]);
    uploadFile.mockResolvedValue({ nodes_created: 0, duplicate: true });
  });

  it("reports an already-uploaded file and releases the upload control", async () => {
    render(<CourseMaterialsPanel courseId="c1" />);

    const input = await screen.findByTestId("materials-file-input");
    const file = new File(["same bytes"], "book.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("materials.alreadyUploaded", {
        description: "book.pdf",
      }),
    );

    // The regression: `busy` stayed true on the success path, so the button
    // remained disabled and the list stopped polling.
    await waitFor(() => {
      const button = screen.getByText("materials.upload").closest("button");
      expect(button?.disabled).toBe(false);
    });

    // The list is refreshed immediately instead of waiting for the next tick.
    expect(listCourseFiles).toHaveBeenCalledTimes(2);
  });
});
