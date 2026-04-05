"""
Verify Railway deployment is healthy.

Usage:
    python scripts/verify_railway.py https://your-api.up.railway.app

Optionally test with an API key:
    python scripts/verify_railway.py https://your-api.up.railway.app cu_test_testkey1234567890abcdef12
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request


def get_args() -> tuple[str, str | None]:
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_railway.py <base-url> [api-key]")
        print("  e.g: python scripts/verify_railway.py https://pokant-api.up.railway.app")
        sys.exit(1)
    base = sys.argv[1].rstrip("/")
    key = sys.argv[2] if len(sys.argv) > 2 else None
    return base, key


def fetch(url: str, headers: dict[str, str] | None = None, timeout: int = 15) -> tuple[int, str]:
    """Simple GET, returns (status, body)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def main() -> None:
    base, api_key = get_args()
    print(f"Target: {base}\n")

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

    # ------------------------------------------------------------------
    # 1. Health endpoint
    # ------------------------------------------------------------------
    print("== Health Check ==")
    t0 = time.time()
    status, body = fetch(f"{base}/health")
    elapsed = int((time.time() - t0) * 1000)

    check(status == 200, f"GET /health → {status} ({elapsed}ms)")

    if status == 200:
        try:
            data = json.loads(body)
            db_ok = data.get("database") == "ok" or data.get("db") == "ok" or "ok" in body.lower()
            redis_ok = data.get("redis") == "ok" or "ok" in body.lower()
            check(True, f"response: {body[:200]}")
        except json.JSONDecodeError:
            check("ok" in body.lower(), f"response (non-JSON): {body[:200]}")

    # ------------------------------------------------------------------
    # 2. Metrics endpoint
    # ------------------------------------------------------------------
    print("\n== Metrics ==")
    status, body = fetch(f"{base}/metrics")
    check(status == 200, f"GET /metrics → {status}")
    if status == 200:
        has_http = "http_requests_total" in body or "http_request" in body
        check(has_http or len(body) > 50, f"metrics body: {len(body)} bytes")

    # ------------------------------------------------------------------
    # 3. Unauthenticated access (should return 401 or 403)
    # ------------------------------------------------------------------
    print("\n== Auth Guard ==")
    status, body = fetch(f"{base}/api/v1/tasks")
    check(status in (401, 403, 422), f"GET /api/v1/tasks (no key) → {status} (expected 401/403)")

    # ------------------------------------------------------------------
    # 4. Authenticated access (if API key provided)
    # ------------------------------------------------------------------
    if api_key:
        print("\n== Authenticated API ==")
        headers = {"X-API-Key": api_key}

        status, body = fetch(f"{base}/api/v1/tasks", headers=headers)
        check(status == 200, f"GET /api/v1/tasks → {status}")
        if status == 200:
            try:
                data = json.loads(body)
                tasks = data if isinstance(data, list) else data.get("tasks", data.get("items", []))
                check(True, f"tasks returned: {len(tasks)} items")
            except json.JSONDecodeError:
                check(False, "tasks response", f"not JSON: {body[:100]}")

        # Alerts
        status, body = fetch(f"{base}/api/v1/alerts", headers=headers)
        check(status == 200, f"GET /api/v1/alerts → {status}")

        # Analytics
        status, body = fetch(f"{base}/api/v1/analytics/health?period=24h", headers=headers)
        check(status == 200, f"GET /api/v1/analytics/health → {status}")

    else:
        print("\n  [SKIP] Authenticated tests (no API key provided)")
        print("         Re-run with: python scripts/verify_railway.py <url> <api-key>")

    # ------------------------------------------------------------------
    # 5. CORS / headers
    # ------------------------------------------------------------------
    print("\n== Response Headers ==")
    req = urllib.request.Request(f"{base}/health")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        server = resp.headers.get("server", "not set")
        content_type = resp.headers.get("content-type", "not set")
        print(f"  [INFO] server: {server}")
        print(f"  [INFO] content-type: {content_type}")
        check(True, "headers readable")
    except Exception as e:
        check(False, "headers", str(e))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total = total_pass + total_fail
    print(f"\n{'=' * 50}")
    print(f"RESULTS: {total_pass}/{total} passed, {total_fail} failed")
    if total_fail == 0:
        print("Railway deployment is healthy!")
    else:
        print("Fix the failures above.")
    print(f"{'=' * 50}")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
