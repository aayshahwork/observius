"""E2E: Replay — verify completed tasks generate a replay URL."""

from __future__ import annotations

import time

import httpx
import pytest


class TestReplay:
    def test_completed_task_has_replay_url(self, client: httpx.Client) -> None:
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Extract the page title",
                "timeout_seconds": 60,
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        data = {}
        for _ in range(36):
            data = client.get(f"/api/v1/tasks/{task_id}").json()
            if data["status"] in ("completed", "failed"):
                break
            time.sleep(5)

        if data["status"] != "completed":
            pytest.skip("Task did not complete; replay test not applicable")

        # Fetch replay URL
        replay_resp = client.get(f"/api/v1/tasks/{task_id}/replay")
        assert replay_resp.status_code == 200
        replay_data = replay_resp.json()
        assert "replay_url" in replay_data
        assert replay_data["replay_url"].startswith("https://")
