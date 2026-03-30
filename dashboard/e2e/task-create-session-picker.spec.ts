/**
 * E2E: Task creation form — Session picker in Advanced Options.
 *
 * Covers:
 *  - Session picker visible in Advanced Options when sessions exist
 *  - Session picker hidden when no sessions exist
 *  - Active/stale sessions shown with auth state dots, expired filtered out
 *  - Selecting a session includes session_id in POST body
 *  - Default "Auto" option sends no session_id
 */

import {
  test,
  expect,
  mockTaskList,
  mockSessionList,
  ALL_SESSIONS,
  ACTIVE_SESSION,
  STALE_SESSION,
  COMPLETED_TASK_FULL,
} from "./fixtures";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openNewTaskPage(
  page: Parameters<typeof mockSessionList>[0],
  sessions = ALL_SESSIONS
) {
  // Mock the validateKey call
  await page.route("**/api/v1/tasks?limit=1", (route) =>
    route.fulfill({
      status: 200,
      json: { tasks: [], total: 0, has_more: false },
    })
  );
  await mockTaskList(page, []);
  await mockSessionList(page, sessions);
  await page.goto("/tasks/new");
}

// ---------------------------------------------------------------------------
// Session picker visibility
// ---------------------------------------------------------------------------

test.describe("Session picker visibility", () => {
  test("session picker is visible in Advanced Options when sessions exist", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);

    await page.getByRole("button", { name: "Advanced Options" }).click();

    await expect(page.getByText("Session (optional)")).toBeVisible();
    await expect(
      page.getByText("Reuse an authenticated session to skip login")
    ).toBeVisible();
  });

  test("session picker is NOT visible when no sessions exist", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page, []);

    await page.getByRole("button", { name: "Advanced Options" }).click();

    await expect(page.getByText("Session (optional)")).not.toBeVisible();
  });

  test("session picker is hidden when Advanced Options is collapsed", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);

    await expect(page.getByText("Session (optional)")).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Session options content
// ---------------------------------------------------------------------------

test.describe("Session picker options", () => {
  test("shows active session with green dot", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    // Open the select dropdown
    await page.getByText("Auto — no session").click();

    // Active session should be listed
    const option = page.getByRole("option").filter({ hasText: "github.com" });
    await expect(option).toBeVisible();
    await expect(option.locator("span.rounded-full")).toHaveClass(/bg-green-500/);
  });

  test("shows stale session with amber dot", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await page.getByText("Auto — no session").click();

    const option = page.getByRole("option").filter({ hasText: "app.slack.com" });
    await expect(option).toBeVisible();
    await expect(option.locator("span.rounded-full")).toHaveClass(/bg-amber-500/);
  });

  test("does NOT show expired sessions", async ({ authedPage: page }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await page.getByText("Auto — no session").click();

    // mail.google.com is expired and should be filtered out
    await expect(
      page.getByRole("option").filter({ hasText: "mail.google.com" })
    ).not.toBeVisible();
  });

  test("shows 'Auto' as the default option", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await page.getByText("Auto — no session").click();

    await expect(page.getByRole("option").filter({ hasText: "Auto" })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Form submission with session_id
// ---------------------------------------------------------------------------

test.describe("Form submission with session picker", () => {
  test("POST body includes session_id when a session is selected", async ({
    authedPage: page,
  }) => {
    let capturedBody: Record<string, unknown> = {};

    await page.route("**/api/v1/tasks", (route) => {
      if (route.request().method() !== "POST") {
        route.fallback();
        return;
      }
      capturedBody = JSON.parse(route.request().postData() ?? "{}");
      route.fulfill({ status: 201, json: COMPLETED_TASK_FULL });
    });
    await page.route("**/api/v1/tasks?limit=1", (route) =>
      route.fulfill({
        status: 200,
        json: { tasks: [], total: 0, has_more: false },
      })
    );
    await page.route("**/api/v1/tasks?*", (route) => {
      const url = route.request().url();
      if (url.includes("limit=1")) {
        route.fulfill({
          status: 200,
          json: { tasks: [], total: 0, has_more: false },
        });
        return;
      }
      route.fallback();
    });
    await mockSessionList(page, ALL_SESSIONS);
    // Mock redirect target
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}`, (route) =>
      route.fulfill({ status: 200, json: COMPLETED_TASK_FULL })
    );
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto("/tasks/new");

    // Fill required fields
    await page.getByLabel("URL *").fill("https://example.com");
    await page.getByLabel("Task Description *").fill("Extract data");

    // Open advanced and select a session
    await page.getByRole("button", { name: "Advanced Options" }).click();
    await page.getByText("Auto — no session").click();
    await page.getByRole("option").filter({ hasText: "github.com" }).click();

    await page.getByRole("button", { name: "Create Task" }).click();

    await expect(page).toHaveURL(/\/tasks\//);

    expect(capturedBody.session_id).toBe(ACTIVE_SESSION.session_id);
  });

  test("POST body omits session_id when 'Auto' is selected", async ({
    authedPage: page,
  }) => {
    let capturedBody: Record<string, unknown> = {};

    await page.route("**/api/v1/tasks", (route) => {
      if (route.request().method() !== "POST") {
        route.fallback();
        return;
      }
      capturedBody = JSON.parse(route.request().postData() ?? "{}");
      route.fulfill({ status: 201, json: COMPLETED_TASK_FULL });
    });
    await page.route("**/api/v1/tasks?limit=1", (route) =>
      route.fulfill({
        status: 200,
        json: { tasks: [], total: 0, has_more: false },
      })
    );
    await mockSessionList(page, ALL_SESSIONS);
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}`, (route) =>
      route.fulfill({ status: 200, json: COMPLETED_TASK_FULL })
    );
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.goto("/tasks/new");

    await page.getByLabel("URL *").fill("https://example.com");
    await page.getByLabel("Task Description *").fill("Extract data");

    // Don't open advanced options — leave session at default
    await page.getByRole("button", { name: "Create Task" }).click();

    await expect(page).toHaveURL(/\/tasks\//);

    expect(capturedBody.session_id).toBeUndefined();
  });
});
