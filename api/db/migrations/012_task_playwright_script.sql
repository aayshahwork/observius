-- 012: Add playwright_script column to tasks table.
-- Stores the generated Playwright script when a user saves it from the dashboard.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS playwright_script TEXT;
