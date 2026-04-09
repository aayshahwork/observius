/**
 * E2E: Reliability indicators on the step timeline.
 *
 * Covers:
 *  - Failure class badge shows human-readable label
 *  - Repair badge shows "Repaired: <action> ✓" when patch_applied.success=true
 *  - Repair badge shows "Attempted: <action> ✗" when patch_applied.success=false
 *  - Artifact links (HAR, Trace, Video) render when refs are present
 *  - Steps without reliability fields render normally (no spurious badges)
 */

import {
  test,
  expect,
  mockTaskDetail,
  mockTaskReplay,
  mockTaskSteps,
  COMPLETED_TASK_FULL,
} from "./fixtures";
import type { StepResponse } from "../src/lib/types";

// ---------------------------------------------------------------------------
// Fixture: steps with reliability fields
// ---------------------------------------------------------------------------

const TASK_WITH_REPAIRS = {
  ...COMPLETED_TASK_FULL,
  task_id: "rel10001-0000-7000-8000-000000000001",
  steps: 3,
  failure_counts: { element_not_found: 2, network_timeout: 1 },
};

const RELIABILITY_STEPS: StepResponse[] = [
  {
    step_number: 1,
    action_type: "click",
    description: "Click the submit button",
    screenshot_url: null,
    tokens_in: 300,
    tokens_out: 150,
    duration_ms: 800,
    success: false,
    error: "Element not found",
    created_at: "2025-03-28T10:00:01.000Z",
    // Reliability fields
    validator_verdict: "fail_ui",
    failure_class: "element_not_found",
    patch_applied: { action: "scroll_into_view", success: false },
    har_ref: "https://artifacts.example.com/step1.har",
    trace_ref: "https://artifacts.example.com/step1.zip",
    video_ref: null,
  },
  {
    step_number: 2,
    action_type: "click",
    description: "Click repaired submit button",
    screenshot_url: null,
    tokens_in: 280,
    tokens_out: 140,
    duration_ms: 600,
    success: true,
    error: null,
    created_at: "2025-03-28T10:00:02.000Z",
    // Reliability fields — successful repair
    validator_verdict: "pass",
    failure_class: "element_not_found",
    patch_applied: { action: "wait_for_element", success: true },
    har_ref: null,
    trace_ref: null,
    video_ref: "https://artifacts.example.com/step2.mp4",
  },
  {
    step_number: 3,
    action_type: "navigate",
    description: "Navigate to results page",
    screenshot_url: null,
    tokens_in: 200,
    tokens_out: 100,
    duration_ms: 1_200,
    success: true,
    error: null,
    created_at: "2025-03-28T10:00:03.000Z",
    // No reliability fields — normal step
  },
];

async function gotoReliabilityTask(page: import("@playwright/test").Page) {
  await mockTaskDetail(page, TASK_WITH_REPAIRS);
  await mockTaskReplay(page, TASK_WITH_REPAIRS.task_id);
  await mockTaskSteps(page, TASK_WITH_REPAIRS.task_id, RELIABILITY_STEPS);
  await page.goto(`/tasks/${TASK_WITH_REPAIRS.task_id}`);
  // Ensure Steps tab is active
  await page.getByRole("tab", { name: /Steps/ }).click();
}

// ---------------------------------------------------------------------------
// Failure class badge
// ---------------------------------------------------------------------------

test.describe("failure class badge", () => {
  test("shows human-readable failure class label when failure_class is set", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // Step 1 has failure_class: "element_not_found" → "Element Not Found"
    await expect(
      page.locator('[data-slot="badge"]').filter({ hasText: "Element Not Found" })
    ).toBeVisible();
  });

  test("does not show failure badge when failure_class is absent", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // Navigate to step 3 — no failure_class
    const controls = page.locator(".flex.items-center.gap-1").first();
    await controls.locator("button").nth(2).click(); // next
    await controls.locator("button").nth(2).click(); // next again → step 3
    await expect(page.getByText("Step 3 / 3")).toBeVisible();
    // No badge with variant="destructive" should appear for a step without failure_class
    await expect(
      page.locator('[data-slot="badge"][data-variant="destructive"]')
    ).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Repair badge — failed repair
// ---------------------------------------------------------------------------

test.describe("repair badge — attempted (failed)", () => {
  test("shows 'Attempted: Scroll Into View ✗' for failed patch_applied", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // Step 1: patch_applied = { action: "scroll_into_view", success: false }
    await expect(
      page.locator('[data-slot="badge"]').filter({ hasText: /Attempted.*Scroll Into View.*✗/ })
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Repair badge — successful repair
// ---------------------------------------------------------------------------

test.describe("repair badge — repaired (success)", () => {
  test("shows 'Repaired: Wait For Element ✓' when navigating to step 2", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // Navigate to step 2 (successful repair)
    const controls = page.locator(".flex.items-center.gap-1").first();
    await controls.locator("button").nth(2).click();
    await expect(page.getByText("Step 2 / 3")).toBeVisible();
    await expect(
      page.locator('[data-slot="badge"]').filter({ hasText: /Repaired.*Wait For Element.*✓/ })
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Artifact links
// ---------------------------------------------------------------------------

test.describe("artifact links", () => {
  test("shows HAR and Trace links for step 1", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // Step 1 has har_ref and trace_ref but no video_ref
    await expect(page.getByRole("link", { name: "HAR" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Trace" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Video" })).not.toBeVisible();
  });

  test("HAR link has download attribute", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    const harLink = page.getByRole("link", { name: "HAR" });
    await expect(harLink).toHaveAttribute("download");
  });

  test("Trace link opens in new tab", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    const traceLink = page.getByRole("link", { name: "Trace" });
    await expect(traceLink).toHaveAttribute("target", "_blank");
  });

  test("shows Video link for step 2 (no HAR or Trace)", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // Navigate to step 2
    const controls = page.locator(".flex.items-center.gap-1").first();
    await controls.locator("button").nth(2).click();
    await expect(page.getByText("Step 2 / 3")).toBeVisible();

    await expect(page.getByRole("link", { name: "Video" })).toBeVisible();
    await expect(page.getByRole("link", { name: "HAR" })).not.toBeVisible();
    await expect(page.getByRole("link", { name: "Trace" })).not.toBeVisible();
  });

  test("no artifact links for step 3 (no refs)", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // Navigate to step 3
    const controls = page.locator(".flex.items-center.gap-1").first();
    await controls.locator("button").nth(2).click();
    await controls.locator("button").nth(2).click();
    await expect(page.getByText("Step 3 / 3")).toBeVisible();

    await expect(page.getByRole("link", { name: "HAR" })).not.toBeVisible();
    await expect(page.getByRole("link", { name: "Trace" })).not.toBeVisible();
    await expect(page.getByRole("link", { name: "Video" })).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Repair Activity card (collapsible)
// ---------------------------------------------------------------------------

test.describe("repair activity card", () => {
  test("Repair Activity card appears when task has repair steps", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    await expect(page.getByText("Repair Activity")).toBeVisible();
  });

  test("shows repair count badge", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // 2 steps have patch_applied
    await expect(
      page.getByText(/2 repairs? attempted/)
    ).toBeVisible();
  });

  test("expanding shows repair timeline entries", async ({
    authedPage: page,
  }) => {
    await gotoReliabilityTask(page);
    // Expand the card
    const expandBtn = page.locator("text=Repair Activity").locator("..").locator("..").getByRole("button");
    await expandBtn.click();
    // Should see step entries
    await expect(page.getByText(/Step 1:/)).toBeVisible();
    await expect(page.getByText(/Step 2:/)).toBeVisible();
  });
});
