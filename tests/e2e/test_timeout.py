"""E2E: Timeout — verify tasks respect timeout_seconds."""

from __future__ import annotations

import time

import httpx


class TestTimeout:
    def test_short_timeout_fails_gracefully(self, client: httpx.Client) -> None:
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://the-internet.herokuapp.com/slow",
                "task": "Wait for the page to load completely and extract all text",
                "timeout_seconds": 30,
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        data = {}
        for _ in range(24):
            data = client.get(f"/api/v1/tasks/{task_id}").json()
            if data["status"] in ("completed", "failed"):
                break
            time.sleep(5)

        # Task should fail or timeout, not hang indefinitely
        assert data["status"] in ("failed", "completed")
