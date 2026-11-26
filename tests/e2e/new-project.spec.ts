import { expect, test } from "@playwright/test";
import { skipOnboarding } from "./helpers/test-utils";

/**
 * New Space page tests.
 *
 * /new is intentionally a single-field form: returning users only name the
 * space and land in the workspace. Materials, mode and features are configured
 * from inside the course (/course/[id]), so this page must not grow a wizard
 * back.
 */

test.describe("New Space form", () => {
  test.beforeEach(async ({ page }) => {
    await skipOnboarding(page);
    await page.goto("/new");
    await expect(page.getByTestId("project-name-input")).toBeVisible({ timeout: 15_000 });
  });

  test("asks only for a name", async ({ page }) => {
    await expect(page.getByTestId("project-name-input")).toBeVisible();
    await expect(page.getByTestId("create-space")).toBeVisible();
    // The old upload → parse wizard is gone from this page.
    await expect(page.getByTestId("upload-dropzone")).toHaveCount(0);
    await expect(page.getByTestId("project-file-input")).toHaveCount(0);
    await expect(page.getByTestId("project-url-input")).toHaveCount(0);
  });

  test("project name input accepts text", async ({ page }) => {
    const input = page.getByTestId("project-name-input");
    await input.fill("My Test Project");
    await expect(input).toHaveValue("My Test Project");
  });

  test("empty name shows an error and stays on the page", async ({ page }) => {
    await page.getByTestId("create-space").click();
    await expect(page.getByTestId("project-name-error")).toBeVisible();
    await expect(page).toHaveURL(/\/new/, { timeout: 5_000 });
  });

  test("Back button navigates to dashboard", async ({ page }) => {
    await page.getByTestId("new-back-mode").click();
    await expect(page).toHaveURL("/", { timeout: 15_000 });
  });

  test("creating a space opens its workspace", async ({ page }) => {
    test.setTimeout(60_000);
    await page.getByTestId("project-name-input").fill(`Name Only ${Date.now()}`);
    await page.getByTestId("create-space").click();
    await expect(page).toHaveURL(/\/course\//, { timeout: 30_000 });
  });

  test("created space appears on the dashboard", async ({ page }) => {
    test.setTimeout(60_000);
    const name = `Dashboard Visible ${Date.now()}`;
    await page.getByTestId("project-name-input").fill(name);
    await page.getByTestId("create-space").click();
    await expect(page).toHaveURL(/\/course\//, { timeout: 30_000 });

    await page.goto("/");
    await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 });
  });

  test("Enter submits the form", async ({ page }) => {
    test.setTimeout(60_000);
    await page.getByTestId("project-name-input").fill(`Enter Key ${Date.now()}`);
    await page.getByTestId("project-name-input").press("Enter");
    await expect(page).toHaveURL(/\/course\//, { timeout: 30_000 });
  });
});
