"""E2E: Data extraction — task with structured output_schema."""

from __future__ import annotations

import time

import httpx


class TestDataExtraction:
    def test_extract_structured_data(self, client: httpx.Client) -> None:
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://books.toscrape.com/",
                "task": "Extract the title and price of the first 3 books on the page",
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "books": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "price": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "timeout_seconds": 120,
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

        assert data["status"] == "completed"
        result = data.get("result") or {}
        assert "books" in result
        assert len(result["books"]) >= 1
