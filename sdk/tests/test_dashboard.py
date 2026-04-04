"""Tests for the Pokant local dashboard FastAPI app."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("fastapi not installed", allow_module_level=True)

from computeruse.dashboard import create_app


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a pre-populated .pokant directory."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    ss_dir = tmp_path / "screenshots" / "task-001"
    ss_dir.mkdir(parents=True)
    replays_dir = tmp_path / "replays"
    replays_dir.mkdir()

    now = datetime.now(timezone.utc)

    run1 = {
        "task_id": "task-001",
        "status": "completed",
        "step_count": 3,
        "cost_cents": 15.0,
        "error_category": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "duration_ms": 4500,
        "steps": [
            {
                "action_type": "navigate",
                "description": "Go to page",
                "duration_ms": 1500,
                "success": True,
                "tokens_in": 200,
                "tokens_out": 80,
                "screenshot_path": "task-001/step_0.png",
            },
            {
                "action_type": "click",
                "description": "Click button",
                "duration_ms": 1000,
                "success": True,
                "tokens_in": 150,
                "tokens_out": 60,
                "screenshot_path": None,
            },
            {
                "action_type": "extract",
                "description": "Extract data",
                "duration_ms": 2000,
                "success": True,
                "tokens_in": 300,
                "tokens_out": 100,
                "screenshot_path": None,
            },
        ],
    }
    (runs_dir / "task-001.json").write_text(json.dumps(run1))

    run2 = {
        "task_id": "task-002",
        "status": "failed",
        "step_count": 1,
        "cost_cents": 3.0,
        "error_category": "browser",
        "error": "Timeout waiting for selector",
        "created_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "duration_ms": 30000,
        "steps": [],
    }
    (runs_dir / "task-002.json").write_text(json.dumps(run2))

    # Screenshot file
    (ss_dir / "step_0.png").write_bytes(b"\x89PNG" + b"\x00" * 50)

    # Replay file
    (replays_dir / "task-001.html").write_text("<html>replay for task-001</html>")

    return tmp_path


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    app = create_app(str(data_dir))
    return TestClient(app)


class TestListRuns:
    def test_returns_all_runs(self, client: TestClient) -> None:
        resp = client.get("/api/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = {r["task_id"] for r in data}
        assert ids == {"task-001", "task-002"}

    def test_empty_dir(self, tmp_path: Path) -> None:
        app = create_app(str(tmp_path))
        c = TestClient(app)
        resp = c.get("/api/runs")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetRun:
    def test_existing_run(self, client: TestClient) -> None:
        resp = client.get("/api/runs/task-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-001"
        assert data["status"] == "completed"
        assert len(data["steps"]) == 3

    def test_missing_run(self, client: TestClient) -> None:
        resp = client.get("/api/runs/nonexistent")
        assert resp.status_code == 404


class TestStats:
    def test_aggregated_stats(self, client: TestClient) -> None:
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 2
        assert data["completed"] == 1
        assert data["failed"] == 1
        assert data["success_rate"] == 50.0
        assert data["total_cost_cents"] == 18.0
        assert data["error_categories"] == {"browser": 1}

    def test_empty_stats(self, tmp_path: Path) -> None:
        app = create_app(str(tmp_path))
        c = TestClient(app)
        resp = c.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 0
        assert data["success_rate"] == 0


class TestScreenshots:
    def test_serve_screenshot(self, client: TestClient) -> None:
        resp = client.get("/screenshots/task-001/step_0.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_missing_screenshot(self, client: TestClient) -> None:
        resp = client.get("/screenshots/task-001/step_99.png")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client: TestClient) -> None:
        resp = client.get("/screenshots/../runs/task-001.json")
        # Should either 403 or 404, not serve the file
        assert resp.status_code in (403, 404)


class TestReplays:
    def test_serve_replay(self, client: TestClient) -> None:
        resp = client.get("/replays/task-001")
        assert resp.status_code == 200
        assert "replay for task-001" in resp.text

    def test_missing_replay(self, client: TestClient) -> None:
        resp = client.get("/replays/nonexistent")
        assert resp.status_code == 404


class TestIndex:
    def test_serves_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Pokant" in resp.text
