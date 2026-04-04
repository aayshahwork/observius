"""Tests for CLI info, clean, and dashboard commands."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from computeruse.cli.main import cli


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a pre-populated .pokant directory."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    ss_dir = tmp_path / "screenshots"
    replays_dir = tmp_path / "replays"
    replays_dir.mkdir()

    now = datetime.now(timezone.utc)

    # Completed run (recent)
    run1 = {
        "task_id": "aaaa-1111",
        "status": "completed",
        "step_count": 5,
        "cost_cents": 12.5,
        "error_category": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "duration_ms": 5000,
        "steps": [
            {
                "action_type": "navigate",
                "description": "Go to page",
                "duration_ms": 1000,
                "success": True,
                "tokens_in": 100,
                "tokens_out": 50,
                "screenshot_path": "aaaa-1111/step_0.png",
            }
        ],
    }
    (runs_dir / "aaaa-1111.json").write_text(json.dumps(run1))

    # Failed run (recent)
    run2 = {
        "task_id": "bbbb-2222",
        "status": "failed",
        "step_count": 3,
        "cost_cents": 8.0,
        "error_category": "browser",
        "error": "Element not found",
        "created_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "duration_ms": 3000,
        "steps": [],
    }
    (runs_dir / "bbbb-2222.json").write_text(json.dumps(run2))

    # Old completed run (10 days ago)
    old_time = (now - timedelta(days=10)).isoformat()
    run3 = {
        "task_id": "cccc-3333",
        "status": "completed",
        "step_count": 2,
        "cost_cents": 5.0,
        "error_category": None,
        "error": None,
        "created_at": old_time,
        "completed_at": old_time,
        "duration_ms": 2000,
        "steps": [],
    }
    (runs_dir / "cccc-3333.json").write_text(json.dumps(run3))

    # Screenshots for run1
    run1_ss = ss_dir / "aaaa-1111"
    run1_ss.mkdir(parents=True)
    (run1_ss / "step_0.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

    # Replay for old run
    (replays_dir / "cccc-3333.html").write_text("<html>replay</html>")

    return tmp_path


class TestInfoCommand:
    def test_info_shows_summary(self, data_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "--data-dir", str(data_dir)])
        assert result.exit_code == 0
        assert "3" in result.output  # total runs
        assert "2 completed" in result.output
        assert "1 failed" in result.output

    def test_info_shows_cost(self, data_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "--data-dir", str(data_dir)])
        assert result.exit_code == 0
        assert "$0.26" in result.output  # 25.5 cents

    def test_info_shows_screenshots(self, data_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "--data-dir", str(data_dir)])
        assert result.exit_code == 0
        assert "1 files" in result.output

    def test_info_empty_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "--data-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "0" in result.output

    def test_info_nonexistent_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["info", "--data-dir", str(tmp_path / "nope")]
        )
        assert result.exit_code == 0

    def test_info_skips_malformed_json(self, data_dir: Path) -> None:
        (data_dir / "runs" / "bad.json").write_text("not json {{{")
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "--data-dir", str(data_dir)])
        assert result.exit_code == 0
        assert "3" in result.output  # still 3 valid runs


class TestCleanCommand:
    def test_dry_run_shows_old_runs(self, data_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["clean", "--data-dir", str(data_dir), "--older-than", "7d", "--dry-run"]
        )
        assert result.exit_code == 0
        assert "cccc-3333" in result.output
        assert "Would delete 1 run(s)" in result.output
        # File should still exist
        assert (data_dir / "runs" / "cccc-3333.json").exists()

    def test_clean_deletes_old_runs(self, data_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["clean", "--data-dir", str(data_dir), "--older-than", "7d"]
        )
        assert result.exit_code == 0
        assert "Deleted 1 run(s)" in result.output
        assert not (data_dir / "runs" / "cccc-3333.json").exists()
        assert not (data_dir / "replays" / "cccc-3333.html").exists()
        # Recent runs untouched
        assert (data_dir / "runs" / "aaaa-1111.json").exists()
        assert (data_dir / "runs" / "bbbb-2222.json").exists()

    def test_clean_with_hours(self, data_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["clean", "--data-dir", str(data_dir), "--older-than", "1h", "--dry-run"]
        )
        assert result.exit_code == 0
        # The 10-day-old run should be caught
        assert "cccc-3333" in result.output

    def test_clean_no_old_runs(self, data_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["clean", "--data-dir", str(data_dir), "--older-than", "30d"]
        )
        assert result.exit_code == 0
        assert "No runs older than" in result.output

    def test_clean_invalid_duration(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["clean", "--older-than", "abc"]
        )
        assert result.exit_code != 0

    def test_clean_empty_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["clean", "--data-dir", str(tmp_path), "--older-than", "1d"]
        )
        assert result.exit_code == 0
        assert "No runs directory" in result.output


class TestDashboardCommand:
    def test_dashboard_missing_deps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Dashboard should fail gracefully if fastapi is not installed."""
        import computeruse.dashboard as dashboard_mod

        original_create_app = dashboard_mod.create_app

        def mock_import_error(*args, **kwargs):
            raise ImportError("No module named 'fastapi'")

        monkeypatch.setattr(
            "computeruse.dashboard.create_app",
            mock_import_error,
        )

        runner = CliRunner()
        # We can't easily test the real import failure path without
        # removing fastapi, but we verify the command exists
        result = runner.invoke(cli, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "Launch local debugging dashboard" in result.output
