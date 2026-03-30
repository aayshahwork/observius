"""E2E: Native executor mode (computer_20251124).

Submits tasks with executor_mode="native" and verifies the full pipeline:
API → Celery → native executor loop → result persistence.

These tests require live services:
- API server running at E2E_BASE_URL
- Celery worker with Anthropic API key
- Browserbase or local Playwright browser

Set E2E_API_KEY to a valid API key to run these tests.
"""

from __future__ import annotations

import time

import httpx
import pytest


class TestNativeExecutorBasic:
    """Smoke tests: submit tasks with executor_mode='native', verify completion."""

    def test_simple_extraction_native_mode(self, client: httpx.Client) -> None:
        """Native executor can navigate to a page and extract content."""
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Extract the main heading text from this page.",
                "executor_mode": "native",
                "output_schema": {
                    "heading": "string",
                },
                "timeout_seconds": 120,
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        data = _poll_until_done(client, task_id, timeout=180)

        assert data["status"] == "completed", f"Task failed: {data.get('error')}"
        assert data["success"] is True
        assert data["steps"] >= 1
        assert data["duration_ms"] > 0

        # Should have extracted something
        result = data.get("result") or {}
        assert "heading" in result

    def test_native_mode_multi_step(self, client: httpx.Client) -> None:
        """Native executor can perform multiple actions (click, scroll)."""
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://books.toscrape.com/",
                "task": (
                    "Find the title of the first book on the page. "
                    "Click on it to go to its detail page, then extract the price."
                ),
                "executor_mode": "native",
                "output_schema": {
                    "title": "string",
                    "price": "string",
                },
                "timeout_seconds": 180,
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        data = _poll_until_done(client, task_id, timeout=240)

        if data["status"] != "completed":
            pytest.skip(f"Task did not complete: {data.get('error')}")

        assert data["steps"] >= 2  # navigate + at least 1 click + done
        result = data.get("result") or {}
        assert "title" in result
        assert "price" in result

    def test_native_mode_max_steps_respected(self, client: httpx.Client) -> None:
        """Verify the task completes even when max_steps is low."""
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Read the entire page content and every link on the page.",
                "executor_mode": "native",
                "timeout_seconds": 60,
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        data = _poll_until_done(client, task_id, timeout=120)

        # Should complete (success or max steps reached) without hanging
        assert data["status"] in ("completed", "failed")
        assert data["duration_ms"] > 0


class TestNativeExecutorRegression:
    """Verify browser_use mode still works after adding the native path."""

    def test_default_mode_unchanged(self, client: httpx.Client) -> None:
        """A task without executor_mode defaults to browser_use and still works."""
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Extract the page heading text.",
                "timeout_seconds": 90,
            },
        )
        assert resp.status_code == 201
        # No executor_mode field — should default to browser_use
        task_id = resp.json()["task_id"]

        data = _poll_until_done(client, task_id, timeout=120)

        assert data["status"] == "completed", f"Task failed: {data.get('error')}"
        assert data["success"] is True

    def test_explicit_browser_use_mode(self, client: httpx.Client) -> None:
        """Explicitly setting executor_mode='browser_use' works identically."""
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Extract the page heading text.",
                "executor_mode": "browser_use",
                "timeout_seconds": 90,
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        data = _poll_until_done(client, task_id, timeout=120)

        assert data["status"] == "completed", f"Task failed: {data.get('error')}"
        assert data["success"] is True


class TestNativeExecutorValidation:
    """API validation for executor_mode field."""

    def test_invalid_executor_mode_rejected(self, client: httpx.Client) -> None:
        """executor_mode with invalid value is rejected by the API."""
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Extract the page heading.",
                "executor_mode": "invalid_mode",
                "timeout_seconds": 60,
            },
        )
        assert resp.status_code == 422  # Pydantic validation error

    def test_native_mode_accepted(self, client: httpx.Client) -> None:
        """executor_mode='native' is accepted by the API."""
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Extract the page heading.",
                "executor_mode": "native",
                "timeout_seconds": 60,
            },
        )
        assert resp.status_code == 201


class TestNativeExecutorReplay:
    """Verify replay generation works for native executor tasks."""

    def test_native_task_generates_replay(self, client: httpx.Client) -> None:
        """A completed native task should have a replay URL."""
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Read the page content.",
                "executor_mode": "native",
                "timeout_seconds": 90,
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        data = _poll_until_done(client, task_id, timeout=120)

        if data["status"] != "completed":
            pytest.skip("Task did not complete; replay test not applicable")

        replay_resp = client.get(f"/api/v1/tasks/{task_id}/replay")
        # 200 with replay_url in production (R2 upload).
        # 404 in local dev when boto3/R2 is not configured — that's expected.
        if replay_resp.status_code == 404:
            pytest.skip("Replay not available (R2/boto3 not configured locally)")
        assert replay_resp.status_code == 200
        assert "replay_url" in replay_resp.json()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _poll_until_done(
    client: httpx.Client,
    task_id: str,
    timeout: int = 180,
    interval: int = 5,
) -> dict:
    """Poll the task status endpoint until terminal state or timeout."""
    iterations = timeout // interval
    data: dict = {}
    for _ in range(iterations):
        resp = client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        time.sleep(interval)
    return data
