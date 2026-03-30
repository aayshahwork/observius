/**
 * E2E: Usage page analytics overhaul.
 *
 * All tests use Playwright route interception to mock API responses — no live
 * backend required.
 *
 * Covered scenarios:
 *  1. Empty state / no tasks
 *  2. Full data with mixed task types
 *  3. Browser-only tasks (no native executor)
 *  4. No failed tasks (all completed)
 *  5. has_more=true banner
 *  6. has_more=false (no banner)
 */

import { test, expect, seedAuth } from "./fixtures";
import type { TaskResponse, TaskListResponse, UsageResponse } from "../src/lib/types";

// ---------------------------------------------------------------------------
// Shared mock data
// ---------------------------------------------------------------------------

const BASE_USAGE: UsageResponse = {
  monthly_steps_used: 120,
  monthly_step_limit: 500,
  tier: "free",
  daily_usage: [
    { date: "2025-03-20", steps: 10 },
    { date: "2025-03-21", steps: 20 },
    { date: "2025-03-22", steps: 30 },
  ],
};

/** Completed task with non-zero cost and browser_use executor */
const TASK_BROWSER_COSTLY: TaskResponse = {
  task_id: "01910000-0000-7000-8000-000000000001",
  url: "https://example.com/shop",
  status: "completed",
  success: true,
  result: { title: "Product" },
  error: null,
  replay_url: null,
  steps: 8,
  duration_ms: 15_000,
  created_at: "2025-03-25T10:00:00.000Z",
  completed_at: "2025-03-25T10:00:15.000Z",
  retry_count: 0,
  retry_of_task_id: null,
  error_category: null,
  cost_cents: 45,
  total_tokens_in: 3_000,
  total_tokens_out: 1_500,
  executor_mode: "browser_use",
};

/** Completed task with non-zero cost and native executor */
const TASK_NATIVE_COSTLY: TaskResponse = {
  task_id: "01910000-0000-7000-8000-000000000002",
  url: "https://api.example.com/data",
  status: "completed",
  success: true,
  result: { records: 42 },
  error: null,
  replay_url: null,
  steps: 4,
  duration_ms: 3_000,
  created_at: "2025-03-26T12:00:00.000Z",
  completed_at: "2025-03-26T12:00:03.000Z",
  retry_count: 0,
  retry_of_task_id: null,
  error_category: null,
  cost_cents: 80,
  total_tokens_in: 2_500,
  total_tokens_out: 900,
  executor_mode: "native",
};

/** Failed task with an error category (triggers error distribution chart) */
const TASK_FAILED_LLM: TaskResponse = {
  task_id: "01910000-0000-7000-8000-000000000003",
  url: "https://example.com/check",
  status: "failed",
  success: false,
  result: null,
  error: "LLM 503",
  replay_url: null,
  steps: 2,
  duration_ms: 4_000,
  created_at: "2025-03-27T08:00:00.000Z",
  completed_at: "2025-03-27T08:00:04.000Z",
  retry_count: 1,
  retry_of_task_id: null,
  error_category: "transient_llm",
  cost_cents: 5,
  total_tokens_in: 400,
  total_tokens_out: 100,
  executor_mode: "browser_use",
};

/** Second failed task with a different category */
const TASK_FAILED_NETWORK: TaskResponse = {
  task_id: "01910000-0000-7000-8000-000000000004",
  url: "https://slow.example.com",
  status: "failed",
  success: false,
  result: null,
  error: "Network timeout",
  replay_url: null,
  steps: 1,
  duration_ms: 30_000,
  created_at: "2025-03-28T09:00:00.000Z",
  completed_at: "2025-03-28T09:00:30.000Z",
  retry_count: 0,
  retry_of_task_id: null,
  error_category: "transient_network",
  cost_cents: 2,
  total_tokens_in: 200,
  total_tokens_out: 50,
  executor_mode: "native",
};

// ---------------------------------------------------------------------------
// Route-mocking helpers
// ---------------------------------------------------------------------------

