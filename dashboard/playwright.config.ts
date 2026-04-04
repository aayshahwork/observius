import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for Pokant dashboard E2E tests.
 *
 * Run:
 *   npx playwright test                          # headless, all files
 *   npx playwright test --headed                 # with browser visible
 *   npx playwright test --debug                  # step through with inspector
 *   npx playwright test --trace on               # always capture traces
 *   npx playwright show-report                   # view HTML report
 *
 * Environment variables:
 *   DASHBOARD_URL   - Base URL of the running dashboard (default: http://localhost:3000)
 *   TEST_API_KEY    - Fake API key injected into localStorage (default: sk-test-key)
 *   API_BASE_URL    - Where the Next.js app proxies /api/v1/ requests (default: http://localhost:8000)
 */

const dashboardUrl = process.env.DASHBOARD_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [
    ["html", { outputFolder: "playwright-report", open: "never" }],
    ["junit", { outputFile: "playwright-results.xml" }],
    ["list"],
  ],
  use: {
    baseURL: dashboardUrl,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Start the Next.js dev server automatically when running tests locally.
  // In CI, start the server externally and set DASHBOARD_URL.
  webServer: process.env.CI
    ? undefined
    : {
        command: "npm run dev",
        url: dashboardUrl,
        reuseExistingServer: true,
        timeout: 60_000,
      },
});
