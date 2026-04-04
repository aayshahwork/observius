-- 001_initial_schema.sql
-- Pokant: Initial database schema
-- Creates uuid_generate_v7(), all tables, indexes, and RLS policies.

BEGIN;

-- Required for gen_random_bytes() used in uuid_generate_v7()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================================
-- a) uuid_generate_v7() — RFC 9562 UUIDv7 with temporal ordering
-- ============================================================================
CREATE OR REPLACE FUNCTION uuid_generate_v7()
RETURNS uuid
LANGUAGE plpgsql
VOLATILE PARALLEL SAFE
SET search_path = ''
AS $$
DECLARE
    ts_ms  bigint;
    uuid_bytes bytea;
BEGIN
    ts_ms := (extract(epoch FROM clock_timestamp()) * 1000)::bigint;

    -- Start with 16 random bytes (fully-qualified for SET search_path = '')
    uuid_bytes := public.gen_random_bytes(16);

    -- Overwrite first 6 bytes with 48-bit timestamp (big-endian)
    -- Mask to 0-255 BEFORE casting to int to avoid overflow
    uuid_bytes := set_byte(uuid_bytes, 0, ((ts_ms >> 40) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 1, ((ts_ms >> 32) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 2, ((ts_ms >> 24) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 3, ((ts_ms >> 16) & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 4, ((ts_ms >> 8)  & 255)::int);
    uuid_bytes := set_byte(uuid_bytes, 5, (ts_ms         & 255)::int);

    -- Set version nibble to 0x7 (byte 6, high nibble)
    uuid_bytes := set_byte(uuid_bytes, 6, (get_byte(uuid_bytes, 6) & 15) | 112);

    -- Set variant bits to 0b10xx_xxxx (byte 8)
    uuid_bytes := set_byte(uuid_bytes, 8, (get_byte(uuid_bytes, 8) & 63) | 128);

    RETURN encode(uuid_bytes, 'hex')::uuid;
END;
$$;


-- ============================================================================
-- b) accounts
-- ============================================================================
CREATE TABLE accounts (
    id                  uuid        PRIMARY KEY DEFAULT uuid_generate_v7(),
    email               text        UNIQUE NOT NULL,
    name                text        NOT NULL,
    tier                text        CHECK (tier IN ('free', 'startup', 'growth', 'enterprise')) DEFAULT 'free',
    stripe_customer_id  text        UNIQUE,
    monthly_step_limit  int         NOT NULL DEFAULT 500,
    monthly_steps_used  int         NOT NULL DEFAULT 0,
    encryption_key_id   text        NOT NULL,
    created_at          timestamptz DEFAULT now()
);


-- ============================================================================
-- c) api_keys
-- ============================================================================
CREATE TABLE api_keys (
    id          uuid        PRIMARY KEY DEFAULT uuid_generate_v7(),
    account_id  uuid        NOT NULL REFERENCES accounts(id),
    key_hash    text        NOT NULL UNIQUE,
    key_prefix  text        NOT NULL,
    key_suffix  text        NOT NULL,
    label       text,
    expires_at  timestamptz,
    revoked_at  timestamptz,
    created_at  timestamptz DEFAULT now()
);

CREATE INDEX idx_api_keys_account_id ON api_keys (account_id);


-- ============================================================================
-- d) tasks
-- ============================================================================
CREATE TABLE tasks (
    id                    uuid          PRIMARY KEY DEFAULT uuid_generate_v7(),
    account_id            uuid          NOT NULL REFERENCES accounts(id),
    status                text          CHECK (status IN ('queued', 'running', 'completed', 'failed', 'timeout', 'cancelled')),
    success               bool          DEFAULT false,
    url                   text          NOT NULL,
    task_description      text          NOT NULL CHECK (length(task_description) <= 2000),
    output_schema         jsonb,
    result                jsonb,
    error_code            text,
    error_message         text,
    model_used            text,
    total_steps           int           DEFAULT 0,
    duration_ms           int,
    total_tokens_in       int           DEFAULT 0,
    total_tokens_out      int           DEFAULT 0,
    cost_cents            numeric(10,4) DEFAULT 0,
    max_cost_cents        int           CHECK (max_cost_cents > 0 OR max_cost_cents IS NULL),
    cumulative_cost_cents numeric(10,4) DEFAULT 0,
    replay_s3_key         text,
    session_id            uuid,
    idempotency_key       text,
    webhook_url           text,
    webhook_delivered     bool          DEFAULT false,
    worker_id             text,
    created_at            timestamptz   DEFAULT now(),
    started_at            timestamptz,
    completed_at          timestamptz
);

CREATE INDEX idx_tasks_account_status_created
    ON tasks (account_id, status, created_at DESC);

CREATE UNIQUE INDEX idx_tasks_account_idempotency
    ON tasks (account_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX idx_tasks_created
    ON tasks (created_at DESC);


-- ============================================================================
-- e) task_steps
-- ============================================================================
CREATE TABLE task_steps (
    id                uuid        PRIMARY KEY DEFAULT uuid_generate_v7(),
    task_id           uuid        NOT NULL REFERENCES tasks(id),
    step_number       int         NOT NULL,
    action_type       text        NOT NULL,
    description       text,
    screenshot_s3_key text,
    llm_tokens_in     int         DEFAULT 0,
    llm_tokens_out    int         DEFAULT 0,
    duration_ms       int,
    success           bool        DEFAULT true,
    error_message     text,
    created_at        timestamptz DEFAULT now()
);

CREATE INDEX idx_task_steps_task_step
    ON task_steps (task_id, step_number);


-- ============================================================================
-- f) sessions
-- ============================================================================
CREATE TABLE sessions (
    id                uuid        PRIMARY KEY DEFAULT uuid_generate_v7(),
    account_id        uuid        NOT NULL REFERENCES accounts(id),
    origin_domain     text        NOT NULL,
    cookies_encrypted bytea       NOT NULL,
    auth_state        text        CHECK (auth_state IN ('active', 'stale', 'expired')),
    last_used_at      timestamptz DEFAULT now(),
    expires_at        timestamptz,
    created_at        timestamptz DEFAULT now(),
    UNIQUE (account_id, origin_domain)
);


