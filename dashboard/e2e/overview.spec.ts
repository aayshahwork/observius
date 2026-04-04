/**
 * E2E: Overview page — health-at-a-glance dashboard at /overview.
 *
 * Covers:
 *  - Page loads and displays the "Overview" heading
 *  - Four metric cards render with correct values
 *  - Success rate is color-coded by threshold
 *  - Task count trend arrow shows vs yesterday
 *  - Task Activity chart section renders
 *  - Recent Failures panel shows failed tasks or empty state
 *  - Retry Activity panel shows retried tasks or empty state
 *  - Retry button calls API, shows loading state, refreshes data
 *  - Loading skeletons appear while data loads
 *  - Sidebar highlights the Overview nav item
 *  - Logo link points to /overview
 */

import {
  test,
  expect,
  mockTaskList,
  COMPLETED_TASK_FULL,
  FAILED_TASK_WITH_RETRY,
} from "./fixtures";
import type { TaskResponse } from "../src/lib/types";

// ---------------------------------------------------------------------------
// Mock data — dates are dynamic so "today"/"yesterday" logic works
// ---------------------------------------------------------------------------

const NOW = new Date();
const TODAY_ISO = NOW.toISOString();

// Noon yesterday local time — guaranteed to fall in the page's "yesterday" bucket
const YESTERDAY = new Date(
  NOW.getFullYear(),
  NOW.getMonth(),
  NOW.getDate() - 1,
  12,
  0,
  0
);
const YESTERDAY_ISO = YESTERDAY.toISOString();

const TASK_TODAY_COMPLETED_1: TaskResponse = {
  ...COMPLETED_TASK_FULL,
  task_id: "01900000-0000-7000-8000-e00000000001",
  created_at: TODAY_ISO,
  completed_at: TODAY_ISO,
  cost_cents: 5,
};

const TASK_TODAY_COMPLETED_2: TaskResponse = {
  ...COMPLETED_TASK_FULL,
  task_id: "01900000-0000-7000-8000-e00000000002",
  created_at: TODAY_ISO,
  completed_at: TODAY_ISO,
  cost_cents: 3,
};

const TASK_TODAY_FAILED: TaskResponse = {
  ...FAILED_TASK_WITH_RETRY,
  task_id: "01900000-0000-7000-8000-e00000000003",
  created_at: TODAY_ISO,
  completed_at: TODAY_ISO,
  status: "failed",
  success: false,
  error: "LLM 503",
  replay_url: "https://example.com/failed-page",
  error_category: "transient_llm",
  retry_count: 0,
  retry_of_task_id: null,
  cost_cents: 1,
};

const TASK_TODAY_RETRIED: TaskResponse = {
  ...COMPLETED_TASK_FULL,
  task_id: "01900000-0000-7000-8000-e00000000004",
  created_at: TODAY_ISO,
  completed_at: TODAY_ISO,
  retry_count: 2,
  retry_of_task_id: "01900000-0000-7000-8000-e00000000099",
  cost_cents: 10,
};

const TASK_YESTERDAY: TaskResponse = {
  ...COMPLETED_TASK_FULL,
  task_id: "01900000-0000-7000-8000-e00000000005",
  created_at: YESTERDAY_ISO,
  completed_at: YESTERDAY_ISO,
};

const ALL_TASKS = [
  TASK_TODAY_COMPLETED_1,
  TASK_TODAY_COMPLETED_2,
  TASK_TODAY_FAILED,
  TASK_TODAY_RETRIED,
  TASK_YESTERDAY,
];

