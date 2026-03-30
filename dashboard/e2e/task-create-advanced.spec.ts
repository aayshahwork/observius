/**
 * E2E: New Task form — Advanced Options collapsible section.
 *
 * Covers:
 *  - "Advanced Options" button toggles the section open and closed
 *  - Chevron rotates 180° when open
 *  - Executor mode radios: default is "browser_use", can switch to "native"
 *  - Max cost input accepts numeric values and shows help text
 *  - Form submits with executor_mode and max_cost_cents included in POST body
 *  - Form submits without those fields when advanced section is left at defaults
 */

import { test, expect, mockTaskCreate, mockTaskList, mockTaskDetail, mockTaskReplay, COMPLETED_TASK_FULL } from "./fixtures";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openNewTaskPage(page: Parameters<typeof mockTaskCreate>[0]) {
  // Intercept the validateKey call (listTasks with limit=1) that the auth hook
  // may fire before the page renders.
  await page.route("**/api/v1/tasks?limit=1", (route) =>
    route.fulfill({
      status: 200,
      json: { tasks: [], total: 0, has_more: false },
    })
  );
  // Mock sessions endpoint (new task page fetches sessions on mount)
  await page.route("**/api/v1/sessions", (route) =>
    route.fulfill({ status: 200, json: [] })
  );
  await mockTaskList(page, []);
  await page.goto("/tasks/new");
}

// ---------------------------------------------------------------------------
// Collapsible open / close
// ---------------------------------------------------------------------------

test.describe("Advanced Options collapsible", () => {
  test("is collapsed by default — interior content hidden", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);

    const advancedContent = page.locator("fieldset").filter({ hasText: "Executor Mode" });
    await expect(advancedContent).not.toBeVisible();
  });

  test("opens when the 'Advanced Options' button is clicked", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);

    await page.getByRole("button", { name: "Advanced Options" }).click();

    const advancedContent = page.locator("fieldset").filter({ hasText: "Executor Mode" });
    await expect(advancedContent).toBeVisible();
  });

  test("closes again when the button is clicked a second time", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);

    const toggle = page.getByRole("button", { name: "Advanced Options" });
    await toggle.click();
    await expect(page.locator("fieldset").filter({ hasText: "Executor Mode" })).toBeVisible();

    await toggle.click();
    await expect(page.locator("fieldset").filter({ hasText: "Executor Mode" })).not.toBeVisible();
  });

  test("chevron SVG rotates to 180° when section is open", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);

    const toggle = page.getByRole("button", { name: "Advanced Options" });
    const chevron = toggle.locator("svg");

    // Closed state: no rotate-180
    await expect(chevron).not.toHaveClass(/rotate-180/);

    await toggle.click();

    // Open state: rotate-180 applied
    await expect(chevron).toHaveClass(/rotate-180/);
  });
});

// ---------------------------------------------------------------------------
// Executor mode radios
// ---------------------------------------------------------------------------

test.describe("Executor Mode radio buttons", () => {
  test("'Browser Use (default)' radio is selected by default", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    const browserUseRadio = page.getByRole("radio", { name: "Browser Use (default)" });
    await expect(browserUseRadio).toBeChecked();
  });

  test("'Native Claude CUA' radio is unchecked by default", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    const nativeRadio = page.getByRole("radio", { name: "Native Claude CUA" });
    await expect(nativeRadio).not.toBeChecked();
  });

  test("selecting 'Native Claude CUA' unchecks 'Browser Use'", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await page.getByRole("radio", { name: "Native Claude CUA" }).click();

    await expect(page.getByRole("radio", { name: "Native Claude CUA" })).toBeChecked();
    await expect(page.getByRole("radio", { name: "Browser Use (default)" })).not.toBeChecked();
  });

  test("switching back to 'Browser Use' re-checks it", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await page.getByRole("radio", { name: "Native Claude CUA" }).click();
    await page.getByRole("radio", { name: "Browser Use (default)" }).click();

    await expect(page.getByRole("radio", { name: "Browser Use (default)" })).toBeChecked();
    await expect(page.getByRole("radio", { name: "Native Claude CUA" })).not.toBeChecked();
  });
});

// ---------------------------------------------------------------------------
// Max cost input
// ---------------------------------------------------------------------------

