/**
 * E2E: Task detail page — new fields added in the recent dashboard update.
 *
 * Covers:
 *  - Executor mode badge ("Browser Use" / "Native")
 *  - Cost and token display in the metadata grid
 *  - Retry chain banner when retry_count > 0
 *  - Error category badge with "(auto-retryable)" label for transient categories
 *  - Null-safety: page renders correctly when optional fields are 0 / null
 */

import {
  test,
  expect,
  mockTaskDetail,
  mockTaskReplay,
  mockTaskList,
  COMPLETED_TASK_FULL,
  FAILED_TASK_WITH_RETRY,
  COMPLETED_TASK_MINIMAL,
} from "./fixtures";

// ---------------------------------------------------------------------------
// Executor mode badge
// ---------------------------------------------------------------------------

test.describe("executor mode badge", () => {
  test("shows 'Browser Use' badge for browser_use tasks", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, COMPLETED_TASK_FULL);
    await mockTaskReplay(page, COMPLETED_TASK_FULL.task_id);

    await page.goto(`/tasks/${COMPLETED_TASK_FULL.task_id}`);

    await expect(
      page.getByRole("status").or(page.locator('[data-slot="badge"]')).first()
    ).toBeVisible();

    // Find the outline badge adjacent to the status badge
    const badge = page.locator('[data-slot="badge"][class*="border-border"]').filter({
      hasText: "Browser Use",
    });
    await expect(badge).toBeVisible();
  });

  test("shows 'Native' badge for native executor tasks", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, FAILED_TASK_WITH_RETRY);
    // Replay will 404 for failed native task — that's fine
    await page.route(`**/api/v1/tasks/${FAILED_TASK_WITH_RETRY.task_id}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto(`/tasks/${FAILED_TASK_WITH_RETRY.task_id}`);

    const badge = page.locator('[data-slot="badge"][class*="border-border"]').filter({
      hasText: "Native",
    });
    await expect(badge).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Cost field
// ---------------------------------------------------------------------------

test.describe("cost field in metadata grid", () => {
  test("renders formatted cost when cost_cents > 0", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, COMPLETED_TASK_FULL); // cost_cents: 3
    await mockTaskReplay(page, COMPLETED_TASK_FULL.task_id);

    await page.goto(`/tasks/${COMPLETED_TASK_FULL.task_id}`);

    // Cost label
    await expect(page.getByText("Cost", { exact: true })).toBeVisible();
    // formatCost(3) → "$0.03"
    await expect(page.getByText("$0.03")).toBeVisible();
  });

  test("renders '—' when cost_cents is 0 (null safety)", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, COMPLETED_TASK_MINIMAL); // cost_cents: 0
    await mockTaskReplay(page, COMPLETED_TASK_MINIMAL.task_id);

    await page.goto(`/tasks/${COMPLETED_TASK_MINIMAL.task_id}`);

    await expect(page.getByText("Cost", { exact: true })).toBeVisible();
    // formatCost(0) → "—"
    const costValue = page.locator("dd").filter({ hasText: "—" }).first();
    await expect(costValue).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Token field
// ---------------------------------------------------------------------------

test.describe("token field in metadata grid", () => {
  test("renders formatted token counts when both are non-zero", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, COMPLETED_TASK_FULL); // tokens_in: 1200, out: 800
    await mockTaskReplay(page, COMPLETED_TASK_FULL.task_id);

    await page.goto(`/tasks/${COMPLETED_TASK_FULL.task_id}`);

    await expect(page.getByText("Tokens", { exact: true })).toBeVisible();
    // formatTokens(1200) → "1.2K", formatTokens(800) → "800"
    await expect(page.getByText(/↑1\.2K\s*↓800/)).toBeVisible();
  });

  test("renders '—' when both token counts are 0 (null safety)", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, COMPLETED_TASK_MINIMAL); // both 0
    await mockTaskReplay(page, COMPLETED_TASK_MINIMAL.task_id);

    await page.goto(`/tasks/${COMPLETED_TASK_MINIMAL.task_id}`);

    await expect(page.getByText("Tokens", { exact: true })).toBeVisible();
    // Both are 0 so the template renders "—"
    // The Cost field also renders "—"; grab by the dt/dd pair
    const tokensDt = page.locator("dt").filter({ hasText: "Tokens" });
    await expect(tokensDt).toBeVisible();
    const tokensDd = tokensDt.locator("~ dd");
    await expect(tokensDd).toHaveText("—");
  });
});

// ---------------------------------------------------------------------------
// Retry chain banner
// ---------------------------------------------------------------------------

test.describe("retry chain banner", () => {
  test("shows retry banner when retry_count > 0 and retry_of_task_id is set", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, FAILED_TASK_WITH_RETRY);
    await page.route(
      `**/api/v1/tasks/${FAILED_TASK_WITH_RETRY.task_id}/replay`,
      (route) => route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto(`/tasks/${FAILED_TASK_WITH_RETRY.task_id}`);

    // Banner text: "Retry attempt 2 — original task:"
    await expect(page.getByText(/Retry attempt 2/)).toBeVisible();
    // Link shows first 8 chars of the original task ID
    const originalId = FAILED_TASK_WITH_RETRY.retry_of_task_id!;
    await expect(page.getByText(originalId.slice(0, 8))).toBeVisible();
  });

  test("clicking the original task link navigates to it", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, FAILED_TASK_WITH_RETRY);
    await page.route(
      `**/api/v1/tasks/${FAILED_TASK_WITH_RETRY.task_id}/replay`,
      (route) => route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );
    // Mock the original task so navigation succeeds
    const originalId = FAILED_TASK_WITH_RETRY.retry_of_task_id!;
    await page.route(`**/api/v1/tasks/${originalId}`, (route) =>
      route.fulfill({
        status: 200,
        json: { ...COMPLETED_TASK_FULL, task_id: originalId },
      })
    );
    await page.route(`**/api/v1/tasks/${originalId}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto(`/tasks/${FAILED_TASK_WITH_RETRY.task_id}`);

    const originalIdShort = originalId.slice(0, 8);
    await page.getByText(originalIdShort).click();

    await expect(page).toHaveURL(new RegExp(`/tasks/${originalId}`));
  });

  test("does not show retry banner when retry_count is 0", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, COMPLETED_TASK_FULL); // retry_count: 0
    await mockTaskReplay(page, COMPLETED_TASK_FULL.task_id);

    await page.goto(`/tasks/${COMPLETED_TASK_FULL.task_id}`);

    await expect(page.getByText(/Retry attempt/)).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Error category badge
