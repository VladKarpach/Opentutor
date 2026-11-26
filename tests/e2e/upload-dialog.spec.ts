import { expect, test } from "@playwright/test";
import {
  skipOnboarding,
  SAMPLE_COURSE_MD,
  SAMPLE_COURSE_2_MD,
} from "./helpers/test-utils";

/**
 * Content upload tests.
 *
 * /new is now a name-only form, so the upload UI lives in the guided setup's
 * content step (and, after setup, in the course materials panel). These tests
 * cover the setup content step.
 */

test.describe.serial("File upload", () => {
  test.beforeEach(async ({ page }) => {
    await skipOnboarding(page);
    await page.goto("/new");
    await expect(page.getByTestId("setup-file-input")).toBeAttached({ timeout: 15_000 });
  });

  test("upload dropzone is visible", async ({ page }) => {
    await expect(page.getByTestId("setup-dropzone")).toBeVisible();
  });

  test("file input exists with correct accept types", async ({ page }) => {
    const fileInput = page.getByTestId("setup-file-input");
    await expect(fileInput).toBeAttached();
    const accept = await fileInput.getAttribute("accept");
    expect(accept).toContain(".pdf");
    expect(accept).toContain(".md");
    expect(accept).toContain(".txt");
  });

  test("file can be selected for upload", async ({ page }) => {
    await page.getByTestId("setup-file-input").setInputFiles(SAMPLE_COURSE_MD);
    // The file name should appear somewhere after selection
    await expect(page.getByText(/sample-course/i)).toBeVisible({ timeout: 15_000 });
  });

  test("a second file can be added", async ({ page }) => {
    await page.getByTestId("setup-file-input").setInputFiles([SAMPLE_COURSE_MD, SAMPLE_COURSE_2_MD]);
    await expect(page.getByText(/sample-course-2/i)).toBeVisible({ timeout: 15_000 });
  });
});

test.describe.serial("URL input", () => {
  test.beforeEach(async ({ page }) => {
    await skipOnboarding(page);
    await page.goto("/new");
    await expect(page.getByTestId("setup-tab-url")).toBeVisible({ timeout: 15_000 });
    await page.getByTestId("setup-tab-url").click();
  });

  test("URL input is visible on the URL tab", async ({ page }) => {
    await expect(page.getByTestId("setup-url-input")).toBeVisible();
  });

  test("URL input accepts text", async ({ page }) => {
    const urlInput = page.getByTestId("setup-url-input");
    await urlInput.fill("https://example.com/test");
    await expect(urlInput).toHaveValue("https://example.com/test");
  });

  test("autoscrape toggle is visible", async ({ page }) => {
    await expect(page.getByTestId("setup-autoscrape-toggle")).toBeVisible();
  });
});
