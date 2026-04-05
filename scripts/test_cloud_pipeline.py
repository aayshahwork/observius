"""
End-to-end cloud pipeline test.

Tests the full flow: API health → task submission → polling → result → steps.
Also tests SDK cloud mode (and reports the auth header mismatch if present).

Usage:
    python scripts/test_cloud_pipeline.py <api-base-url> [api-key]

Examples:
    python scripts/test_cloud_pipeline.py https://pokant-api-production.up.railway.app
    python scripts/test_cloud_pipeline.py https://pokant-api-production.up.railway.app cu_test_testkey1234567890abcdef12

If no API key is provided, uses the seed key from migration 002.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SEED_API_KEY = "cu_test_testkey1234567890abcdef12"
POLL_INTERVAL = 5       # seconds between polls
POLL_TIMEOUT = 180      # max seconds to wait for task completion
TERMINAL_STATUSES = {"completed", "failed", "timeout", "cancelled"}


def get_args() -> tuple[str, str]:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_cloud_pipeline.py <api-base-url> [api-key]")
        sys.exit(1)
    base = sys.argv[1].rstrip("/")
    key = sys.argv[2] if len(sys.argv) > 2 else SEED_API_KEY
    return base, key


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no external deps needed)
# ---------------------------------------------------------------------------

def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    timeout: int = 30,
) -> tuple[int, dict | str]:
    """Make an HTTP request. Returns (status_code, parsed_json_or_text)."""
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

class TestRunner:
    def __init__(self, base_url: str, api_key: str):
        self.base = base_url
        self.api_key = api_key
        self.headers = {"X-API-Key": api_key}
        self.passed = 0
        self.failed = 0
        self.task_id: str | None = None

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  [PASS] {label}")
        else:
            self.failed += 1
            msg = f"  [FAIL] {label}"
            if detail:
                msg += f" — {detail}"
            print(msg)
        return ok

    def run_all(self) -> None:
        self.test_health()
        if not self.test_auth():
            print("\n  Auth failed — cannot continue. Check API key and Supabase seed data.")
            self.summary()
            return
        self.test_submit_task()
        if self.task_id:
            self.test_poll_task()
            self.test_get_steps()
        self.test_list_tasks()
        self.test_sdk_cloud()
        self.summary()

    # ------------------------------------------------------------------
    # 1. Health check
    # ------------------------------------------------------------------
    def test_health(self) -> None:
        print("\n== 1. Health Check ==")
        t0 = time.time()
        status, body = http_request(f"{self.base}/health")
        elapsed = int((time.time() - t0) * 1000)
        self.check(status == 200, f"GET /health → {status} ({elapsed}ms)")
        if isinstance(body, dict):
            db = body.get("database", body.get("db", "?"))
            redis = body.get("redis", "?")
            self.check(True, f"database={db}, redis={redis}")

    # ------------------------------------------------------------------
    # 2. Auth guard
    # ------------------------------------------------------------------
    def test_auth(self) -> bool:
        print("\n== 2. Authentication ==")
        # Without key
        status, _ = http_request(f"{self.base}/api/v1/tasks")
        self.check(status in (401, 403, 422), f"no key → {status} (expected 401/403)")

        # With key
        status, body = http_request(f"{self.base}/api/v1/tasks", headers=self.headers)
        ok = self.check(status == 200, f"with X-API-Key → {status}")
        if not ok and isinstance(body, dict):
            print(f"         detail: {body}")
        return ok

    # ------------------------------------------------------------------
    # 3. Submit task
    # ------------------------------------------------------------------
    def test_submit_task(self) -> None:
        print("\n== 3. Submit Task ==")
        payload = {
            "url": "https://news.ycombinator.com",
            "task": "Return the titles of the top 3 posts on Hacker News",
            "output_schema": {"titles": "list[str]"},
            "timeout_seconds": 120,
            "max_retries": 1,
        }

        t0 = time.time()
        status, body = http_request(
            f"{self.base}/api/v1/tasks",
            method="POST",
            headers=self.headers,
            body=payload,
        )
        elapsed = int((time.time() - t0) * 1000)

        self.check(status == 201, f"POST /api/v1/tasks → {status} ({elapsed}ms)")

        if isinstance(body, dict) and "task_id" in body:
            self.task_id = str(body["task_id"])
            task_status = body.get("status", "?")
            self.check(True, f"task_id={self.task_id}, status={task_status}")
        else:
            self.check(False, "task_id in response", f"body={json.dumps(body)[:200]}")

    # ------------------------------------------------------------------
    # 4. Poll until terminal
    # ------------------------------------------------------------------
    def test_poll_task(self) -> None:
        print(f"\n== 4. Poll Task (every {POLL_INTERVAL}s, max {POLL_TIMEOUT}s) ==")
        if not self.task_id:
            self.check(False, "no task to poll")
            return

        deadline = time.time() + POLL_TIMEOUT
        last_status = "?"
        poll_count = 0

        while time.time() < deadline:
            poll_count += 1
            status, body = http_request(
                f"{self.base}/api/v1/tasks/{self.task_id}",
                headers=self.headers,
            )

            if status != 200:
                self.check(False, f"poll #{poll_count} → HTTP {status}")
                break

            if not isinstance(body, dict):
                self.check(False, f"poll #{poll_count} — unexpected body type")
                break

            last_status = body.get("status", "?")
            steps = body.get("steps", 0)
            duration = body.get("duration_ms", 0)
            cost = body.get("cost_cents", 0)

            if last_status in TERMINAL_STATUSES:
                self.check(True, f"poll #{poll_count} → {last_status} ({steps} steps, {duration}ms, ${cost/100:.4f})")

                # Check result
                if last_status == "completed":
                    result = body.get("result")
                    success = body.get("success", False)
                    self.check(success, f"success={success}")
                    self.check(result is not None, f"result={json.dumps(result)[:200]}")

                    if isinstance(result, dict) and "titles" in result:
                        titles = result["titles"]
                        self.check(isinstance(titles, list) and len(titles) > 0,
                                   f"got {len(titles)} titles: {titles[:3]}")
                    else:
                        self.check(False, "output_schema extraction", f"result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")

                elif last_status == "failed":
                    error = body.get("error", "no error message")
                    error_cat = body.get("error_category", "none")
                    self.check(False, f"task failed: [{error_cat}] {error[:200]}")

                elif last_status == "timeout":
                    self.check(False, "task timed out")

                break

            # Still running
            if poll_count % 6 == 0:  # Log every 30s
                print(f"         ... still {last_status} (step {steps}, {int(time.time() - (deadline - POLL_TIMEOUT))}s elapsed)")

            time.sleep(POLL_INTERVAL)
        else:
            self.check(False, f"task did not finish in {POLL_TIMEOUT}s (last status: {last_status})")

    # ------------------------------------------------------------------
    # 5. Steps + screenshots
    # ------------------------------------------------------------------
    def test_get_steps(self) -> None:
        print("\n== 5. Task Steps & Screenshots ==")
        if not self.task_id:
            self.check(False, "no task to check steps")
            return

        status, body = http_request(
            f"{self.base}/api/v1/tasks/{self.task_id}/steps",
            headers=self.headers,
        )
        self.check(status == 200, f"GET /tasks/{self.task_id[:8]}../steps → {status}")

        if status != 200 or not isinstance(body, list):
            return

        self.check(len(body) > 0, f"steps returned: {len(body)}")

        # Check for screenshots
        steps_with_screenshots = [s for s in body if s.get("screenshot_url")]
        self.check(
            len(steps_with_screenshots) > 0,
            f"steps with screenshots: {len(steps_with_screenshots)}/{len(body)}",
        )

        # Print first few steps
        for s in body[:5]:
            action = s.get("action_type", "?")
            desc = (s.get("description") or "")[:80]
            has_ss = "screenshot" if s.get("screenshot_url") else "no-screenshot"
            print(f"         step {s.get('step_number', '?')}: [{action}] {desc} ({has_ss})")

    # ------------------------------------------------------------------
    # 6. List tasks
    # ------------------------------------------------------------------
    def test_list_tasks(self) -> None:
        print("\n== 6. List Tasks ==")
        status, body = http_request(
            f"{self.base}/api/v1/tasks?limit=5",
            headers=self.headers,
        )
        self.check(status == 200, f"GET /api/v1/tasks?limit=5 → {status}")

        if isinstance(body, dict):
            tasks = body.get("tasks", [])
            total = body.get("total", 0)
            self.check(total > 0, f"total tasks: {total}, returned: {len(tasks)}")

    # ------------------------------------------------------------------
    # 7. SDK cloud mode
    # ------------------------------------------------------------------
    def test_sdk_cloud(self) -> None:
        print("\n== 7. SDK Cloud Mode ==")

        try:
            sys.path.insert(0, "sdk")
            from computeruse.client import ComputerUse, _CLOUD_API_BASE
        except ImportError as e:
            print(f"  [SKIP] SDK not importable: {e}")
            return

        # Check if SDK points to correct URL
        print(f"  [INFO] SDK _CLOUD_API_BASE = {_CLOUD_API_BASE}")
        print(f"  [INFO] Our API base        = {self.base}")

        # The SDK uses Authorization: Bearer, but our API expects X-API-Key.
        # Test this directly to document the current state.
        print("\n  -- Header compatibility check --")
        bearer_status, bearer_body = http_request(
            f"{self.base}/api/v1/tasks?limit=1",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        apikey_status, _ = http_request(
            f"{self.base}/api/v1/tasks?limit=1",
            headers={"X-API-Key": self.api_key},
        )

        if bearer_status == 200:
            self.check(True, "SDK auth (Authorization: Bearer) works")
        elif apikey_status == 200 and bearer_status in (401, 403, 422):
            self.check(False,
                       "SDK auth header mismatch",
                       f"API expects X-API-Key (→{apikey_status}) but SDK sends Authorization: Bearer (→{bearer_status}). "
                       "Fix: add Bearer support to api/middleware/auth.py")
            print("\n  -- SDK cloud test skipped due to auth mismatch --")
            print("  To fix, update api/middleware/auth.py to also accept Authorization: Bearer header,")
            print("  OR update sdk/computeruse/client.py to send X-API-Key header.")
            return
        else:
            self.check(False, f"auth check failed — Bearer→{bearer_status}, X-API-Key→{apikey_status}")
            return

        # If Bearer works, try the full SDK flow
        try:
            cu = ComputerUse(
                api_key=self.api_key,
                local=False,
            )

            # Monkey-patch the cloud base URL to point at our Railway deployment
            import computeruse.client as client_mod
            original_base = client_mod._CLOUD_API_BASE
            client_mod._CLOUD_API_BASE = f"{self.base}/api/v1"

            print(f"\n  -- Running SDK cloud task (patched to {self.base}/api/v1) --")
            t0 = time.time()

            result = cu.run_task(
                url="https://news.ycombinator.com",
                task="Get the titles of the top 3 posts",
                output_schema={"titles": "list[str]"},
                max_steps=30,
                timeout_seconds=120,
            )
            elapsed = int(time.time() - t0)

            self.check(result.status == "completed", f"SDK result status={result.status} ({elapsed}s)")
            self.check(result.result is not None, f"SDK result data: {json.dumps(result.result)[:200]}")
            self.check(result.steps > 0, f"SDK steps: {result.steps}")

            # Restore
            client_mod._CLOUD_API_BASE = original_base

        except Exception as e:
            self.check(False, f"SDK cloud execution", str(e)[:300])

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def summary(self) -> None:
        total = self.passed + self.failed
        print(f"\n{'=' * 60}")
        print(f"RESULTS: {self.passed}/{total} passed, {self.failed} failed")
        if self.task_id:
            print(f"Task ID: {self.task_id}")
        if self.failed == 0:
            print("Cloud pipeline is fully operational!")
        else:
            print("Some checks failed — see above for details.")
        print(f"{'=' * 60}")
        sys.exit(0 if self.failed == 0 else 1)


if __name__ == "__main__":
    base_url, api_key = get_args()
    masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else api_key
    print(f"API: {base_url}")
    print(f"Key: {masked}")
    runner = TestRunner(base_url, api_key)
    runner.run_all()
