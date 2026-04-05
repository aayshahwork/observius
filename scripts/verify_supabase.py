"""
Verify Supabase database schema matches all 12 Pokant migrations.

Usage:
    python scripts/verify_supabase.py "postgresql://postgres.[ref]:[pw]@db.[ref].supabase.co:5432/postgres"

Or set the env var:
    export DATABASE_URL="postgresql://..."
    python scripts/verify_supabase.py
"""

from __future__ import annotations

import sys

try:
    import psycopg2  # type: ignore[import-untyped]
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Expected schema — derived from migrations 001-012
# ---------------------------------------------------------------------------

EXPECTED_TABLES: dict[str, list[str]] = {
    "accounts": [
        "id",
        "email",
        "name",
        "tier",
        "stripe_customer_id",
        "monthly_step_limit",
        "monthly_steps_used",
        "encryption_key_id",
        "created_at",
        # 005
        "webhook_secret",
    ],
    "api_keys": [
        "id",
        "account_id",
        "key_hash",
        "key_prefix",
        "key_suffix",
        "label",
        "expires_at",
        "revoked_at",
        "created_at",
    ],
    "tasks": [
        "id",
        "account_id",
        "status",
        "success",
        "url",
        "task_description",
        "output_schema",
        "result",
        "error_code",
        "error_message",
        "model_used",
        "total_steps",
        "duration_ms",
        "total_tokens_in",
        "total_tokens_out",
        "cost_cents",
        "max_cost_cents",
        "cumulative_cost_cents",
        "replay_s3_key",
        "session_id",
        "idempotency_key",
        "webhook_url",
        "webhook_delivered",
        "worker_id",
        "created_at",
        "started_at",
        "completed_at",
        # 004
        "retry_count",
        "retry_of_task_id",
        "error_category",
        # 006
        "executor_mode",
        # 010
        "analysis_json",
        # 011
        "compiled_workflow_json",
        # 012
        "playwright_script",
    ],
    "task_steps": [
        "id",
        "task_id",
        "step_number",
        "action_type",
        "description",
        "screenshot_s3_key",
        "llm_tokens_in",
        "llm_tokens_out",
        "duration_ms",
        "success",
        "error_message",
        "created_at",
        # 009
        "context",
    ],
    "sessions": [
        "id",
        "account_id",
        "origin_domain",
        "cookies_encrypted",
        "auth_state",
        "last_used_at",
        "expires_at",
        "created_at",
    ],
    "audit_log": [
        "id",
        "account_id",
        "actor_type",
        "actor_id",
        "action",
        "resource_type",
        "resource_id",
        "metadata",
        "ip_address",
        "created_at",
    ],
    "alerts": [
        "id",
        "account_id",
        "alert_type",
        "message",
        "task_id",
        "acknowledged",
        "created_at",
    ],
}

EXPECTED_INDEXES = [
    # 001
    "idx_api_keys_account_id",
    "idx_tasks_account_status_created",
    "idx_tasks_account_idempotency",
    "idx_tasks_created",
    "idx_task_steps_task_step",
    "idx_audit_log_account_created",
    "idx_audit_log_resource",
    # 004
    "idx_tasks_retry_of",
    # 007
    "idx_alerts_account_unacked",
    "idx_alerts_account_type_recent",
    # 008
    "idx_tasks_analytics",
]

EXPECTED_RLS_TABLES = [
    "accounts",
    "api_keys",
    "tasks",
    "task_steps",
    "sessions",
    "audit_log",
    "alerts",
]

EXPECTED_POLICIES = {
    "accounts": ["accounts_tenant"],
    "api_keys": ["api_keys_tenant"],
    "tasks": ["tasks_tenant"],
    "task_steps": ["task_steps_tenant"],
    "sessions": ["sessions_tenant"],
    "audit_log": ["audit_log_tenant", "audit_log_insert"],
    "alerts": ["alerts_account_isolation", "alerts_insert_policy"],
}

EXPECTED_FUNCTIONS = ["uuid_generate_v7"]


# ---------------------------------------------------------------------------
# Verification logic
# ---------------------------------------------------------------------------

