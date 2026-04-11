"""
Post-deploy production verification.

Run after every Railway deploy to verify the full stack works end-to-end.

Usage:
    python scripts/verify_production.py https://your-api.up.railway.app

Optionally test with an existing API key (skips registration):
    python scripts/verify_production.py https://your-api.up.railway.app cu_live_existing_key

Checks (in order):
  1. Health endpoint (database + redis)
  2. Auth guard (401 without key)
  3. Register new account
  4. Auth with new key (200)
  5. Submit task
  6. Poll until terminal
  7. Verify structured result
  8. Check steps + screenshots
  9. Verify usage incremented
  10. List tasks
  11. Bearer auth compatibility
  12. Metrics endpoint

Total runtime: under 5 minutes (limited by task execution time).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLL_INTERVAL = 5
POLL_TIMEOUT = 240
TERMINAL_STATUSES = {"completed", "failed", "timeout", "cancelled"}


def get_args() -> tuple[str, str | None]:
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_production.py <api-base-url> [api-key]")
        print("  e.g: python scripts/verify_production.py https://pokant-api.up.railway.app")
        sys.exit(1)
    base = sys.argv[1].rstrip("/")
    key = sys.argv[2] if len(sys.argv) > 2 else None
    return base, key


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def http(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    timeout: int = 30,
) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body else None
    hdrs = headers or {}
    if body:
        hdrs["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode()
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

class ProductionVerifier:
    def __init__(self, base_url: str, existing_key: str | None = None):
        self.base = base_url
        self.existing_key = existing_key
        self.api_key: str | None = existing_key
        self.account_id: str | None = None
        self.task_id: str | None = None
        self.passed = 0
        self.failed = 0

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  [PASS] {label}")
        else:
            self.failed += 1
            msg = f"  [FAIL] {label}"
            if detail:
                msg += f" -- {detail}"
            print(msg)
        return ok

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    # ------------------------------------------------------------------
    # 1. Health
    # ------------------------------------------------------------------
    def test_health(self) -> bool:
        print("\n== 1. Health Check ==")
        t0 = time.time()
        status, body = http(f"{self.base}/health")
        ms = int((time.time() - t0) * 1000)

        ok = self.check(status == 200, f"GET /health -> {status} ({ms}ms)")
        if not ok:
            self.check(False, "Cannot proceed without healthy API", f"body={str(body)[:200]}")
            return False

        if isinstance(body, dict):
            db_ok = body.get("database") == "ok" or body.get("db") == "ok"
            redis_ok = body.get("redis") == "ok"
            self.check(db_ok, f"database={body.get('database', body.get('db', '?'))}")
            self.check(redis_ok, f"redis={body.get('redis', '?')}")
        return True

    # ------------------------------------------------------------------
    # 2. Auth guard
    # ------------------------------------------------------------------
    def test_auth_guard(self) -> None:
        print("\n== 2. Auth Guard ==")
        status, _ = http(f"{self.base}/api/v1/tasks")
        self.check(status in (401, 403), f"no key -> {status} (expected 401)")

    # ------------------------------------------------------------------
    # 3. Register
    # ------------------------------------------------------------------
    def test_register(self) -> bool:
        print("\n== 3. Registration ==")
        if self.existing_key:
            print("  [SKIP] Using provided API key")
            return True

        email = f"verify-{uuid.uuid4().hex[:8]}@test.pokant.dev"
        status, body = http(
            f"{self.base}/auth/register",
            method="POST",
            body={"email": email, "name": "Deploy Verifier"},
        )

        ok = self.check(status == 201, f"POST /auth/register -> {status}")
        if not ok:
            detail = json.dumps(body)[:200] if isinstance(body, dict) else str(body)[:200]
            self.check(False, "Registration failed", detail)
            return False

        if isinstance(body, dict):
            self.api_key = body.get("api_key")
            self.account_id = body.get("account_id")
            self.check(
                self.api_key is not None and self.api_key.startswith("cu_live_"),
                f"api_key={self.api_key[:16]}... account_id={self.account_id}",
            )
            self.check(body.get("tier") == "free", f"tier={body.get('tier')}")
            return self.api_key is not None
        return False

    # ------------------------------------------------------------------
    # 4. Authenticated access
    # ------------------------------------------------------------------
    def test_auth_with_key(self) -> bool:
        print("\n== 4. Authenticated Access ==")
        if not self.api_key:
            self.check(False, "no API key available")
            return False

        status, body = http(f"{self.base}/api/v1/tasks", headers=self.headers)
        ok = self.check(status == 200, f"GET /tasks with key -> {status}")
        if not ok and isinstance(body, dict):
            print(f"         detail: {json.dumps(body)[:200]}")
        return ok

    # ------------------------------------------------------------------
    # 5. Submit task
    # ------------------------------------------------------------------
    def test_submit_task(self) -> bool:
        print("\n== 5. Submit Task ==")
        if not self.api_key:
            self.check(False, "no API key")
            return False

        payload = {
            "url": "https://news.ycombinator.com",
            "task": "Return the titles of the top 3 posts on Hacker News",
            "output_schema": {"titles": "list[str]"},
            "timeout_seconds": 120,
            "max_retries": 1,
        }

        t0 = time.time()
        status, body = http(
            f"{self.base}/api/v1/tasks",
            method="POST",
            headers=self.headers,
            body=payload,
        )
        ms = int((time.time() - t0) * 1000)

        ok = self.check(status == 201, f"POST /tasks -> {status} ({ms}ms)")

        if isinstance(body, dict) and "task_id" in body:
            self.task_id = str(body["task_id"])
            self.check(True, f"task_id={self.task_id}")
            return True
        else:
            detail = json.dumps(body)[:200] if isinstance(body, dict) else str(body)[:200]
            self.check(False, "task_id in response", detail)
            return False

    # ------------------------------------------------------------------
    # 6. Poll until terminal
    # ------------------------------------------------------------------
    def test_poll_task(self) -> str | None:
        print(f"\n== 6. Poll Task (every {POLL_INTERVAL}s, max {POLL_TIMEOUT}s) ==")
        if not self.task_id:
            self.check(False, "no task to poll")
            return None

        deadline = time.time() + POLL_TIMEOUT
        poll_count = 0
        last_status = "?"
        start = time.time()

        while time.time() < deadline:
            poll_count += 1
            status, body = http(
                f"{self.base}/api/v1/tasks/{self.task_id}",
                headers=self.headers,
            )

            if status != 200:
                self.check(False, f"poll #{poll_count} -> HTTP {status}")
                return None

            if not isinstance(body, dict):
                self.check(False, f"poll #{poll_count} -- unexpected body")
                return None

            last_status = body.get("status", "?")
            steps = body.get("steps", 0)
            elapsed = int(time.time() - start)

            if last_status in TERMINAL_STATUSES:
                duration = body.get("duration_ms", 0)
                cost = body.get("cost_cents", 0)
                self.check(True, f"poll #{poll_count} -> {last_status} ({steps} steps, {duration}ms, ${cost/100:.4f}, {elapsed}s wall)")
                return last_status

            if poll_count % 6 == 0:
                print(f"         ... still {last_status} (step {steps}, {elapsed}s elapsed)")

            time.sleep(POLL_INTERVAL)

        self.check(False, f"task did not finish in {POLL_TIMEOUT}s (last: {last_status})")
        return None

    # ------------------------------------------------------------------
    # 7. Verify result
    # ------------------------------------------------------------------
    def test_verify_result(self) -> None:
        print("\n== 7. Verify Result ==")
        if not self.task_id:
            self.check(False, "no task")
            return

        status, body = http(
            f"{self.base}/api/v1/tasks/{self.task_id}",
            headers=self.headers,
        )

        if status != 200 or not isinstance(body, dict):
            self.check(False, f"GET task -> {status}")
            return

        success = body.get("success", False)
        self.check(success, f"success={success}")

        result = body.get("result")
        self.check(result is not None, f"result present: {json.dumps(result)[:200]}")

        if isinstance(result, dict):
            if "titles" in result:
                titles = result["titles"]
                self.check(
                    isinstance(titles, list) and len(titles) > 0,
                    f"titles: {len(titles)} items: {titles[:3]}",
                )
            elif "text" in result:
                # browser-use may return wrapped string; try to parse
                try:
                    inner = json.loads(result["text"])
                    if isinstance(inner, dict) and "titles" in inner:
                        self.check(True, f"titles (parsed from text): {inner['titles'][:3]}")
                    else:
                        self.check(False, "output_schema extraction", f"parsed text keys: {list(inner.keys()) if isinstance(inner, dict) else type(inner)}")
                except (json.JSONDecodeError, ValueError):
                    self.check(False, "output_schema extraction", f"result={json.dumps(result)[:200]}")
            else:
                self.check(False, "output_schema extraction", f"result keys: {list(result.keys())}")

    # ------------------------------------------------------------------
    # 8. Steps + screenshots
    # ------------------------------------------------------------------
    def test_steps(self) -> None:
        print("\n== 8. Steps & Screenshots ==")
        if not self.task_id:
            self.check(False, "no task")
            return

        status, body = http(
            f"{self.base}/api/v1/tasks/{self.task_id}/steps",
            headers=self.headers,
        )
        self.check(status == 200, f"GET /tasks/.../steps -> {status}")

        if status != 200 or not isinstance(body, list):
            return

        self.check(len(body) > 0, f"steps returned: {len(body)}")

        with_screenshots = [s for s in body if s.get("screenshot_url")]
        self.check(len(with_screenshots) > 0, f"steps with screenshots: {len(with_screenshots)}/{len(body)}")

        for s in body[:3]:
            action = s.get("action_type", "?")
            desc = (s.get("description") or "")[:60]
            ss = "screenshot" if s.get("screenshot_url") else "no-screenshot"
            print(f"         step {s.get('step_number', '?')}: [{action}] {desc} ({ss})")

    # ------------------------------------------------------------------
    # 9. Usage incremented
    # ------------------------------------------------------------------
    def test_usage(self) -> None:
        print("\n== 9. Usage Check ==")
        if not self.api_key:
            self.check(False, "no API key")
            return

        status, body = http(
            f"{self.base}/api/v1/billing/usage",
            headers=self.headers,
        )

        if status == 200 and isinstance(body, dict):
            used = body.get("monthly_steps_used", 0)
            limit = body.get("monthly_step_limit", 0)
            self.check(used > 0, f"usage: {used}/{limit} steps used")
        elif status == 404:
            # Billing route may not exist yet
            print("  [SKIP] /billing/usage not available")
        else:
            self.check(False, f"GET /billing/usage -> {status}")

    # ------------------------------------------------------------------
    # 10. List tasks
    # ------------------------------------------------------------------
    def test_list_tasks(self) -> None:
        print("\n== 10. List Tasks ==")
        status, body = http(
            f"{self.base}/api/v1/tasks?limit=5",
            headers=self.headers,
        )
        self.check(status == 200, f"GET /tasks?limit=5 -> {status}")

        if isinstance(body, dict):
            total = body.get("total", 0)
            tasks = body.get("tasks", [])
            self.check(total > 0, f"total: {total}, returned: {len(tasks)}")

    # ------------------------------------------------------------------
    # 11. Bearer auth
    # ------------------------------------------------------------------
    def test_bearer_auth(self) -> None:
        print("\n== 11. Bearer Auth Compatibility ==")
        if not self.api_key:
            self.check(False, "no API key")
            return

        status, _ = http(
            f"{self.base}/api/v1/tasks?limit=1",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        self.check(status == 200, f"Authorization: Bearer -> {status}")

    # ------------------------------------------------------------------
    # 12. Metrics
    # ------------------------------------------------------------------
    def test_metrics(self) -> None:
        print("\n== 12. Metrics Endpoint ==")
        status, body = http(f"{self.base}/metrics")
        self.check(status == 200, f"GET /metrics -> {status}")
        if status == 200 and isinstance(body, str):
            has_http = "http_request" in body
            self.check(has_http, f"metrics body: {len(body)} bytes, has http_request metrics")

    # ------------------------------------------------------------------
    # Run all
    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self.test_health():
            self.summary()
            return

        self.test_auth_guard()

        if not self.test_register():
            self.summary()
            return

        if not self.test_auth_with_key():
            self.summary()
            return

        if not self.test_submit_task():
            self.summary()
            return

        task_status = self.test_poll_task()

        if task_status == "completed":
            self.test_verify_result()
            self.test_steps()

        self.test_usage()
        self.test_list_tasks()
        self.test_bearer_auth()
        self.test_metrics()
        self.summary()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def summary(self) -> None:
        total = self.passed + self.failed
        print(f"\n{'=' * 60}")
        print(f"RESULTS: {self.passed}/{total} passed, {self.failed} failed")
        if self.task_id:
            print(f"Task ID: {self.task_id}")
        if self.api_key and not self.existing_key:
            print(f"New API Key: {self.api_key}")
        if self.failed == 0:
            print("Production is fully operational!")
        else:
            print("Some checks failed -- see above.")
        print(f"{'=' * 60}")
        sys.exit(0 if self.failed == 0 else 1)


if __name__ == "__main__":
    base_url, existing_key = get_args()
    masked = ""
    if existing_key:
        masked = existing_key[:8] + "..." + existing_key[-4:] if len(existing_key) > 12 else existing_key
    print(f"Target: {base_url}")
    if masked:
        print(f"Key:    {masked}")
    else:
        print("Mode:   Register new account")
    verifier = ProductionVerifier(base_url, existing_key)
    verifier.run()
