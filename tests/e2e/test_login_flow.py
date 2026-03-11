"""E2E: Login flow — submit a login task and verify completion."""

from __future__ import annotations

import time

import httpx


class TestLoginFlow:
    def test_login_task_completes_successfully(self, client: httpx.Client) -> None:
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://the-internet.herokuapp.com/login",
                "task": "Login with username 'tomsmith' and password 'SuperSecretPassword!'",
                "timeout_seconds": 120,
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Poll for completion (max 3 min)
        data = {}
        for _ in range(36):
            status_resp = client.get(f"/api/v1/tasks/{task_id}")
            assert status_resp.status_code == 200
            data = status_resp.json()
            if data["status"] in ("completed", "failed"):
                break
            time.sleep(5)

        assert data["status"] == "completed"
        assert data["success"] is True