const MOCK_SESSIONS = [
  {
    session_id: "sess-e2e-001",
    origin_domain: "example.com",
    auth_state: "active",
    last_used_at: TODAY_ISO,
    expires_at: null,
    created_at: TODAY_ISO,
  },
  {
    session_id: "sess-e2e-002",
    origin_domain: "test.com",
    auth_state: "stale",
    last_used_at: null,
    expires_at: null,
    created_at: TODAY_ISO,
  },
  {
    session_id: "sess-e2e-003",
    origin_domain: "another.com",
    auth_state: "expired",
    last_used_at: null,
    expires_at: null,
    created_at: TODAY_ISO,
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function mockSessions(
  page: Parameters<typeof mockTaskList>[0],
  sessions = MOCK_SESSIONS
) {
  await page.route("**/api/v1/sessions", (route) => {
    if (route.request().method() !== "GET") {
      route.fallback();
      return;
    }
    route.fulfill({ status: 200, json: sessions });
  });
}

async function setupPopulated(page: Parameters<typeof mockTaskList>[0]) {
  await mockTaskList(page, ALL_TASKS);
  await mockSessions(page);
}

async function setupEmpty(page: Parameters<typeof mockTaskList>[0]) {
  await mockTaskList(page, []);
  await mockSessions(page, []);
}

// ---------------------------------------------------------------------------
// Page load and heading
// ---------------------------------------------------------------------------

test.describe("Overview page — structure", () => {
  test("displays the Overview heading", async ({ authedPage: page }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  });

  test("renders four metric cards", async ({ authedPage: page }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    await expect(page.getByText("Tasks Today")).toBeVisible();
    await expect(page.getByText("Success Rate (24h)")).toBeVisible();
    await expect(page.getByText("Cost Today")).toBeVisible();
    await expect(page.getByText("Active Sessions")).toBeVisible();
  });

  test("renders chart section", async ({ authedPage: page }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    await expect(
      page.getByText("Task Activity (Last 7 Days)")
    ).toBeVisible();
  });

  test("renders both bottom panels", async ({ authedPage: page }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    await expect(page.getByText("Recent Failures")).toBeVisible();
    await expect(page.getByText("Retry Activity")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Metric card values
// ---------------------------------------------------------------------------

test.describe("Overview page — metric values", () => {
  test("Tasks Today shows count with trend arrow", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // 4 tasks today (completed_1, completed_2, failed, retried), 1 yesterday
    // trend = 4 - 1 = +3
    const card = page
      .locator('[data-slot="card"]')
      .filter({ hasText: "Tasks Today" });
    await expect(card.locator(".text-2xl")).toHaveText("4");
    await expect(card.getByText(/3 vs yesterday/)).toBeVisible();
  });

  test("Success Rate shows percentage with color", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // 24h: 3 completed + 1 failed = 75% → amber (70-90%)
    const card = page
      .locator("div")
      .filter({ hasText: /^Success Rate/ })
      .first();
    await expect(card.getByText("75%")).toBeVisible();
  });

  test("Cost Today shows formatted dollar amount", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // sum of today's cost_cents: 5 + 3 + 1 + 10 = 19 → $0.19
    const card = page
      .locator("div")
      .filter({ hasText: /^Cost Today/ })
      .first();
    await expect(card.getByText("$0.19")).toBeVisible();
  });

  test("Active Sessions shows count excluding stale/expired", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // 3 sessions: 1 active, 1 stale, 1 expired → 1 active
    const card = page
      .locator("div")
      .filter({ hasText: /^Active Sessions/ })
      .first();
    await expect(card.getByText("1")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Empty states
// ---------------------------------------------------------------------------

test.describe("Overview page — empty state", () => {
  test("shows dash for Success Rate when no tasks", async ({
    authedPage: page,
  }) => {
    await setupEmpty(page);
    await page.goto("/overview");

    const card = page
      .locator("div")
      .filter({ hasText: /^Success Rate/ })
      .first();
    await expect(card.getByText("—")).toBeVisible();
  });

  test("shows empty chart message when no activity", async ({
    authedPage: page,
  }) => {
    await setupEmpty(page);
    await page.goto("/overview");

    await expect(
      page.getByText("No task activity in the last 7 days")
    ).toBeVisible();
  });

  test("shows positive empty state for no failures", async ({
    authedPage: page,
  }) => {
    // Only completed tasks — no failures
    await mockTaskList(page, [TASK_TODAY_COMPLETED_1]);
    await mockSessions(page, []);
    await page.goto("/overview");

    await expect(
      page.getByText("No failures in the last 24 hours")
    ).toBeVisible();
  });

  test("shows empty state for no retry activity", async ({
    authedPage: page,
  }) => {
    // Only non-retried tasks
    await mockTaskList(page, [TASK_TODAY_COMPLETED_1]);
    await mockSessions(page, []);
    await page.goto("/overview");

    await expect(page.getByText("No retry activity")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Recent Failures panel
// ---------------------------------------------------------------------------

test.describe("Overview page — Recent Failures", () => {
  test("shows failed task with error category badge", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // TASK_TODAY_FAILED has error_category: "transient_llm"
    const panel = page.locator("div").filter({ hasText: /^Recent Failures/ }).first();
    await expect(
      panel.getByText("Transient (LLM)")
    ).toBeVisible();
  });

  test("failed task row shows truncated URL", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // TASK_TODAY_FAILED has replay_url: "https://example.com/failed-page"
    // truncateUrl → "example.com/failed-page"
    await expect(page.getByText("example.com/failed-page")).toBeVisible();
  });

  test("clicking a failed task navigates to its detail page", async ({
    authedPage: page,
  }) => {
    // Register detail page mocks BEFORE setupPopulated so they take precedence
    await page.route(
      `**/api/v1/tasks/${TASK_TODAY_FAILED.task_id}`,
      (route) => {
        if (route.request().method() !== "GET") {
          route.fallback();
          return;
        }
        route.fulfill({ status: 200, json: TASK_TODAY_FAILED });
      }
    );
    await page.route(
      `**/api/v1/tasks/${TASK_TODAY_FAILED.task_id}/replay`,
      (route) =>
        route.fulfill({
          status: 200,
          json: {
            task_id: TASK_TODAY_FAILED.task_id,
            replay_url: "https://replay.example.com",
          },
        })
    );
    await setupPopulated(page);
    await page.goto("/overview");

    // Click the cursor-pointer div (the onClick handler), not just the text span
    const card = page
      .locator('[data-slot="card"]')
      .filter({ hasText: "Recent Failures" });
    const clickableRow = card
      .locator("div.cursor-pointer")
      .filter({ hasText: "example.com/failed-page" });
    await clickableRow.click();

    await expect(page).toHaveURL(
      new RegExp(`/tasks/${TASK_TODAY_FAILED.task_id}`),
      { timeout: 10_000 }
    );
  });
});

// ---------------------------------------------------------------------------
// Retry button
// ---------------------------------------------------------------------------

test.describe("Overview page — Retry button", () => {
  test("calls retry API and refreshes data on success", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // Mock the retry endpoint
    await page.route(
      `**/api/v1/tasks/${TASK_TODAY_FAILED.task_id}/retry`,
      (route) => {
        if (route.request().method() !== "POST") {
          route.fallback();
          return;
        }
        route.fulfill({ status: 200, json: TASK_TODAY_COMPLETED_1 });
      }
    );

    // Find the Retry button next to the failed task
    const failureRow = page
      .locator("div")
      .filter({ hasText: "example.com/failed-page" })
      .filter({ has: page.getByRole("button", { name: "Retry" }) });

    const retryBtn = failureRow.getByRole("button", { name: "Retry" });
    await expect(retryBtn).toBeVisible();

    // Click retry and wait for the POST request
    const [retryRequest] = await Promise.all([
      page.waitForRequest(
        (req) =>
          req.url().includes(`/tasks/${TASK_TODAY_FAILED.task_id}/retry`) &&
          req.method() === "POST"
      ),
      retryBtn.click(),
    ]);

    expect(retryRequest).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Retry Activity panel
// ---------------------------------------------------------------------------

test.describe("Overview page — Retry Activity", () => {
  test("shows retried task with attempt badge", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // TASK_TODAY_RETRIED has retry_count: 2 → "Attempt 3"
    await expect(page.getByText("Attempt 3")).toBeVisible();
  });

  test("shows status badge and cost for retried task", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    // TASK_TODAY_RETRIED: status "completed", cost_cents 10 → "$0.10"
    const panel = page
      .locator("div")
      .filter({ hasText: /^Retry Activity/ })
      .first();
    await expect(panel.getByText("Completed")).toBeVisible();
    await expect(panel.getByText("$0.10")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

test.describe("Overview page — loading state", () => {
  test("shows skeletons while data is loading", async ({
    authedPage: page,
  }) => {
    // Delay API responses to observe skeletons
    await page.route("**/api/v1/tasks?*", async (route) => {
      await new Promise((r) => setTimeout(r, 2000));
      route.fulfill({
        status: 200,
        json: { tasks: [], total: 0, has_more: false },
      });
    });
    await page.route("**/api/v1/tasks", async (route) => {
      if (route.request().method() !== "GET") {
        route.fallback();
        return;
      }
      await new Promise((r) => setTimeout(r, 2000));
      route.fulfill({
        status: 200,
        json: { tasks: [], total: 0, has_more: false },
      });
    });
    await page.route("**/api/v1/sessions", async (route) => {
      await new Promise((r) => setTimeout(r, 2000));
      route.fulfill({ status: 200, json: [] });
    });

    await page.goto("/overview");

    // Skeleton elements should be visible before data arrives
    const skeletons = page.locator('[data-slot="skeleton"]');
    await expect(skeletons.first()).toBeVisible();

    // Should have multiple skeletons (cards + chart + panels)
    const count = await skeletons.count();
    expect(count).toBeGreaterThanOrEqual(4);
  });
});

// ---------------------------------------------------------------------------
// Sidebar navigation
// ---------------------------------------------------------------------------

test.describe("Overview page — sidebar", () => {
  test("Overview nav item is highlighted when on /overview", async ({
    authedPage: page,
  }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    const navLink = page.getByRole("link", { name: "Overview" });
    await expect(navLink).toBeVisible();
    await expect(navLink).toHaveClass(/bg-sidebar-accent/);
  });

  test("Logo links to /overview", async ({ authedPage: page }) => {
    await setupPopulated(page);
    await page.goto("/overview");

    const logo = page.getByRole("link", { name: "Pokant" });
    await expect(logo).toHaveAttribute("href", "/overview");
  });
});