async function mockUsage(
  page: Parameters<typeof seedAuth>[0],
  usage: UsageResponse = BASE_USAGE
): Promise<void> {
  await page.route("**/api/v1/account/usage", (route) =>
    route.fulfill({ status: 200, json: usage })
  );
}

async function mockUsageTasks(
  page: Parameters<typeof seedAuth>[0],
  tasks: TaskResponse[],
  hasMore = false
): Promise<void> {
  const payload: TaskListResponse = {
    tasks,
    total: tasks.length,
    has_more: hasMore,
  };
  // Match the tasks list with query params (since=..., limit=100)
  await page.route("**/api/v1/tasks?*", (route) => {
    // Only intercept GET requests
    if (route.request().method() !== "GET") {
      route.fallback();
      return;
    }
    route.fulfill({ status: 200, json: payload });
  });
  // Also intercept bare /api/v1/tasks GET (used by validateKey limit=1 call)
  await page.route("**/api/v1/tasks", (route) => {
    if (route.request().method() !== "GET") {
      route.fallback();
      return;
    }
    route.fulfill({ status: 200, json: payload });
  });
}

async function gotoUsagePage(page: Parameters<typeof seedAuth>[0]): Promise<void> {
  await page.goto("/usage");
  // Wait until the loading skeleton is gone (heading becomes visible)
  await expect(page.getByRole("heading", { name: "Usage" })).toBeVisible({
    timeout: 15_000,
  });
}

// ---------------------------------------------------------------------------
// Scenario 1: Empty state — no tasks
// ---------------------------------------------------------------------------

test.describe("Empty state (no tasks)", () => {
  test("monthly steps card and progress bar are visible", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, []);
    await gotoUsagePage(page);

    // Monthly Steps card heading
    await expect(
      page.getByText("Monthly Steps")
    ).toBeVisible();

    // Tier badge
    await expect(page.getByText("free")).toBeVisible();

    // Progress label shows the step counts
    await expect(page.getByText(/120.*of.*500.*steps/)).toBeVisible();
  });

  test("all 4 summary cards are rendered", async ({ authedPage: page }) => {
    await mockUsage(page);
    await mockUsageTasks(page, []);
    await gotoUsagePage(page);

    await expect(page.getByText("Monthly Cost")).toBeVisible();
    await expect(page.getByText("Avg Cost / Task")).toBeVisible();
    await expect(page.getByText("Token Usage")).toBeVisible();
    await expect(page.getByText("Retry Rate")).toBeVisible();
  });

  test("all 4 summary cards show '—' when there are no tasks", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, []);
    await gotoUsagePage(page);

    // With empty tasks, formatCost(0) = "—", formatTokens(0) = "—",
    // and retry rate branch returns "—" when totalTasks === 0.
    // There should be at least 3 em-dashes (Monthly Cost, Token Usage, Retry Rate;
    // Avg Cost also "—" because 0/0 = 0 → formatCost(0) = "—")
    const emDashes = page.getByText("—");
    await expect(emDashes.first()).toBeVisible();
    // Confirm count is at least 3 (Monthly Cost, Token Usage, Retry Rate)
    expect(await emDashes.count()).toBeGreaterThanOrEqual(3);
  });

  test("cost chart shows empty state message when no tasks", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, []);
    await gotoUsagePage(page);

    await expect(
      page.getByText(/Cost tracking was added recently/)
    ).toBeVisible();
  });

  test("error distribution chart is NOT visible when no failed tasks", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, []);
    await gotoUsagePage(page);

    await expect(
      page.getByText("Failure Categories (Last 30 Days)")
    ).not.toBeVisible();
  });

  test("executor comparison section is NOT visible when no tasks", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, []);
    await gotoUsagePage(page);

    await expect(
      page.getByText("Browser Use vs Native Executor")
    ).not.toBeVisible();
  });

  test("most expensive tasks table is NOT visible when no tasks", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, []);
    await gotoUsagePage(page);

    await expect(page.getByText("Most Expensive Tasks")).not.toBeVisible();
  });

  test("has_more banner is NOT shown when no tasks", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, [], false);
    await gotoUsagePage(page);

    await expect(
      page.getByText(/Showing analytics for the most recent 100 tasks/)
    ).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Scenario 2: Full data with mixed tasks