test.describe("Max cost input", () => {
  test("max cost input is visible when advanced section is open", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await expect(page.getByLabel("Max cost (cents)")).toBeVisible();
  });

  test("max cost input accepts a numeric value", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await page.getByLabel("Max cost (cents)").fill("50");
    await expect(page.getByLabel("Max cost (cents)")).toHaveValue("50");
  });

  test("help text is visible", async ({ authedPage: page }) => {
    await openNewTaskPage(page);
    await page.getByRole("button", { name: "Advanced Options" }).click();

    await expect(
      page.getByText("Task will stop if LLM cost exceeds this limit")
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Form submission includes new fields
// ---------------------------------------------------------------------------

test.describe("Form submission with advanced fields", () => {
  test("POST body includes executor_mode='native' when native radio selected", async ({
    authedPage: page,
  }) => {
    let capturedBody: Record<string, unknown> = {};

    await mockTaskList(page, []);
    await openNewTaskPage(page);

    // Register POST handler LAST so it has LIFO priority
    await page.route("**/api/v1/tasks", (route) => {
      if (route.request().method() !== "POST") { route.fallback(); return; }
      capturedBody = JSON.parse(route.request().postData() ?? "{}");
      route.fulfill({ status: 201, json: COMPLETED_TASK_FULL });
    });
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}`, (route) =>
      route.fulfill({ status: 200, json: COMPLETED_TASK_FULL })
    );
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.getByLabel("URL *").fill("https://example.com");
    await page.getByLabel("Task Description *").fill("Extract the heading");

    await page.getByRole("button", { name: "Advanced Options" }).click();
    await page.getByRole("radio", { name: "Native Claude CUA" }).click();

    await page.getByRole("button", { name: "Create Task" }).click();
    await expect(page).toHaveURL(/\/tasks\//);

    expect(capturedBody.executor_mode).toBe("native");
  });

  test("POST body includes max_cost_cents when a value is entered", async ({
    authedPage: page,
  }) => {
    let capturedBody: Record<string, unknown> = {};

    await mockTaskList(page, []);
    await openNewTaskPage(page);

    await page.route("**/api/v1/tasks", (route) => {
      if (route.request().method() !== "POST") { route.fallback(); return; }
      capturedBody = JSON.parse(route.request().postData() ?? "{}");
      route.fulfill({ status: 201, json: COMPLETED_TASK_FULL });
    });
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}`, (route) =>
      route.fulfill({ status: 200, json: COMPLETED_TASK_FULL })
    );
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.getByLabel("URL *").fill("https://example.com");
    await page.getByLabel("Task Description *").fill("Extract the heading");

    await page.getByRole("button", { name: "Advanced Options" }).click();
    await page.getByLabel("Max cost (cents)").fill("75");

    await page.getByRole("button", { name: "Create Task" }).click();
    await expect(page).toHaveURL(/\/tasks\//);

    expect(capturedBody.max_cost_cents).toBe(75);
  });

  test("POST body omits max_cost_cents when the field is left blank", async ({
    authedPage: page,
  }) => {
    let capturedBody: Record<string, unknown> = {};

    await page.route("**/api/v1/tasks", (route) => {
      if (route.request().method() !== "POST") {
        route.continue();
        return;
      }
      capturedBody = JSON.parse(route.request().postData() ?? "{}");
      route.fulfill({ status: 201, json: COMPLETED_TASK_FULL });
    });
    await mockTaskList(page, []);
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}`, (route) =>
      route.fulfill({ status: 200, json: COMPLETED_TASK_FULL })
    );
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await openNewTaskPage(page);

    await page.getByLabel("URL *").fill("https://example.com");
    await page.getByLabel("Task Description *").fill("Extract the heading");
    // Do NOT open advanced options

    await page.getByRole("button", { name: "Create Task" }).click();

    await expect(page).toHaveURL(/\/tasks\//);

    expect(capturedBody.max_cost_cents).toBeUndefined();
  });

  test("POST body defaults to executor_mode='browser_use' when not changed", async ({
    authedPage: page,
  }) => {
    let capturedBody: Record<string, unknown> = {};

    await mockTaskList(page, []);
    await openNewTaskPage(page);

    await page.route("**/api/v1/tasks", (route) => {
      if (route.request().method() !== "POST") { route.fallback(); return; }
      capturedBody = JSON.parse(route.request().postData() ?? "{}");
      route.fulfill({ status: 201, json: COMPLETED_TASK_FULL });
    });
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}`, (route) =>
      route.fulfill({ status: 200, json: COMPLETED_TASK_FULL })
    );
    await page.route(`**/api/v1/tasks/${COMPLETED_TASK_FULL.task_id}/replay`, (route) =>
      route.fulfill({ status: 404, json: { error_code: "NOT_FOUND", message: "No replay" } })
    );

    await page.getByLabel("URL *").fill("https://example.com");
    await page.getByLabel("Task Description *").fill("Extract the heading");

    await page.getByRole("button", { name: "Create Task" }).click();
    await expect(page).toHaveURL(/\/tasks\//);

    expect(capturedBody.executor_mode).toBe("browser_use");
  });
});

// ---------------------------------------------------------------------------
// Create button disabled state
// ---------------------------------------------------------------------------

test.describe("Create Task button disabled state", () => {
  test("button is disabled when URL and description are empty", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);

    await expect(page.getByRole("button", { name: "Create Task" })).toBeDisabled();
  });

  test("button becomes enabled when both URL and description are filled", async ({
    authedPage: page,
  }) => {
    await openNewTaskPage(page);

    await page.getByLabel("URL *").fill("https://example.com");
    await page.getByLabel("Task Description *").fill("Do something");

    await expect(page.getByRole("button", { name: "Create Task" })).toBeEnabled();
  });
});
