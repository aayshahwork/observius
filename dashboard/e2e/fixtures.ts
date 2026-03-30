/**
 * Shared fixtures and helpers for Observius dashboard E2E tests.
 *
 * All tests use route interception (page.route) to mock API responses so they
 * run without a live backend.
 */

import { test as base, expect, type Page } from "@playwright/test";
import type { TaskResponse, TaskListResponse, SessionResponse } from "../src/lib/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const FAKE_API_KEY = "sk-test-fake-key-for-e2e";

/** A completed task that has all new fields populated. */
export const COMPLETED_TASK_FULL: TaskResponse = {
  task_id: "aaaa0001-0000-7000-8000-000000000001",
  url: "https://example.com",
  status: "completed",
  success: true,
  result: { heading: "Example Domain" },
  error: null,
  replay_url: "https://example.com",
  steps: 5,
  duration_ms: 12_340,
  created_at: "2025-03-28T10:00:00.000Z",
  completed_at: "2025-03-28T10:00:12.340Z",
  retry_count: 0,
  retry_of_task_id: null,
  error_category: null,
  cost_cents: 3,
  total_tokens_in: 1_200,
  total_tokens_out: 800,
  executor_mode: "browser_use",
};

/** A failed task with all error-related fields populated. */
export const FAILED_TASK_WITH_RETRY: TaskResponse = {
  task_id: "bbbb0002-0000-7000-8000-000000000002",
  url: "https://example.com/login",
  status: "failed",
  success: false,
  result: null,
  error: "LLM API returned 503 Service Unavailable",
  replay_url: null,
  steps: 2,
  duration_ms: 4_000,
  created_at: "2025-03-28T09:00:00.000Z",
  completed_at: "2025-03-28T09:00:04.000Z",
  retry_count: 2,
  retry_of_task_id: "eeee0099-0000-7000-8000-000000000099",
  error_category: "transient_llm",
  cost_cents: 1,
  total_tokens_in: 400,
  total_tokens_out: 200,
  executor_mode: "native",
};

/** A task with no optional fields — simulates old data before the new columns. */
export const COMPLETED_TASK_MINIMAL: TaskResponse = {
  task_id: "cccc0003-0000-7000-8000-000000000003",
  url: null,
  status: "completed",
  success: true,
  result: null,
  error: null,
  replay_url: null,
  steps: 1,
  duration_ms: 1_000,
  created_at: "2025-03-27T08:00:00.000Z",
  completed_at: "2025-03-27T08:00:01.000Z",
  retry_count: 0,
  retry_of_task_id: null,
  error_category: null,
  cost_cents: 0,
  total_tokens_in: 0,
  total_tokens_out: 0,
  executor_mode: "browser_use",
};

/** A native-executor task used to verify the "N" badge in the table. */
export const NATIVE_TASK: TaskResponse = {
  task_id: "dddd0004-0000-7000-8000-000000000004",
  url: "https://native.example.com",
  status: "completed",
  success: true,
  result: null,
  error: null,
  replay_url: null,
  steps: 3,
  duration_ms: 5_000,
  created_at: "2025-03-28T11:00:00.000Z",
  completed_at: "2025-03-28T11:00:05.000Z",
  retry_count: 0,
  retry_of_task_id: null,
  error_category: null,
  cost_cents: 75,
  total_tokens_in: 2_500,
  total_tokens_out: 1_800,
  executor_mode: "native",
};

// ---------------------------------------------------------------------------
// Session fixtures
// ---------------------------------------------------------------------------

/** An active (authenticated) session, last used recently. */
export const ACTIVE_SESSION: SessionResponse = {
  session_id: "sess-0001-0000-0000-000000000001",
  origin_domain: "github.com",
  auth_state: "active",
  last_used_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(), // 2 hours ago
  expires_at: new Date(Date.now() + 7 * 86400_000).toISOString(),
  created_at: "2025-03-20T10:00:00.000Z",
};

/** A stale session — credentials may need re-auth. */
export const STALE_SESSION: SessionResponse = {
  session_id: "sess-0002-0000-0000-000000000002",
  origin_domain: "app.slack.com",
  auth_state: "stale",
  last_used_at: new Date(Date.now() - 10 * 86400_000).toISOString(), // 10 days ago
  expires_at: new Date(Date.now() - 86400_000).toISOString(),
  created_at: "2025-03-10T08:00:00.000Z",
};

