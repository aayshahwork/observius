-- 003_audit_log_insert_policy.sql
-- Add INSERT policy on audit_log so writes succeed under non-superuser roles.
-- The table is append-only (UPDATE/DELETE already revoked), so unconditional
-- INSERT is safe — the backend is the sole writer.

CREATE POLICY audit_log_insert ON audit_log
    FOR INSERT
    WITH CHECK (true);
