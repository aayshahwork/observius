/**
 * E2E: Sessions page — enhanced list, stats row, detail drawer, delete flow.
 *
 * Covers:
 *  - Stats row shows total / active / stale counts
 *  - Auth state badges with colored dots (green, amber, red)
 *  - Last Used column shows amber text for sessions > 7 days old
 *  - Clicking a row opens the detail drawer
 *  - Drawer shows domain, auth state, created/last used dates
 *  - Drawer shows staleness warning for stale sessions
 *  - Drawer shows recent tasks fetched by session_id
 *  - Delete flow: drawer → confirm dialog → session removed from list
 *  - Empty state when no sessions exist
 */

import {
  test,
  expect,
  mockSessionList,
  mockSessionDelete,
  mockTaskList,
  ALL_SESSIONS,
  ACTIVE_SESSION,
  STALE_SESSION,
  EXPIRED_SESSION,
  COMPLETED_TASK_FULL,
  FAILED_TASK_WITH_RETRY,
} from "./fixtures";
import type { SessionResponse } from "../src/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openSessionsPage(
  page: Parameters<typeof mockSessionList>[0],
  sessions: SessionResponse[] = ALL_SESSIONS
) {
  // Mock the validateKey call
  await page.route("**/api/v1/tasks?limit=1", (route) =>
    route.fulfill({
      status: 200,
      json: { tasks: [], total: 0, has_more: false },
    })
  );
  await mockSessionList(page, sessions);
  // Mock the tasks endpoint that the drawer may call (session_id filter)
  await mockTaskList(page, []);
  await page.goto("/sessions");
  // Wait for loading to complete — either the table or empty state appears
  await expect(
    page.getByText("Sessions").first()
  ).toBeVisible();
}

// ---------------------------------------------------------------------------
// Stats row
// ---------------------------------------------------------------------------

test.describe("Stats row", () => {
  test("shows total, active, and stale counts", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    // Total = 3
    await expect(page.getByText("Total").locator("..").getByText("3")).toBeVisible();
    // Active = 1
    await expect(page.getByText("Active").locator("..").getByText("1")).toBeVisible();
    // Stale = 1
    await expect(page.getByText("Stale").locator("..").getByText("1")).toBeVisible();
  });

  test("shows help text about session creation", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    await expect(
      page.getByText("Sessions are created automatically when tasks require login")
    ).toBeVisible();
  });

  test("stale count is amber-colored when > 0", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    // The stale count "1" is in a <p> with amber class, sibling of the "Stale" label
    const staleValue = page.getByText("Stale").locator("..").locator("p.text-lg");
    await expect(staleValue).toHaveClass(/text-amber/);
  });
});

// ---------------------------------------------------------------------------
// Auth state badges
// ---------------------------------------------------------------------------

test.describe("Auth state badges", () => {
  test("active session shows green dot", async ({ authedPage: page }) => {
    await openSessionsPage(page);

    const row = page.getByRole("row").filter({ hasText: "github.com" });
    const dot = row.locator("span.rounded-full");
    await expect(dot).toHaveClass(/bg-green-500/);
  });

  test("stale session shows amber dot", async ({ authedPage: page }) => {
    await openSessionsPage(page);

    const row = page.getByRole("row").filter({ hasText: "app.slack.com" });
    const dot = row.locator("span.rounded-full");
    await expect(dot).toHaveClass(/bg-amber-500/);
  });

  test("expired session shows red dot", async ({ authedPage: page }) => {
    await openSessionsPage(page);

    const row = page.getByRole("row").filter({ hasText: "mail.google.com" });
    const dot = row.locator("span.rounded-full");
    await expect(dot).toHaveClass(/bg-red-500/);
  });
});

// ---------------------------------------------------------------------------
// Last Used column
// ---------------------------------------------------------------------------

test.describe("Last Used column", () => {
  test("recent session shows default text color", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    const row = page.getByRole("row").filter({ hasText: "github.com" });
    const lastUsedCell = row.locator("td").nth(2);
    await expect(lastUsedCell.locator("span")).toHaveClass(/text-muted-foreground/);
  });

  test("session > 7 days old shows amber text", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    // Stale session last used 10 days ago
    const row = page.getByRole("row").filter({ hasText: "app.slack.com" });
    const lastUsedCell = row.locator("td").nth(2);
    await expect(lastUsedCell.locator("span")).toHaveClass(/text-amber/);
  });
});

// ---------------------------------------------------------------------------
// Session detail drawer
// ---------------------------------------------------------------------------

