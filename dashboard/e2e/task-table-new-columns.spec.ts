/**
 * E2E: Task table — new Cost column and Native indicator ("N" badge).
 *
 * Covers:
 *  - Cost column header is present in the table
 *  - Cost cell shows formatted cost ("$0.03") for a non-zero value
 *  - Cost cell shows "—" when cost_cents is 0
 *  - Amber background is applied when cost_cents > 50
 *  - No amber background when cost_cents <= 50
 *  - "N" badge appears in the Status cell for native executor tasks
 *  - "N" badge is absent for browser_use tasks
 *  - Clicking a row navigates to the task detail page
 */

import {
  test,
  expect,
  mockTaskList,
  mockTaskDetail,
  mockTaskReplay,
  COMPLETED_TASK_FULL,
  COMPLETED_TASK_MINIMAL,
  NATIVE_TASK,
  FAILED_TASK_WITH_RETRY,
} from "./fixtures";

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

async function gotoTasksPage(page: Parameters<typeof mockTaskList>[0]) {
  // Intercept the validateKey call
  await page.route("**/api/v1/tasks?limit=1", (route) =>
    route.fulfill({
      status: 200,
      json: { tasks: [], total: 0, has_more: false },
    })
  );
  await page.goto("/tasks");
}

// ---------------------------------------------------------------------------
// Cost column header
// ---------------------------------------------------------------------------

test.describe("Cost column header", () => {
  test("'Cost' column header is visible in the task table", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL]);
    await gotoTasksPage(page);

    await expect(page.getByRole("columnheader", { name: "Cost" })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Cost cell formatting
// ---------------------------------------------------------------------------

test.describe("Cost cell formatting", () => {
  test("shows '$0.03' when cost_cents is 3", async ({ authedPage: page }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL]); // cost_cents: 3
    await gotoTasksPage(page);

    await expect(page.getByRole("cell", { name: "$0.03" })).toBeVisible();
  });

  test("shows '—' when cost_cents is 0", async ({ authedPage: page }) => {
    await mockTaskList(page, [COMPLETED_TASK_MINIMAL]); // cost_cents: 0
    await gotoTasksPage(page);

    // There may be multiple "—" cells (e.g. Duration). Check the Cost column
    // specifically by finding the row for our task.
    const taskRow = page.getByRole("row").filter({
      hasText: COMPLETED_TASK_MINIMAL.task_id.slice(0, 8),
    });
    // The cost cell is hidden on mobile but present in DOM.
    // At desktop viewport (default 1280×720) it is visible.
    const costCell = taskRow.locator("td").nth(5); // 0:Status 1:Desc 2:URL 3:Steps 4:Duration 5:Cost
    await expect(costCell).toHaveText("—");
  });

  test("applies amber background class when cost_cents > 50", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [NATIVE_TASK]); // cost_cents: 75 > 50
    await gotoTasksPage(page);

    const taskRow = page.getByRole("row").filter({
      hasText: NATIVE_TASK.task_id.slice(0, 8),
    });
    const costCell = taskRow.locator("td").nth(5);
    await expect(costCell).toHaveClass(/bg-amber-50/);
  });

  test("does not apply amber background when cost_cents <= 50", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL]); // cost_cents: 3
    await gotoTasksPage(page);

    const taskRow = page.getByRole("row").filter({
      hasText: COMPLETED_TASK_FULL.task_id.slice(0, 8),
    });
    const costCell = taskRow.locator("td").nth(5);
    await expect(costCell).not.toHaveClass(/bg-amber-50/);
  });
});

// ---------------------------------------------------------------------------
// Native indicator ("N" badge)
// ---------------------------------------------------------------------------

test.describe("Native indicator badge", () => {
  test("'N' badge appears in status cell for native executor tasks", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [NATIVE_TASK]);
    await gotoTasksPage(page);

    const taskRow = page.getByRole("row").filter({
      hasText: NATIVE_TASK.task_id.slice(0, 8),
    });
    const statusCell = taskRow.locator("td").first();

    // The badge contains just "N"
    const nBadge = statusCell.locator('[data-slot="badge"]').filter({ hasText: /^N$/ });
    await expect(nBadge).toBeVisible();
  });

  test("'N' badge is absent for browser_use tasks", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL]); // executor_mode: "browser_use"
    await gotoTasksPage(page);

    const taskRow = page.getByRole("row").filter({
      hasText: COMPLETED_TASK_FULL.task_id.slice(0, 8),
    });
    const statusCell = taskRow.locator("td").first();

    const nBadge = statusCell.locator('[data-slot="badge"]').filter({ hasText: /^N$/ });
    await expect(nBadge).not.toBeVisible();
  });

  test("mixed list: 'N' shows only on native rows", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL, NATIVE_TASK]);
    await gotoTasksPage(page);

    const browserUseRow = page.getByRole("row").filter({
      hasText: COMPLETED_TASK_FULL.task_id.slice(0, 8),
    });
    const nativeRow = page.getByRole("row").filter({
      hasText: NATIVE_TASK.task_id.slice(0, 8),
    });

    await expect(
      nativeRow.locator('[data-slot="badge"]').filter({ hasText: /^N$/ })
    ).toBeVisible();

    await expect(
      browserUseRow.locator('[data-slot="badge"]').filter({ hasText: /^N$/ })
    ).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Row click navigation
// ---------------------------------------------------------------------------

test.describe("Row click navigation", () => {
  test("clicking a task row navigates to its detail page", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL]);
    await mockTaskDetail(page, COMPLETED_TASK_FULL);
    await mockTaskReplay(page, COMPLETED_TASK_FULL.task_id);
    await gotoTasksPage(page);

    const taskRow = page.getByRole("row").filter({
      hasText: COMPLETED_TASK_FULL.task_id.slice(0, 8),
    });
    await taskRow.click();

    await expect(page).toHaveURL(
      new RegExp(`/tasks/${COMPLETED_TASK_FULL.task_id}`)
    );
  });
});

// ---------------------------------------------------------------------------
// Multiple tasks in the table
// ---------------------------------------------------------------------------

test.describe("Table with multiple tasks", () => {
  test("renders all tasks in the supplied list", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [
      COMPLETED_TASK_FULL,
      NATIVE_TASK,
      COMPLETED_TASK_MINIMAL,
      FAILED_TASK_WITH_RETRY,
    ]);
    await gotoTasksPage(page);

    // Each task ID truncated to 8 chars should appear as description fallback
    for (const task of [COMPLETED_TASK_FULL, NATIVE_TASK, COMPLETED_TASK_MINIMAL, FAILED_TASK_WITH_RETRY]) {
      await expect(
        page.getByRole("row").filter({ hasText: task.task_id.slice(0, 8) })
      ).toBeVisible();
    }
  });
});
