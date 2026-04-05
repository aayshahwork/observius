#!/usr/bin/env python3
"""
test_billing_flow.py — End-to-end billing flow test.

Tests billing endpoints, webhook handling, and quota enforcement.

Usage:
    python scripts/test_billing_flow.py
    BASE_URL=https://api.pokant.dev API_KEY=cu_test_... python scripts/test_billing_flow.py

Requires: httpx  (pip install httpx)
"""

from __future__ import annotations

import os
import sys

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY", "cu_test_testkey1234567890abcdef12")

PASS = 0
FAIL = 0
SKIP = 0


def header() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def section(name: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def ok(label: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    extra = f"  ({detail})" if detail else ""
    print(f"  PASS  {label}{extra}")


def fail(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    extra = f"  ({detail})" if detail else ""
    print(f"  FAIL  {label}{extra}")


def skip(label: str, reason: str = "") -> None:
    global SKIP
    SKIP += 1
    extra = f"  — {reason}" if reason else ""
    print(f"  SKIP  {label}{extra}")


def check_status(resp: httpx.Response, expected: int | list[int], label: str) -> bool:
    expected_list = expected if isinstance(expected, list) else [expected]
    if resp.status_code in expected_list:
        ok(label, f"{resp.status_code}")
        return True
    fail(label, f"expected {expected}, got {resp.status_code}: {resp.text[:200]}")
    return False


def main() -> None:
    client = httpx.Client(base_url=BASE_URL, timeout=30)

    # ------------------------------------------------------------------
    # 0. Health check
    # ------------------------------------------------------------------
    section("0. Health check")
    try:
        resp = client.get("/health")
        check_status(resp, 200, "GET /health")
    except httpx.ConnectError:
        fail("GET /health", "Cannot connect — is the API running?")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 1. Check current usage
    # ------------------------------------------------------------------
    section("1. GET /api/v1/billing/usage")
    resp = client.get("/api/v1/billing/usage", headers=header())
    if not check_status(resp, 200, "GET /billing/usage"):
        print("  Cannot continue without billing/usage. Exiting.")
        sys.exit(1)

    usage = resp.json()
    print(f"        tier={usage['tier']}  "
          f"used={usage['monthly_steps_used']}/{usage['monthly_step_limit']}")

    # Validate response schema
    for field in ("monthly_steps_used", "monthly_step_limit", "tier"):
        if field in usage:
            ok(f"Response has '{field}' field")
        else:
            fail(f"Response missing '{field}' field")

    # ------------------------------------------------------------------
    # 2. Checkout endpoint
    # ------------------------------------------------------------------
    section("2. POST /api/v1/billing/checkout")

    # In production mode (STRIPE_SECRET_KEY set), checkout requires valid
    # price IDs. In dev mode (no key), it applies tier directly.
    resp = client.post(
        "/api/v1/billing/checkout",
        headers=header(),
        json={"tier": "startup"},
    )

    if resp.status_code == 200:
        # Dev mode — tier applied directly
        ok("Checkout dev mode", f"url={resp.json().get('checkout_url')}")
        stripe_configured = False
    elif resp.status_code == 400 and "No price configured" in resp.text:
        # Production mode — Stripe key set but no price IDs
        ok("Checkout production mode rejects missing price ID", "400")
        stripe_configured = True
    else:
        fail("Checkout unexpected response", f"{resp.status_code}: {resp.text[:200]}")
        stripe_configured = resp.status_code != 200

    # Same-tier checkout — should 400 (only testable in dev mode after upgrade)
    resp = client.post(
        "/api/v1/billing/checkout",
        headers=header(),
        json={"tier": usage["tier"]},
    )
    if usage["tier"] in ("startup", "growth", "enterprise"):
        check_status(resp, 400, "Same-tier checkout rejected")
    else:
        # "free" is not a valid checkout target, so 422
        check_status(resp, 422, "Free tier rejected by schema (not a valid target)")

    # Invalid tier — should 422
    resp = client.post(
        "/api/v1/billing/checkout",
        headers=header(),
        json={"tier": "platinum"},
    )
    check_status(resp, 422, "Invalid tier rejected by schema")

    # ------------------------------------------------------------------
    # 3. Portal endpoint
    # ------------------------------------------------------------------
    section("3. POST /api/v1/billing/portal")
    resp = client.post("/api/v1/billing/portal", headers=header())

    if resp.status_code == 200:
        ok("Portal dev mode", f"url={resp.json().get('portal_url')}")
    elif resp.status_code == 400 and "No Stripe customer" in resp.text:
        ok("Portal rejects account without stripe_customer_id", "400")
    else:
        fail("Portal unexpected response", f"{resp.status_code}: {resp.text[:200]}")

    # ------------------------------------------------------------------
    # 4. Webhook handler
    # ------------------------------------------------------------------
    section("4. POST /api/v1/billing/webhook")

    # checkout.session.completed
    resp = client.post(
        "/api/v1/billing/webhook",
        headers={"stripe-signature": "test_sig"},
        json={
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_test_webhook",
                    "metadata": {"tier": "growth"},
                },
            },
        },
    )
    check_status(resp, 200, "webhook: checkout.session.completed")

    # invoice.paid
    resp = client.post(
        "/api/v1/billing/webhook",
        headers={"stripe-signature": "test_sig"},
        json={
            "type": "invoice.paid",
            "data": {"object": {"customer": "cus_test_webhook"}},
        },
    )
    check_status(resp, 200, "webhook: invoice.paid")

    # customer.subscription.deleted
    resp = client.post(
        "/api/v1/billing/webhook",
        headers={"stripe-signature": "test_sig"},
        json={
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_test_webhook"}},
        },
    )
    check_status(resp, 200, "webhook: customer.subscription.deleted")

    # Unknown event — ignored
    resp = client.post(
        "/api/v1/billing/webhook",
        headers={"stripe-signature": "test_sig"},
        json={"type": "payment_intent.created", "data": {"object": {}}},
    )
    check_status(resp, 200, "webhook: unknown event ignored")

    # Invalid JSON — rejected
    resp = client.post(
        "/api/v1/billing/webhook",
        headers={"stripe-signature": "test_sig", "content-type": "application/json"},
        content=b"not json",
    )
    check_status(resp, 400, "webhook: invalid JSON rejected")

    # Missing data fields — still 200 (gracefully handled)
    resp = client.post(
        "/api/v1/billing/webhook",
        headers={"stripe-signature": "test_sig"},
        json={"type": "checkout.session.completed", "data": {"object": {}}},
    )
    check_status(resp, 200, "webhook: missing metadata handled gracefully")

    # ------------------------------------------------------------------
    # 5. Quota enforcement
    # ------------------------------------------------------------------
    section("5. Quota enforcement (402 when at limit)")

    resp = client.get("/api/v1/billing/usage", headers=header())
    usage = resp.json()
    used = usage["monthly_steps_used"]
    limit = usage["monthly_step_limit"]
    print(f"  INFO  used={used}/{limit}")

    if used >= limit:
        resp = client.post(
            "/api/v1/tasks",
            headers=header(),
            json={"url": "https://example.com", "task": "Should be blocked"},
        )
        if check_status(resp, 402, "POST /tasks blocked at quota"):
            body = resp.json()
            if body.get("detail", {}).get("error_code") == "QUOTA_EXCEEDED":
                ok("QUOTA_EXCEEDED error code present")
            else:
                fail("Missing QUOTA_EXCEEDED error code")
    else:
        skip(
            "402 quota test",
            f"used ({used}) < limit ({limit}). "
            "Run: UPDATE accounts SET monthly_steps_used = monthly_step_limit;",
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    section("RESULTS")
    total = PASS + FAIL + SKIP
    print(f"  {PASS} passed, {FAIL} failed, {SKIP} skipped  ({total} total)")
    print()

    mode = "PRODUCTION" if stripe_configured else "DEV"
    print(f"  Stripe mode: {mode}")
    if stripe_configured:
        print("    Checkout and portal require real Stripe config (expected).")
        print("    Set STRIPE_PRICE_IDS in .env to test full checkout flow.")
    print()
    print("  Gaps to address:")
    print("    1. POST /tasks/ingest must call UsageTracker.try_increment_steps()")
    print("    2. Uncomment Stripe API calls in billing.py (search for 'TODO: wire up stripe')")
    print("    3. Enable webhook signature verification")
    print()

    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
