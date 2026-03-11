"""
tests/load/locustfile.py — Load test for the ComputerUse API.

Simulates realistic user flows:
- Create task (POST /api/v1/tasks)
- Poll for completion (GET /api/v1/tasks/{id})
- List tasks (GET /api/v1/tasks)

Targets:
- 50 concurrent users, 5 tasks/sec sustained for 5 min.
- P99 < 500ms for API (non-execution), error rate < 5%.

Usage:
    locust -f tests/load/locustfile.py --config tests/load/locust.conf
    # Or with CSV output:
    locust -f tests/load/locustfile.py --config tests/load/locust.conf --csv results --html report.html

Environment variables:
    LOAD_TEST_API_KEY: API key for authentication (required).
    LOAD_TEST_HOST: Override host (default: http://localhost:8000).
"""

from __future__ import annotations

import os
import random
from uuid import uuid4

from locust import HttpUser, between, events, task


class TaskUser(HttpUser):
    """Simulates a user creating and polling browser automation tasks."""

    wait_time = between(1, 3)

    def on_start(self) -> None:
        """Set up authentication headers."""
        api_key = os.environ.get("LOAD_TEST_API_KEY", "")
        self.client.headers.update({
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        })
        self._task_ids: list[str] = []

    @task(3)
    def create_task(self) -> None:
        """POST /api/v1/tasks — create a new automation task."""
        resp = self.client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": f"Load test task {uuid4().hex[:8]}",
                "timeout_seconds": 60,
            },
        )
        if resp.status_code == 201:
            data = resp.json()
            task_id = data.get("task_id")
            if task_id:
                self._task_ids.append(task_id)
                # Cap tracked IDs to prevent memory growth
                if len(self._task_ids) > 100:
                    self._task_ids = self._task_ids[-50:]

    @task(7)
    def poll_task(self) -> None:
        """GET /api/v1/tasks/{task_id} — poll task status."""
        if not self._task_ids:
            return
        task_id = random.choice(self._task_ids)
        self.client.get(
            f"/api/v1/tasks/{task_id}",
            name="/api/v1/tasks/[id]",
        )

    @task(1)
    def list_tasks(self) -> None:
        """GET /api/v1/tasks — list tasks with pagination."""
        self.client.get("/api/v1/tasks?limit=10")


# ---------------------------------------------------------------------------
# Post-run assertions (checked when running with --headless)
# ---------------------------------------------------------------------------


@events.quitting.add_listener
def check_results(environment, **kwargs):
    """Validate load test results against targets."""
    stats = environment.runner.stats
    total = stats.total

    errors: list[str] = []

    # P99 < 500ms for all endpoints
    for entry in stats.entries.values():
        p99 = entry.get_response_time_percentile(0.99) or 0
        if p99 > 500:
            errors.append(f"{entry.name}: P99={p99:.0f}ms > 500ms")

    # Error rate < 5%
    if total.num_requests > 0:
        error_rate = total.num_failures / total.num_requests
        if error_rate > 0.05:
            errors.append(f"Error rate {error_rate:.1%} > 5%")

    if errors:
        for err in errors:
            environment.runner.log.warning("LOAD TEST FAILED: %s", err)
        environment.process_exit_code = 1
    else:
        environment.runner.log.info(
            "LOAD TEST PASSED: %d requests, %.1f%% error rate, P99=%dms",
            total.num_requests,
            (total.num_failures / max(total.num_requests, 1)) * 100,
            total.get_response_time_percentile(0.99) or 0,
        )
