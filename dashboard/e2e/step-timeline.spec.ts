/**
 * E2E: Step timeline on the task detail page.
 *
 * Covers:
 *  - Steps tab appears when task has steps, defaults to active
 *  - StepTimeline renders with screenshot placeholder and playback controls
 *  - Prev/next navigation updates the step counter and detail panel
 *  - Timeline bar segments are clickable and highlight current step
 *  - Replay tab is still accessible alongside Steps
 *  - Graceful handling when steps endpoint returns empty array
 */

import {
  test,
  expect,
  mockTaskDetail,
  mockTaskReplay,
  mockTaskSteps,
  COMPLETED_TASK_FULL,
  COMPLETED_TASK_MINIMAL,
  SAMPLE_STEPS,
} from "./fixtures";

// Helper: navigate to a task detail page with all mocks in place
async function gotoTaskWithSteps(page: import("@playwright/test").Page) {
  await mockTaskDetail(page, COMPLETED_TASK_FULL);
  await mockTaskReplay(page, COMPLETED_TASK_FULL.task_id);
  await mockTaskSteps(page, COMPLETED_TASK_FULL.task_id, SAMPLE_STEPS);
  await page.goto(`/tasks/${COMPLETED_TASK_FULL.task_id}`);
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

test.describe("steps and replay tabs", () => {
  test("Steps tab is visible and active by default when task has steps", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);

    // Tab triggers
    const stepsTab = page.getByRole("tab", { name: /Steps/ });
    const replayTab = page.getByRole("tab", { name: "Replay" });

    await expect(stepsTab).toBeVisible();
    await expect(replayTab).toBeVisible();

    // Steps tab shows the count
    await expect(stepsTab).toHaveText(/Steps \(5\)/);
  });

  test("clicking Replay tab shows the replay viewer", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);

    // Click Replay tab
    await page.getByRole("tab", { name: "Replay" }).click();

    // Replay iframe or "No replay" should be visible
    // With our mock, replay URL is returned, so iframe should be present
    await expect(page.locator("iframe[title='Task replay']")).toBeVisible();
  });

  test("Steps tab not shown when task has 0 steps", async ({
    authedPage: page,
  }) => {
    const noStepsTask = { ...COMPLETED_TASK_FULL, task_id: "ff000000-0000-7000-8000-000000000099", steps: 0 };
    await mockTaskDetail(page, noStepsTask);
    await mockTaskReplay(page, noStepsTask.task_id);

    await page.goto(`/tasks/${noStepsTask.task_id}`);

    // Should NOT have a Steps tab
    await expect(page.getByRole("tab", { name: /Steps/ })).not.toBeVisible();
    // Replay tab/content should still render
    await expect(page.locator("iframe[title='Task replay']")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// StepTimeline rendering
// ---------------------------------------------------------------------------

test.describe("step timeline rendering", () => {
  test("shows step counter starting at step 1", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);
    await expect(page.getByText("Step 1 / 5")).toBeVisible();
  });

  test("shows action type badge for the first step", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);
    // Step 1 is action_type: "navigate"
    await expect(
      page.locator('[data-slot="badge"]').filter({ hasText: "Navigate" })
    ).toBeVisible();
  });

  test("shows 'No screenshot captured' placeholder for steps without screenshots", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);
    // Step 1 has screenshot_url: null
    await expect(page.getByText("No screenshot captured")).toBeVisible();
  });

  test("renders timeline bar with correct number of segments", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);
    // Timeline bar segments — each is a tooltip trigger inside the group
    const segments = page.locator('[role="group"][aria-label="Step timeline"] [data-slot="tooltip-trigger"]');
    await expect(segments).toHaveCount(5);
  });

  test("shows description text for the current step", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);
    await expect(page.getByText("Navigate to example.com homepage")).toBeVisible();
  });

  test("shows token counts for the current step", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);
    // Step 1: tokens_in=300, tokens_out=150 → "↑300 ↓150"
    await expect(page.getByText(/↑300\s*↓150/)).toBeVisible();
  });

  test("shows keyboard shortcut hint", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);
    await expect(page.getByText(/arrow keys.*play\/pause/i)).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

test.describe("step navigation", () => {
  test("next button advances to step 2", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);

    // Click the next button (ChevronRight)
    const nextBtn = page.locator("button").filter({ has: page.locator("svg") }).nth(2); // prev, play, next
    // More reliable: find by the step counter context
    await page.getByText("Step 1 / 5").waitFor();

    // Click next — it's the third button in the playback controls group
    const controls = page.locator(".flex.items-center.gap-1").first();
    const buttons = controls.locator("button");
    await buttons.nth(2).click(); // 0=prev, 1=play, 2=next

    await expect(page.getByText("Step 2 / 5")).toBeVisible();
    // Step 2 is action_type: "click"
    await expect(
      page.locator('[data-slot="badge"]').filter({ hasText: "Click" })
    ).toBeVisible();
  });

  test("prev button is disabled on step 1", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);

    const controls = page.locator(".flex.items-center.gap-1").first();
    const prevBtn = controls.locator("button").first();
    await expect(prevBtn).toBeDisabled();
  });

  test("clicking a timeline segment jumps to that step", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);

    // Click the 4th segment (index 3) — step 4 is a failed step
    const segments = page.locator('[role="group"][aria-label="Step timeline"] [data-slot="tooltip-trigger"]');
    await segments.nth(3).click();

    await expect(page.getByText("Step 4 / 5")).toBeVisible();
    // Step 4 has success: false, should show error
    await expect(page.getByText("Element not found after timeout")).toBeVisible();
  });

  test("failed step shows error message in detail panel", async ({
    authedPage: page,
  }) => {
    await gotoTaskWithSteps(page);

    // Jump to step 4 (failed)
    const segments = page.locator('[role="group"][aria-label="Step timeline"] [data-slot="tooltip-trigger"]');
    await segments.nth(3).click();

    // Error panel should be visible
    await expect(page.getByText("Element not found after timeout")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Empty steps graceful handling
// ---------------------------------------------------------------------------

test.describe("empty steps handling", () => {
  test("shows 'No step data available' when steps endpoint returns empty", async ({
    authedPage: page,
  }) => {
    // Task says steps: 1, but the endpoint returns []
    await mockTaskDetail(page, COMPLETED_TASK_MINIMAL);
    await mockTaskReplay(page, COMPLETED_TASK_MINIMAL.task_id);
    await mockTaskSteps(page, COMPLETED_TASK_MINIMAL.task_id, []);

    await page.goto(`/tasks/${COMPLETED_TASK_MINIMAL.task_id}`);

    // Steps tab should show for step count > 0
    const stepsTab = page.getByRole("tab", { name: /Steps/ });
    await expect(stepsTab).toBeVisible();
    await stepsTab.click();

    await expect(page.getByText("No step data available")).toBeVisible();
  });
});