// ---------------------------------------------------------------------------

const MIXED_TASKS = [
  TASK_BROWSER_COSTLY,
  TASK_NATIVE_COSTLY,
  TASK_FAILED_LLM,
  TASK_FAILED_NETWORK,
];

test.describe("Full data with mixed tasks", () => {
  test("summary cards show computed values (not '—')", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS);
    await gotoUsagePage(page);

    // Total cost: 45+80+5+2 = 132 cents → $1.32
    await expect(page.getByText("$1.32")).toBeVisible();

    // Avg cost: 132/4 = 33 cents → $0.33
    await expect(page.getByText("$0.33")).toBeVisible();

    // Total tokens: (3000+1500) + (2500+900) + (400+100) + (200+50) = 8650
    // formatTokens(8650) → "8.7K"
    await expect(page.getByText("8.7K")).toBeVisible();
  });

  test("retry rate shows a percentage when tasks exist", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS);
    await gotoUsagePage(page);

    // TASK_FAILED_LLM has retry_count=1, others have 0 → 1/4 = 25%
    await expect(page.getByText("25.0%")).toBeVisible();
  });

  test("cost chart container is rendered with data", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS);
    await gotoUsagePage(page);

    // When data exists the chart title changes from "Cost Over Time" to
    // "Cost Over Time (Last 30 Days)"
    await expect(
      page.getByText("Cost Over Time (Last 30 Days)")
    ).toBeVisible();

    // The empty-state message should NOT appear
    await expect(
      page.getByText(/Cost tracking was added recently/)
    ).not.toBeVisible();
  });

  test("error distribution chart IS visible when failed tasks exist", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS);
    await gotoUsagePage(page);

    await expect(
      page.getByText("Failure Categories (Last 30 Days)")
    ).toBeVisible();
  });

  test("executor comparison section IS visible when native tasks exist", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS);
    await gotoUsagePage(page);

    await expect(
      page.getByText("Browser Use vs Native Executor")
    ).toBeVisible();

    // Both executor mode labels are shown inside the comparison cards
    await expect(page.getByText("Browser Use", { exact: true })).toBeVisible();
    await expect(page.getByText("Native", { exact: true })).toBeVisible();
  });

  test("most expensive tasks table IS visible and shows the top task", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS);
    await gotoUsagePage(page);

    await expect(page.getByText("Most Expensive Tasks")).toBeVisible();

    // Most expensive: TASK_NATIVE_COSTLY at 80 cents → $0.80
    await expect(
      page.getByRole("cell", { name: /\$0\.80/ })
    ).toBeVisible();
  });

  test("clicking a row in the expensive tasks table navigates to task detail", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS);

    // Mock the detail endpoint needed after navigation to /tasks/:id
    await page.route(
      `**/api/v1/tasks/${TASK_NATIVE_COSTLY.task_id}`,
      (route) => {
        if (route.request().method() !== "GET") {
          route.fallback();
          return;
        }
        route.fulfill({ status: 200, json: TASK_NATIVE_COSTLY });
      }
    );
    // Mock the replay endpoint (task detail page may request it)
    await page.route(
      `**/api/v1/tasks/${TASK_NATIVE_COSTLY.task_id}/replay`,
      (route) =>
        route.fulfill({
          status: 200,
          json: { task_id: TASK_NATIVE_COSTLY.task_id, replay_url: null },
        })
    );

    await gotoUsagePage(page);

    // Click the row containing the most-expensive task's URL.
    // ExpensiveTasksTable sorts by cost_cents desc, so TASK_NATIVE_COSTLY ($0.80)
    // is the first row. Identify the row by its unique URL text.
    const taskRow = page
      .locator("table tbody tr")
      .filter({ hasText: "api.example.com" });
    await expect(taskRow).toBeVisible();
    await taskRow.click();

    await page.waitForURL(
      new RegExp(`/tasks/${TASK_NATIVE_COSTLY.task_id}`),
      { timeout: 10_000 }
    );
  });
});

