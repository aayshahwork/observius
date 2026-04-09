/**
 * E2E: New Task form — Execution Engine selector (3-way segmented control).
 *
 * Covers:
 *  - "Browser Use" card is selected by default
 *  - Clicking "Anthropic CUA" selects it and deselects Browser Use
 *  - Clicking "Skyvern" shows Skyvern-specific sub-fields
 *  - Skyvern sub-fields hidden when switching back to Browser Use
 *  - Form submits with skyvern executor_mode and skyvern_engine in POST body
 *  - Engine badges render correctly in the task table
 */

import {
  test,
  expect,
  mockTaskCreate,
  mockTaskList,
  mockTaskDetail,
  mockTaskReplay,
  COMPLETED_TASK_FULL,
  NATIVE_TASK,
  SKYVERN_TASK,
  SDK_TASK,
} from "./fixtures";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openNewTaskPage(page: Parameters<typeof mockTaskCreate>[0]) {
  await page.route("**/api/v1/tasks?limit=1", (route) =>
    route.fulfill({
      status: 200,
      json: { tasks: [], total: 0, has_more: false },
    })
  );
  await page.route("**/api/v1/sessions", (route) =>
    route.fulfill({ status: 200, json: [] })
  );
  await mockTaskList(page, []);
  await page.goto("/tasks/new");
}

// ---------------------------------------------------------------------------
// Engine selector cards
// ---------------------------------------------------------------------------

test.describe("Execution Engine selector", () => {
  test("Browser Use card is selected by default", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    const buCard = page.getByRole("button", { name: /Browser Use/ });
    await expect(buCard).toHaveClass(/ring-primary/);
  });

  test("selecting Anthropic CUA deselects Browser Use", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await page.getByRole("button", { name: /Anthropic CUA/ }).click();

    const cuaCard = page.getByRole("button", { name: /Anthropic CUA/ });
    const buCard = page.getByRole("button", { name: /Browser Use/ });
    await expect(cuaCard).toHaveClass(/ring-primary/);
    await expect(buCard).not.toHaveClass(/ring-primary/);
  });

  test("selecting Skyvern shows engine and proxy sub-fields", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    // Sub-fields not visible before selecting Skyvern
    await expect(page.getByLabel("Skyvern Engine")).not.toBeVisible();

    await page.getByRole("button", { name: /Skyvern/ }).click();

    await expect(page.getByLabel("Skyvern Engine")).toBeVisible();
    await expect(page.getByLabel("Proxy Location")).toBeVisible();
  });

  test("switching back from Skyvern hides sub-fields", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await page.getByRole("button", { name: /Skyvern/ }).click();
    await expect(page.getByLabel("Skyvern Engine")).toBeVisible();

    await page.getByRole("button", { name: /Browser Use/ }).click();
    await expect(page.getByLabel("Skyvern Engine")).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Form submission with Skyvern
// ---------------------------------------------------------------------------

test.describe("Form submission with Skyvern engine", () => {
  test("POST body includes executor_mode=skyvern", async ({
    authedPage: page,
  }) => {
    let capturedBody: Record<string, unknown> = {};

    await page.route("**/api/v1/tasks", (route) => {
      if (route.request().method() !== "POST") {
        route.continue();
        return;
      }
      capturedBody = JSON.parse(route.request().postData() ?? "{}");
      route.fulfill({ status: 201, json: SKYVERN_TASK });
    });
    await mockTaskList(page, []);
    await page.route(`**/api/v1/tasks/${SKYVERN_TASK.task_id}`, (route) =>
      route.fulfill({ status: 200, json: SKYVERN_TASK })
    );
    await page.route(`**/api/v1/tasks/${SKYVERN_TASK.task_id}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );
    await page.route(`**/api/v1/tasks/${SKYVERN_TASK.task_id}/steps`, (route) =>
      route.fulfill({ status: 200, json: [] })
    );

    await openNewTaskPage(page);

    await page.getByLabel("URL *").fill("https://example.com");
    await page.getByLabel("Task Description *").fill("Extract data via Skyvern");

    await page.getByRole("button", { name: "Advanced Options" }).click();
    await page.getByRole("button", { name: /Skyvern/ }).click();

    await page.getByRole("button", { name: "Create Task" }).click();
    await expect(page).toHaveURL(/\/tasks\//);

    expect(capturedBody.executor_mode).toBe("skyvern");
  });
});

// ---------------------------------------------------------------------------
// Engine badges in task table
// ---------------------------------------------------------------------------

test.describe("Engine badges in task table", () => {
  test("shows BU, CUA, SKY, SDK badges for different executor modes", async ({
    authedPage: page,
  }) => {
    await mockTaskList(page, [COMPLETED_TASK_FULL, NATIVE_TASK, SKYVERN_TASK, SDK_TASK]);
    await page.goto("/tasks");

    // Wait for table to render
    await expect(page.getByText("BU")).toBeVisible();
    await expect(page.getByText("CUA")).toBeVisible();
    await expect(page.getByText("SKY")).toBeVisible();
    await expect(page.getByText("SDK")).toBeVisible();
  });
});
