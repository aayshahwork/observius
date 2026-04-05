#!/usr/bin/env python3
"""
scripts/test_billing.py — Billing tests against the live API + Postgres.

Tests:
  1. Register a fresh free-tier account
  2. Simulate 500 step increments via DB (atomic SQL from usage_tracker)
  3. Verify the 501st step is rejected (402 QUOTA_EXCEEDED)
  4. Test checkout endpoint returns a URL (or correct error)
  5. Test usage endpoint returns current tier info

Usage:
    python scripts/test_billing.py

Requires: httpx  (pip install httpx)
DB access via: docker exec <postgres-container> psql
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
PG_CONTAINER = os.environ.get("PG_CONTAINER", "")

PASS = 0
FAIL = 0


def ok(label: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))


def fail(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


def section(name: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def _find_pg_container() -> str:
    """Auto-detect the postgres container name."""
    global PG_CONTAINER
    if PG_CONTAINER:
        return PG_CONTAINER
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    for name in result.stdout.strip().split("\n"):
        if "postgres" in name:
            PG_CONTAINER = name
            return name
    print("  ERROR: No postgres container found. Set PG_CONTAINER env var.")
    sys.exit(1)


def sql(query: str) -> str:
    """Execute SQL via docker exec psql and return raw output."""
    container = _find_pg_container()
    result = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "postgres", "-d", "computeruse",
         "-t", "-A", "-c", query],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def sql_val(query: str) -> str | None:
    """Execute SQL and return a single value (first line of output)."""
    out = sql(query)
    if not out:
        return None
    # psql -t -A may append "UPDATE N" on a second line for RETURNING queries
    first_line = out.split("\n")[0].strip()
    return first_line if first_line else None


def main() -> None:
    client = httpx.Client(base_url=BASE_URL, timeout=30)

    # ------------------------------------------------------------------
    # 0. Health check
    # ------------------------------------------------------------------
    section("0. Health check")
    try:
        resp = client.get("/health")
        if resp.status_code == 200:
            ok("API is up")
        else:
            fail("API health check", f"{resp.status_code}")
            sys.exit(1)
    except httpx.ConnectError:
        fail("Cannot connect to API")
        sys.exit(1)

    _find_pg_container()
    ok(f"Postgres container: {PG_CONTAINER}")

    # ------------------------------------------------------------------
    # 1. Register a fresh free-tier account
    # ------------------------------------------------------------------
    section("1. Register fresh account")
    email = f"billing_test_{uuid.uuid4().hex[:8]}@test.dev"
    resp = client.post("/api/v1/auth/register", json={"email": email})
    if resp.status_code != 201:
        fail("Registration failed", resp.text[:200])
        sys.exit(1)

    data = resp.json()
    api_key = data["api_key"]
    account_id = data["account_id"]
    ok("Registered", f"email={email}")
    ok(f"Got API key: {api_key[:12]}...")
    if data["tier"] == "free" and data["monthly_step_limit"] == 500:
        ok("Free tier with 500 step limit")
    else:
        fail("Wrong tier/limit", f"tier={data['tier']} limit={data['monthly_step_limit']}")

    headers = {"X-API-Key": api_key}

    # ------------------------------------------------------------------
    # 2. Simulate 500 step increments via DB
    # ------------------------------------------------------------------
    section("2. Simulate usage via DB (atomic increment)")

    # Set usage to 499
    sql(f"UPDATE accounts SET monthly_steps_used = 499 WHERE id = '{account_id}'")
    val = sql_val(
        f"SELECT monthly_steps_used FROM accounts WHERE id = '{account_id}'"
    )
    if val == "499":
        ok("Set monthly_steps_used=499")
    else:
        fail("DB update failed", f"got {val}")

    # Atomic increment 499→500 (should succeed)
    val = sql_val(
        f"UPDATE accounts "
        f"SET monthly_steps_used = monthly_steps_used + 1 "
        f"WHERE id = '{account_id}' "
        f"  AND monthly_steps_used + 1 <= monthly_step_limit "
        f"RETURNING monthly_steps_used"
    )
    if val == "500":
        ok("Atomic increment 499→500 succeeded")
    else:
        fail("Atomic increment to 500 failed", f"got {val}")

    # Atomic increment 500→501 (should fail — no rows returned)
    val = sql_val(
        f"UPDATE accounts "
        f"SET monthly_steps_used = monthly_steps_used + 1 "
        f"WHERE id = '{account_id}' "
        f"  AND monthly_steps_used + 1 <= monthly_step_limit "
        f"RETURNING monthly_steps_used"
    )
    # When no rows match the WHERE, psql returns empty or "UPDATE 0"
    if val is None or val == "" or val.startswith("UPDATE"):
        ok("Atomic increment 500→501 correctly rejected")
    else:
        fail("501st step was allowed!", f"got {val}")

    # ------------------------------------------------------------------
    # 3. Verify API returns 402 QUOTA_EXCEEDED
    # ------------------------------------------------------------------
    section("3. Verify 402 quota enforcement via API")

    resp = client.get("/api/v1/billing/usage", headers=headers)
    usage = resp.json()
    print(f"  INFO  used={usage['monthly_steps_used']}/{usage['monthly_step_limit']}")

    if usage["monthly_steps_used"] >= usage["monthly_step_limit"]:
        ok("Account is at quota limit")
    else:
        fail("Account not at limit")

    resp = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"url": "https://example.com", "task": "Should be blocked"},
    )
    if resp.status_code == 402:
        body = resp.json()
        if body.get("detail", {}).get("error_code") == "QUOTA_EXCEEDED":
            ok("POST /tasks returns 402 QUOTA_EXCEEDED")
        else:
            fail("Wrong error code in 402", str(body)[:200])
    else:
        fail(f"Expected 402, got {resp.status_code}", resp.text[:200])

    # ------------------------------------------------------------------
    # 4. Test checkout endpoint
    # ------------------------------------------------------------------
    section("4. Test checkout endpoint")

    resp = client.post(
        "/api/v1/billing/checkout",
        headers=headers,
        json={"tier": "startup"},
    )
    if resp.status_code == 200:
        url = resp.json().get("checkout_url", "")
        ok("Checkout returned URL", url[:60])
    elif resp.status_code == 400 and "No price configured" in resp.text:
        ok("Checkout requires Stripe price IDs (production mode)")
    else:
        fail(f"Checkout returned {resp.status_code}", resp.text[:200])

    # Invalid tier
    resp = client.post(
        "/api/v1/billing/checkout",
        headers=headers,
        json={"tier": "diamond"},
    )
    if resp.status_code == 422:
        ok("Invalid tier rejected (422)")
    else:
        fail(f"Expected 422 for invalid tier, got {resp.status_code}")

    # ------------------------------------------------------------------
    # 5. Test usage/subscription endpoint
    # ------------------------------------------------------------------
    section("5. Test usage endpoint returns tier info")

    resp = client.get("/api/v1/billing/usage", headers=headers)
    if resp.status_code == 200:
        u = resp.json()
        ok(f"tier={u['tier']} used={u['monthly_steps_used']}/{u['monthly_step_limit']}")
        for field in ("monthly_steps_used", "monthly_step_limit", "tier", "billing_period_end"):
            if field in u:
                ok(f"Has field '{field}'")
            else:
                fail(f"Missing field '{field}'")
    else:
        fail(f"Usage returned {resp.status_code}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    sql(f"DELETE FROM api_keys WHERE account_id = '{account_id}'")
    sql(f"DELETE FROM audit_log WHERE account_id = '{account_id}'")
    sql(f"DELETE FROM accounts WHERE id = '{account_id}'")
    print(f"\n  Cleaned up test account {account_id}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    section("RESULTS")
    total = PASS + FAIL
    print(f"  {PASS} passed, {FAIL} failed  ({total} total)")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