-- ============================================================================
-- g) audit_log
-- ============================================================================
CREATE TABLE audit_log (
    id            uuid        PRIMARY KEY DEFAULT uuid_generate_v7(),
    account_id    uuid        NOT NULL REFERENCES accounts(id),
    actor_type    text        NOT NULL,
    actor_id      text        NOT NULL,
    action        text        NOT NULL,
    resource_type text        NOT NULL,
    resource_id   text        NOT NULL,
    metadata      jsonb,
    ip_address    inet,
    created_at    timestamptz DEFAULT now()
);

CREATE INDEX idx_audit_log_account_created
    ON audit_log (account_id, created_at DESC);

CREATE INDEX idx_audit_log_resource
    ON audit_log (resource_type, resource_id);


-- ============================================================================
-- h) Row Level Security
-- ============================================================================

-- accounts: RLS on id (not account_id)
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
CREATE POLICY accounts_tenant ON accounts
    USING (id = (SELECT current_setting('app.account_id')::uuid));

-- api_keys
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY api_keys_tenant ON api_keys
    USING (account_id = (SELECT current_setting('app.account_id')::uuid));

-- tasks
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY tasks_tenant ON tasks
    USING (account_id = (SELECT current_setting('app.account_id')::uuid));

-- task_steps: tenant via join to tasks
ALTER TABLE task_steps ENABLE ROW LEVEL SECURITY;
CREATE POLICY task_steps_tenant ON task_steps
    USING (task_id IN (
        SELECT id FROM tasks
        WHERE account_id = (SELECT current_setting('app.account_id')::uuid)
    ));

-- sessions
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY sessions_tenant ON sessions
    USING (account_id = (SELECT current_setting('app.account_id')::uuid));

-- audit_log: SELECT only — no UPDATE or DELETE
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_log_tenant ON audit_log
    FOR SELECT
    USING (account_id = (SELECT current_setting('app.account_id')::uuid));

-- Revoke UPDATE/DELETE on audit_log from all non-superuser roles
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;

-- Supabase-specific roles (skip if not present, e.g. in plain Docker Postgres)
DO $$ BEGIN
    EXECUTE 'REVOKE UPDATE, DELETE ON audit_log FROM anon';
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
DO $$ BEGIN
    EXECUTE 'REVOKE UPDATE, DELETE ON audit_log FROM authenticated';
EXCEPTION WHEN undefined_object THEN NULL;
END $$;


COMMIT;
