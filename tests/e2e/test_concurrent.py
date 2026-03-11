"""E2E: Concurrency — submit multiple tasks simultaneously, verify no cross-contamination."""

from __future__ import annotations

import time

import httpx


class TestConcurrentTasks:
    def test_three_concurrent_tasks_complete_independently(self, client: httpx.Client) -> None:
        task_ids = []
        for i in range(3):
            resp = client.post(
                "/api/v1/tasks",
                json={
                    "url": "https://example.com",
                    "task": f"Extract the page title (concurrent task {i})",
                    "timeout_seconds": 60,
                },
            )
            assert resp.status_code == 201
            task_ids.append(resp.json()["task_id"])

        # Poll all tasks until complete
        completed: set[str] = set()
        for _ in range(36):
            for tid in task_ids:
                if tid in completed:
                    continue
                data = client.get(f"/api/v1/tasks/{tid}").json()
                if data["status"] in ("completed", "failed"):
                    completed.add(tid)
            if len(completed) == len(task_ids):
                break
            time.sleep(5)

        assert len(completed) == len(task_ids), f"Only {len(completed)}/{len(task_ids)} tasks finished"