// ---------------------------------------------------------------------------

test.describe("error category badge", () => {
  test("shows error category badge with label on failed task", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, FAILED_TASK_WITH_RETRY); // error_category: "transient_llm"
    await page.route(
      `**/api/v1/tasks/${FAILED_TASK_WITH_RETRY.task_id}/replay`,
      (route) => route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto(`/tasks/${FAILED_TASK_WITH_RETRY.task_id}`);

    // getErrorCategoryLabel("transient_llm") → "Transient (LLM)"
    await expect(page.getByText("Transient (LLM)")).toBeVisible();
  });

  test("shows '(auto-retryable)' text for transient error categories", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, FAILED_TASK_WITH_RETRY);
    await page.route(
      `**/api/v1/tasks/${FAILED_TASK_WITH_RETRY.task_id}/replay`,
      (route) => route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto(`/tasks/${FAILED_TASK_WITH_RETRY.task_id}`);

    await expect(page.getByText("(auto-retryable)")).toBeVisible();
  });

  test("does not show error category badge when error_category is null", async ({
    authedPage: page,
  }) => {
    // Create a failed task with no category
    const taskNoCat = {
      ...FAILED_TASK_WITH_RETRY,
      task_id: "01900000-0000-7000-8000-000000000010",
      error_category: null,
      retry_count: 0,
      retry_of_task_id: null,
    };
    await mockTaskDetail(page, taskNoCat);
    await page.route(
      `**/api/v1/tasks/${taskNoCat.task_id}/replay`,
      (route) => route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto(`/tasks/${taskNoCat.task_id}`);

    // Error message should be visible
    await expect(page.getByText(FAILED_TASK_WITH_RETRY.error!)).toBeVisible();
    // But no category label
    await expect(page.getByText("Transient (LLM)")).not.toBeVisible();
    await expect(page.getByText("(auto-retryable)")).not.toBeVisible();
  });

  test("shows permanent error category without '(auto-retryable)'", async ({
    authedPage: page,
  }) => {
    const taskPermanent = {
      ...FAILED_TASK_WITH_RETRY,
      task_id: "01900000-0000-7000-8000-000000000011",
      error_category: "permanent_task" as const,
      retry_count: 0,
      retry_of_task_id: null,
    };
    await mockTaskDetail(page, taskPermanent);
    await page.route(
      `**/api/v1/tasks/${taskPermanent.task_id}/replay`,
      (route) => route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto(`/tasks/${taskPermanent.task_id}`);

    // getErrorCategoryLabel("permanent_task") → "Permanent (Task)"
    await expect(page.getByText("Permanent (Task)")).toBeVisible();
    // isRetryable("permanent_task") → false, so no auto-retryable text
    await expect(page.getByText("(auto-retryable)")).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Null-safety: page renders without crash for minimal old-style task data
// ---------------------------------------------------------------------------

test.describe("null safety on old task data", () => {
  test("detail page renders without error for a zero-cost, no-error task", async ({
    authedPage: page,
  }) => {
    await mockTaskDetail(page, COMPLETED_TASK_MINIMAL);
    await mockTaskReplay(page, COMPLETED_TASK_MINIMAL.task_id);

    await page.goto(`/tasks/${COMPLETED_TASK_MINIMAL.task_id}`);

    // Core metadata should be present
    await expect(page.getByText("Task Details")).toBeVisible();
    await expect(page.getByText(COMPLETED_TASK_MINIMAL.task_id)).toBeVisible();
    // Executor mode badge defaults to "Browser Use"
    await expect(
      page.locator('[data-slot="badge"][class*="border-border"]').filter({ hasText: "Browser Use" })
    ).toBeVisible();
    // No error card, no retry banner
    await expect(page.getByText(/Retry attempt/)).not.toBeVisible();
    await expect(page.getByText("Error")).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Validate key call when already authenticated
// ---------------------------------------------------------------------------

test.describe("route interception baseline", () => {
  test("task detail page loads and displays task ID", async ({
    authedPage: page,
  }) => {
    // Intercept the validateKey call (listTasks with limit=1)
    await page.route("**/api/v1/tasks?limit=1", (route) =>
      route.fulfill({
        status: 200,
        json: { tasks: [], total: 0, has_more: false },
      })
    );
    await mockTaskList(page, [COMPLETED_TASK_FULL]);
    await mockTaskDetail(page, COMPLETED_TASK_FULL);
    await mockTaskReplay(page, COMPLETED_TASK_FULL.task_id);

    await page.goto(`/tasks/${COMPLETED_TASK_FULL.task_id}`);

    await expect(page.getByText(COMPLETED_TASK_FULL.task_id)).toBeVisible();
  });
});
