-- 011: Add compiled_workflow_json column to tasks table.
-- Stores the compiled workflow JSON for successful runs (explore-to-replay).
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS compiled_workflow_json JSONB;