/** An expired session. */
export const EXPIRED_SESSION: SessionResponse = {
  session_id: "sess-0003-0000-0000-000000000003",
  origin_domain: "mail.google.com",
  auth_state: "expired",
  last_used_at: new Date(Date.now() - 30 * 86400_000).toISOString(), // 30 days ago
  expires_at: new Date(Date.now() - 20 * 86400_000).toISOString(),
  created_at: "2025-02-15T12:00:00.000Z",
};

/** All three sessions for testing the full list. */
export const ALL_SESSIONS: SessionResponse[] = [
  ACTIVE_SESSION,
  STALE_SESSION,
  EXPIRED_SESSION,
];

// ---------------------------------------------------------------------------
// Auth helper
// ---------------------------------------------------------------------------

/**
 * Seeds the API key into localStorage so the AuthProvider considers the user
 * authenticated without going through the login page.
 */
export async function seedAuth(page: Page): Promise<void> {
  await page.addInitScript((key) => {
    localStorage.setItem("computeruse_api_key", key);
  }, FAKE_API_KEY);
}

// ---------------------------------------------------------------------------
// Route mocking helpers
// ---------------------------------------------------------------------------

/**
 * Intercepts GET /api/v1/tasks* and returns the supplied list payload.
 */
export async function mockTaskList(
  page: Page,
  tasks: TaskResponse[],
  total?: number
): Promise<void> {
  const payload: TaskListResponse = {
    tasks,
    total: total ?? tasks.length,
    has_more: false,
  };
  await page.route("**/api/v1/tasks?*", (route) =>
    route.fulfill({ status: 200, json: payload })
  );
  // Also match the bare endpoint (no query string).
  // Use fallback() for non-GET so POST handlers registered elsewhere still fire.
  await page.route("**/api/v1/tasks", (route) => {
    if (route.request().method() !== "GET") {
      route.fallback();
      return;
    }
    route.fulfill({ status: 200, json: payload });
  });
}

/**
 * Intercepts GET /api/v1/tasks/:id and returns the supplied task.
 */
export async function mockTaskDetail(
  page: Page,
  task: TaskResponse
): Promise<void> {
  await page.route(`**/api/v1/tasks/${task.task_id}`, (route) => {
    if (route.request().method() !== "GET") {
      route.continue();
      return;
    }
    route.fulfill({ status: 200, json: task });
  });
}

/**
 * Intercepts GET /api/v1/tasks/:id/replay and returns a dummy replay URL.
 */
export async function mockTaskReplay(
  page: Page,
  taskId: string,
  replayUrl = "https://replay.example.com/task"
): Promise<void> {
  await page.route(`**/api/v1/tasks/${taskId}/replay`, (route) =>
    route.fulfill({
      status: 200,
      json: { task_id: taskId, replay_url: replayUrl },
    })
  );
}

/**
 * Intercepts POST /api/v1/tasks and returns the supplied task (or a default).
 */
export async function mockTaskCreate(
  page: Page,
  response: TaskResponse = COMPLETED_TASK_FULL
): Promise<void> {
  await page.route("**/api/v1/tasks", (route) => {
    if (route.request().method() !== "POST") {
      route.continue();
      return;
    }
    route.fulfill({ status: 201, json: response });
  });
}

// ---------------------------------------------------------------------------
// Session route mocking helpers
// ---------------------------------------------------------------------------

/**
 * Intercepts GET /api/v1/sessions and returns the supplied sessions.
 */
export async function mockSessionList(
  page: Page,
  sessions: SessionResponse[]
): Promise<void> {
  await page.route("**/api/v1/sessions", (route) => {
    if (route.request().method() !== "GET") {
      route.fallback();
      return;
    }
    route.fulfill({ status: 200, json: sessions });
  });
}

/**
 * Intercepts DELETE /api/v1/sessions/:id and returns success.
 */
export async function mockSessionDelete(
  page: Page,
  sessionId: string
): Promise<void> {
  await page.route(`**/api/v1/sessions/${sessionId}`, (route) => {
    if (route.request().method() !== "DELETE") {
      route.fallback();
      return;
    }
    route.fulfill({
      status: 200,
      json: { session_id: sessionId, message: "Session deleted" },
    });
  });
}

// ---------------------------------------------------------------------------
// Custom fixture type
// ---------------------------------------------------------------------------

type DashboardFixtures = {
  /** Page with auth pre-seeded and standard mocks in place. */
  authedPage: Page;
};

export const test = base.extend<DashboardFixtures>({
  authedPage: async ({ page }, use) => {
    await seedAuth(page);
    await use(page);
  },
});

export { expect };
