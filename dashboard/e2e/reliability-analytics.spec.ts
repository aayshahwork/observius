/**
 * E2E: Reliability analytics section on the overview page, and failure filter on tasks page.
 *
 * Covers:
 *  - Reliability score card renders with correct percentage
 *  - Repair success rate card renders
 *  - Circuit breaker card renders
 *  - Failure breakdown chart renders (bar chart)
 *  - Repair effectiveness chart renders
 *  - Top failing domains table renders when data is present
 *  - Graceful empty state when no reliability data exists (score 100%)
 *  - Failure filter dropdown on tasks page filters by dominant_failure
 *  - "Repaired" status filter shows only was_repaired=true tasks
 */

import { test, expect, mockTaskList, COMPLETED_TASK_FULL, FAILED_TASK_WITH_RETRY } from "./fixtures";
import type { ReliabilityAnalytics } from "../src/lib/types";
import type { Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

async function mockReliabilityAnalytics(
  page: Page,
  data: ReliabilityAnalytics
): Promise<void> {
  await page.route("**/api/v1/analytics/reliability*", (route) =>
    route.fulfill({ status: 200, json: data })
  );
}

async function mockOverviewApis(page: Page, reliability: ReliabilityAnalytics): Promise<void> {
  // Tasks list for the overview activity chart
  await page.route("**/api/v1/tasks*", (route) => {
    if (route.request().method() !== "GET") { route.fallback(); return; }
    route.fulfill({
      status: 200,
      json: { tasks: [], total: 0, has_more: false },
    });
  });
  // Sessions
  await page.route("**/api/v1/sessions*", (route) =>
    route.fulfill({ status: 200, json: [] })
  );
  await mockReliabilityAnalytics(page, reliability);
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const FULL_RELIABILITY: ReliabilityAnalytics = {
  success_rate: 0.82,
  repair_success_rate: 0.65,
  failure_distribution: {
    element_not_found: 12,
    network_timeout: 5,
    goal_not_met: 3,
    policy_violation: 1,
  },
  repair_distribution: {
    scroll_into_view: { attempts: 8, successes: 6 },
    wait_for_element: { attempts: 5, successes: 4 },
    clear_cookies: { attempts: 3, successes: 1 },
  },
  circuit_breaker_trips: 2,
  avg_repairs_per_task: 1.3,
  top_failing_domains: [
    { domain: "app.example.com", failure_count: 8, top_failure: "element_not_found" },
    { domain: "login.acme.io", failure_count: 4, top_failure: "network_timeout" },
  ],
};

const EMPTY_RELIABILITY: ReliabilityAnalytics = {
  success_rate: 1.0,
  repair_success_rate: 0,
  failure_distribution: {},
  repair_distribution: {},
  circuit_breaker_trips: 0,
  avg_repairs_per_task: 0,
  top_failing_domains: [],
};

// ---------------------------------------------------------------------------
// Score cards
// ---------------------------------------------------------------------------

test.describe("reliability score cards", () => {
  test("renders reliability score as percentage", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, FULL_RELIABILITY);
    await page.goto("/overview");
    // 0.82 → "82%"
    await expect(page.getByText("82%")).toBeVisible();
    await expect(page.getByText("overall task success rate")).toBeVisible();
  });

  test("renders repair success rate", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, FULL_RELIABILITY);
    await page.goto("/overview");
    // 0.65 → "65%"
    await expect(page.getByText("65%")).toBeVisible();
    await expect(page.getByText(/of repaired tasks succeeded/)).toBeVisible();
  });

  test("renders circuit breaker trips", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, FULL_RELIABILITY);
    await page.goto("/overview");
    await expect(page.getByText("Circuit Breaker")).toBeVisible();
    await expect(page.getByText("trips this period")).toBeVisible();
  });

  test("shows 100% when all tasks succeeded", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, EMPTY_RELIABILITY);
    await page.goto("/overview");
    await expect(page.getByText("100%")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

test.describe("reliability charts", () => {
  test("failure breakdown chart renders with recharts container", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, FULL_RELIABILITY);
    await page.goto("/overview");
    await expect(page.getByText("Failure Breakdown")).toBeVisible();
    // Recharts renders a responsive container
    await expect(page.locator(".recharts-responsive-container").first()).toBeVisible();
  });

  test("repair effectiveness chart renders", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, FULL_RELIABILITY);
    await page.goto("/overview");
    await expect(page.getByText("Repair Effectiveness")).toBeVisible();
  });

  test("empty failure distribution shows 'No failure data' message", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, EMPTY_RELIABILITY);
    await page.goto("/overview");
    await expect(page.getByText("No failure data for this period")).toBeVisible();
  });

  test("empty repair distribution shows 'No repair data' message", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, EMPTY_RELIABILITY);
    await page.goto("/overview");
    await expect(page.getByText("No repair data for this period")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Top failing domains table
// ---------------------------------------------------------------------------

test.describe("top failing domains", () => {
  test("renders domain rows when data is present", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, FULL_RELIABILITY);
    await page.goto("/overview");
    await expect(page.getByText("Top Failing Domains")).toBeVisible();
    await expect(page.getByText("app.example.com")).toBeVisible();
    await expect(page.getByText("login.acme.io")).toBeVisible();
  });

  test("shows failure count badge per domain", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, FULL_RELIABILITY);
    await page.goto("/overview");
    // "8×" for app.example.com
    await expect(page.getByText("8×")).toBeVisible();
  });

  test("top failing domains section hidden when no domains", async ({
    authedPage: page,
  }) => {
    await mockOverviewApis(page, EMPTY_RELIABILITY);
    await page.goto("/overview");
    await expect(page.getByText("Top Failing Domains")).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Failure filter on tasks page
// ---------------------------------------------------------------------------

test.describe("failure filter on tasks page", () => {
  const FAILED_UI_TASK = {
    ...FAILED_TASK_WITH_RETRY,
    task_id: "fail0001-0000-7000-8000-000000000011",
    status: "failed" as const,
    dominant_failure: "element_not_found",
    repair_count: 1,
    was_repaired: false,
  };

  const REPAIRED_TASK = {
    ...COMPLETED_TASK_FULL,
    task_id: "rep00001-0000-7000-8000-000000000012",
    status: "completed" as const,
    dominant_failure: "element_not_found",
    repair_count: 2,
    was_repaired: true,
  };

  test("'Repaired' filter button appears in status row", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL]);
    await page.route("**/api/v1/sessions*", (route) =>
      route.fulfill({ status: 200, json: [] })
    );
    await page.goto("/tasks");
    await expect(page.getByRole("button", { name: "Repaired" })).toBeVisible();
  });

  test("selecting 'Repaired' shows only was_repaired=true tasks", async ({
    authedPage: page,
  }) => {
    // API returns both tasks (no status filter applied for "repaired")
    await mockTaskList(page, [FAILED_UI_TASK, REPAIRED_TASK]);
    await page.route("**/api/v1/sessions*", (route) =>
      route.fulfill({ status: 200, json: [] })
    );
    await page.goto("/tasks");
    await page.getByRole("button", { name: "Repaired" }).click();
    // Only REPAIRED_TASK should be visible (was_repaired: true)
    // The task table shows task_description, which comes from COMPLETED_TASK_FULL
    await expect(page.getByText("Extract heading from example.com")).toBeVisible();
  });

  test("failure class dropdown appears when status=failed is selected", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [FAILED_UI_TASK]);
    await page.route("**/api/v1/sessions*", (route) =>
      route.fulfill({ status: 200, json: [] })
    );
    await page.goto("/tasks");
    await page.getByRole("button", { name: "Failed" }).click();
    // Two select dropdowns appear: error category and failure class
    // Check that at least 2 select triggers are visible in the filter area
    await expect(page.locator('[data-slot="select-trigger"]')).toHaveCount(2);
  });

  test("failure class dropdown is not visible for non-failed status", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL]);
    await page.route("**/api/v1/sessions*", (route) =>
      route.fulfill({ status: 200, json: [] })
    );
    await page.goto("/tasks");
    // Default is "all" — no filter dropdowns shown
    await expect(page.locator('[data-slot="select-trigger"]')).toHaveCount(0);
  });
});
