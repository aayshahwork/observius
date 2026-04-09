-- Migration 015: Failure taxonomy columns + artifact refs on task_steps, circuit breaker on tasks

-- Failure taxonomy on task_steps
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS failure_class text;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS patch_applied jsonb;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS validator_verdict text;

-- Artifact references on task_steps
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS har_ref text;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS trace_ref text;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS video_ref text;

-- Circuit breaker tracking on tasks
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS failure_counts jsonb DEFAULT '{}';

-- Index for failure analysis queries
CREATE INDEX IF NOT EXISTS idx_task_steps_failure_class
    ON task_steps(failure_class) WHERE failure_class IS NOT NULL;