def get_dsn() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    import os
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("Usage: python scripts/verify_supabase.py <connection-string>")
        print("   or: export DATABASE_URL=... && python scripts/verify_supabase.py")
        sys.exit(1)
    # Strip asyncpg prefix if someone passes the SQLAlchemy URL
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def main() -> None:
    dsn = get_dsn()

    # Mask password in display
    display_dsn = dsn
    if "@" in dsn and ":" in dsn.split("@")[0]:
        prefix, rest = dsn.split("@", 1)
        user_part = prefix.rsplit(":", 1)[0]
        display_dsn = f"{user_part}:****@{rest}"

    print(f"Connecting to: {display_dsn}\n")

    try:
        conn = psycopg2.connect(dsn, connect_timeout=10)
    except Exception as e:
        print(f"[FAIL] Connection failed: {e}")
        sys.exit(1)

    print("[PASS] Connected to database\n")
    cur = conn.cursor()

    total_pass = 0
    total_fail = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total_pass, total_fail
        if ok:
            total_pass += 1
            print(f"  [PASS] {label}")
        else:
            total_fail += 1
            msg = f"  [FAIL] {label}"
            if detail:
                msg += f" — {detail}"
            print(msg)

    # --- 1. Extension: pgcrypto ---
    print("== Extensions ==")
    cur.execute(
        "SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto';"
    )
    check(cur.fetchone() is not None, "pgcrypto extension")

    # --- 2. Function: uuid_generate_v7 ---
    print("\n== Functions ==")
    for fn in EXPECTED_FUNCTIONS:
        cur.execute(
            "SELECT 1 FROM pg_proc WHERE proname = %s;", (fn,)
        )
        check(cur.fetchone() is not None, f"function {fn}()")

    # Quick smoke test: call it
    try:
        cur.execute("SELECT uuid_generate_v7();")
        val = cur.fetchone()[0]
        check(val is not None, f"uuid_generate_v7() returns value: {val}")
    except Exception as e:
        check(False, "uuid_generate_v7() callable", str(e))
        conn.rollback()

    # --- 3. Tables and columns ---
    print("\n== Tables & Columns ==")
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """
    )
    existing_tables = {row[0] for row in cur.fetchall()}

    for table, expected_cols in EXPECTED_TABLES.items():
        exists = table in existing_tables
        check(exists, f"table: {table}")
        if not exists:
            continue

        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position;
            """,
            (table,),
        )
        actual_cols = {row[0] for row in cur.fetchall()}
        missing = [c for c in expected_cols if c not in actual_cols]
        extra = sorted(actual_cols - set(expected_cols))

        if missing:
            check(False, f"  {table} columns", f"missing: {missing}")
        else:
            check(True, f"  {table} — all {len(expected_cols)} columns present")
        if extra:
            print(f"         (extra columns OK: {extra})")

    # --- 4. Indexes ---
    print("\n== Indexes ==")
    cur.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public';"
    )
    existing_indexes = {row[0] for row in cur.fetchall()}

    for idx in EXPECTED_INDEXES:
        check(idx in existing_indexes, f"index: {idx}")

    # --- 5. RLS ---
    print("\n== Row Level Security ==")
    cur.execute(
        """
        SELECT tablename, rowsecurity FROM pg_tables
        WHERE schemaname = 'public';
        """
    )
    rls_map = {row[0]: row[1] for row in cur.fetchall()}

    for table in EXPECTED_RLS_TABLES:
        enabled = rls_map.get(table, False)
        check(enabled, f"RLS enabled: {table}")

    # --- 6. Policies ---
    print("\n== RLS Policies ==")
    cur.execute(
        """
        SELECT tablename, policyname FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY tablename, policyname;
        """
    )
    policy_map: dict[str, set[str]] = {}
    for row in cur.fetchall():
        policy_map.setdefault(row[0], set()).add(row[1])

    for table, expected in EXPECTED_POLICIES.items():
        actual = policy_map.get(table, set())
        for pol in expected:
            check(pol in actual, f"policy: {table}.{pol}")

    # --- 7. Seed data (optional) ---
    print("\n== Seed Data (migration 002) ==")
    try:
        # Use superuser bypass — RLS would block without SET LOCAL
        cur.execute("SET LOCAL row_security = off;")
        cur.execute(
            "SELECT id, email, tier FROM accounts WHERE email = 'test@pokant.dev';"
        )
        row = cur.fetchone()
        if row:
            check(True, f"test account exists: {row[0]} ({row[2]} tier)")
            cur.execute(
                "SELECT key_prefix, label FROM api_keys WHERE account_id = %s;",
                (row[0],),
            )
            key_row = cur.fetchone()
            check(key_row is not None, f"test API key exists: {key_row}")
        else:
            print("  [SKIP] No seed data (002 not run or skipped for prod)")
        conn.rollback()  # Reset the SET LOCAL
    except Exception as e:
        print(f"  [SKIP] Seed check failed: {e}")
        conn.rollback()

    # --- Summary ---
    total = total_pass + total_fail
    print(f"\n{'=' * 50}")
    print(f"RESULTS: {total_pass}/{total} passed, {total_fail} failed")
    if total_fail == 0:
        print("All checks passed! Database is production-ready.")
    else:
        print("Fix the failures above before deploying.")
    print(f"{'=' * 50}")

    cur.close()
    conn.close()
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