test.describe("Session detail drawer", () => {
  test("opens when a row is clicked", async ({ authedPage: page }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "github.com" }).click();

    await expect(page.locator("[data-slot='sheet-title']")).toHaveText("github.com");
  });

  test("shows auth state badge in drawer", async ({ authedPage: page }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "github.com" }).click();

    const drawer = page.locator("[data-slot='sheet-content']");
    await expect(drawer.getByText("Active")).toBeVisible();
    await expect(drawer.locator("span.rounded-full.bg-green-500")).toBeVisible();
  });

  test("shows created and last used dates", async ({ authedPage: page }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "github.com" }).click();

    const drawer = page.locator("[data-slot='sheet-content']");
    await expect(drawer.getByText("Created")).toBeVisible();
    await expect(drawer.getByText("Last Used")).toBeVisible();
    await expect(drawer.getByText("Mar 20, 2025")).toBeVisible();
  });

  test("shows staleness warning for stale sessions", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "app.slack.com" }).click();

    await expect(
      page.getByText("credentials may have expired")
    ).toBeVisible();
  });

  test("does NOT show staleness warning for active sessions", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "github.com" }).click();

    await expect(
      page.getByText("credentials may have expired")
    ).not.toBeVisible();
  });

  test("shows recent tasks fetched by session_id", async ({
    authedPage: page,
  }) => {
    // Set up route mocking manually for this test
    await page.route("**/api/v1/tasks?limit=1", (route) =>
      route.fulfill({
        status: 200,
        json: { tasks: [], total: 0, has_more: false },
      })
    );
    await mockSessionList(page, ALL_SESSIONS);

    // When the drawer requests tasks for ACTIVE_SESSION's session_id,
    // return two tasks.
    await page.route("**/api/v1/tasks?*", (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.get("session_id") === ACTIVE_SESSION.session_id) {
        route.fulfill({
          status: 200,
          json: {
            tasks: [COMPLETED_TASK_FULL, FAILED_TASK_WITH_RETRY],
            total: 2,
            has_more: false,
          },
        });
      } else {
        route.fulfill({
          status: 200,
          json: { tasks: [], total: 0, has_more: false },
        });
      }
    });

    await page.goto("/sessions");
    await expect(page.getByText("Sessions").first()).toBeVisible();

    await page.getByRole("row").filter({ hasText: "github.com" }).click();

    // Should show total count
    await expect(page.getByText("(2 total)")).toBeVisible();

    // Should show task status badges
    const drawer = page.locator("[data-slot='sheet-content']");
    await expect(drawer.locator("[data-slot='badge']").filter({ hasText: "Completed" })).toBeVisible();
    await expect(drawer.locator("[data-slot='badge']").filter({ hasText: "Failed" })).toBeVisible();
  });

  test("shows empty message when session has no tasks", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "github.com" }).click();

    await expect(
      page.getByText("No tasks have used this session yet")
    ).toBeVisible();
  });

  test("closes when the X button is clicked", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "github.com" }).click();
    await expect(page.locator("[data-slot='sheet-title']")).toBeVisible();

    // Close button
    await page.locator("[data-slot='sheet-close']").click();

    await expect(page.locator("[data-slot='sheet-title']")).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Delete flow through drawer
// ---------------------------------------------------------------------------

test.describe("Delete session via drawer", () => {
  test("delete button opens confirmation dialog", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "github.com" }).click();
    await page.getByRole("button", { name: "Delete Session" }).click();

    await expect(
      page.getByText("This will delete the session and its stored cookies")
    ).toBeVisible();
  });

  test("cancelling the dialog keeps the session", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page);

    await page.getByRole("row").filter({ hasText: "github.com" }).click();
    await page.getByRole("button", { name: "Delete Session" }).click();

    // Click Cancel in the confirm dialog
    await page.getByRole("button", { name: "Cancel" }).click();

    // Dialog should close but drawer stays open
    await expect(
      page.getByText("This will delete the session and its stored cookies")
    ).not.toBeVisible();
    await expect(page.locator("[data-slot='sheet-title']")).toBeVisible();
  });

  test("confirming delete removes session from list", async ({
    authedPage: page,
  }) => {
    let deleted = false;

    await page.route("**/api/v1/tasks?limit=1", (route) =>
      route.fulfill({
        status: 200,
        json: { tasks: [], total: 0, has_more: false },
      })
    );

    // DELETE handler flips the flag
    await page.route(`**/api/v1/sessions/${ACTIVE_SESSION.session_id}`, (route) => {
      if (route.request().method() !== "DELETE") {
        route.fallback();
        return;
      }
      deleted = true;
      route.fulfill({
        status: 200,
        json: { session_id: ACTIVE_SESSION.session_id, message: "Session deleted" },
      });
    });

    // GET returns different data before and after delete
    await page.route("**/api/v1/sessions", (route) => {
      if (route.request().method() !== "GET") {
        route.fallback();
        return;
      }
      const sessions = deleted ? [STALE_SESSION, EXPIRED_SESSION] : ALL_SESSIONS;
      route.fulfill({ status: 200, json: sessions });
    });
    await mockTaskList(page, []);

    await page.goto("/sessions");
    await expect(page.getByText("Sessions").first()).toBeVisible();

    // Verify github.com is present initially
    await expect(page.getByRole("row").filter({ hasText: "github.com" })).toBeVisible();

    // Open drawer and delete
    await page.getByRole("row").filter({ hasText: "github.com" }).click();
    await page.getByRole("button", { name: "Delete Session" }).click();
    await page.getByRole("button", { name: "Delete" }).click();

    // Drawer should close and session should be gone
    await expect(page.locator("[data-slot='sheet-title']")).not.toBeVisible();
    await expect(page.getByRole("row").filter({ hasText: "github.com" })).not.toBeVisible();

    // Stats should update: total=2
    await expect(page.getByText("Total").locator("..").getByText("2")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

test.describe("Empty state", () => {
  test("shows empty state when no sessions exist", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page, []);

    await expect(page.getByText("No sessions")).toBeVisible();
    await expect(
      page.getByText("Sessions are created automatically when tasks use authenticated browsing")
    ).toBeVisible();
  });

  test("stats row is NOT shown when no sessions exist", async ({
    authedPage: page,
  }) => {
    await openSessionsPage(page, []);

    await expect(page.getByText("Total")).not.toBeVisible();
  });
});
