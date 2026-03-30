-- 006_task_executor_mode.sql
-- Add executor_mode column to tasks table so it can be returned in API responses.
-- Previously executor_mode was only forwarded in the Celery message JSON and never persisted.

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS executor_mode VARCHAR(20) DEFAULT 'browser_use';