// ---------------------------------------------------------------------------
// Scenario 3: Browser-only tasks (no native executor)
// ---------------------------------------------------------------------------

test.describe("Browser-only tasks (no native executor)", () => {
  test("executor comparison section is NOT rendered", async ({
    authedPage: page,
  }) => {
    const browserOnlyTasks = [TASK_BROWSER_COSTLY, TASK_FAILED_LLM];
    await mockUsage(page);
    await mockUsageTasks(page, browserOnlyTasks);
    await gotoUsagePage(page);

    await expect(
      page.getByText("Browser Use vs Native Executor")
    ).not.toBeVisible();
  });

  test("expensive tasks table still shows when browser tasks have cost", async ({
    authedPage: page,
  }) => {
    const browserOnlyTasks = [TASK_BROWSER_COSTLY];
    await mockUsage(page);
    await mockUsageTasks(page, browserOnlyTasks);
    await gotoUsagePage(page);

    await expect(page.getByText("Most Expensive Tasks")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Scenario 4: No failed tasks (all completed)
// ---------------------------------------------------------------------------

test.describe("No failed tasks", () => {
  test("error distribution section is NOT rendered", async ({
    authedPage: page,
  }) => {
    const completedOnly = [TASK_BROWSER_COSTLY, TASK_NATIVE_COSTLY];
    await mockUsage(page);
    await mockUsageTasks(page, completedOnly);
    await gotoUsagePage(page);

    await expect(
      page.getByText("Failure Categories (Last 30 Days)")
    ).not.toBeVisible();
  });

  test("summary cards render without dashes when tasks exist", async ({
    authedPage: page,
  }) => {
    const completedOnly = [TASK_BROWSER_COSTLY, TASK_NATIVE_COSTLY];
    await mockUsage(page);
    await mockUsageTasks(page, completedOnly);
    await gotoUsagePage(page);

    // Total cost: 45+80 = 125 cents → $1.25
    await expect(page.getByText("$1.25")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Scenario 5: has_more=true banner
// ---------------------------------------------------------------------------

test.describe("has_more banner", () => {
  test("banner is visible when has_more=true", async ({ authedPage: page }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS, true);
    await gotoUsagePage(page);

    await expect(
      page.getByText(/Showing analytics for the most recent 100 tasks/)
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Scenario 6: has_more=false — no banner
// ---------------------------------------------------------------------------

test.describe("has_more=false (no banner)", () => {
  test("banner is NOT shown when has_more=false", async ({
    authedPage: page,
  }) => {
    await mockUsage(page);
    await mockUsageTasks(page, MIXED_TASKS, false);
    await gotoUsagePage(page);

    await expect(
      page.getByText(/Showing analytics for the most recent 100 tasks/)
    ).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Scenario 7: Failed tasks without error_category (edge case)
// ---------------------------------------------------------------------------

test.describe("Failed tasks without error_category", () => {
  test("error chart is NOT shown when failed tasks have no error_category", async ({
    authedPage: page,
  }) => {
    const taskFailedNoCategory: TaskResponse = {
      ...TASK_FAILED_LLM,
      task_id: "01910000-0000-7000-8000-000000000099",
      error_category: null,
    };
    await mockUsage(page);
    await mockUsageTasks(page, [taskFailedNoCategory]);
    await gotoUsagePage(page);

    await expect(
      page.getByText("Failure Categories (Last 30 Days)")
    ).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Scenario 8: Monthly steps tier badge
// ---------------------------------------------------------------------------

test.describe("Tier badge on monthly steps card", () => {
  test("badge shows the correct tier from usage response", async ({
    authedPage: page,
  }) => {
    const startupUsage: UsageResponse = {
      ...BASE_USAGE,
      tier: "startup",
      monthly_steps_used: 1_200,
      monthly_step_limit: 5_000,
    };
    await mockUsage(page, startupUsage);
    await mockUsageTasks(page, []);
    await gotoUsagePage(page);

    await expect(page.getByText("startup")).toBeVisible();
    await expect(page.getByText(/1,200.*of.*5,000.*steps/)).toBeVisible();
  });
});
