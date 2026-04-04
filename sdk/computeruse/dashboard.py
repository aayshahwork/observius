"""
computeruse/dashboard.py — Local debugging dashboard.

A minimal FastAPI application that serves run data from the .pokant/
directory on disk. No database required.

Usage::

    from computeruse.dashboard import create_app
    import uvicorn

    app = create_app(".pokant")
    uvicorn.run(app, host="0.0.0.0", port=8080)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse


def _load_runs(runs_dir: Path) -> List[Dict[str, Any]]:
    """Read all JSON files from the runs directory, skipping malformed ones."""
    if not runs_dir.is_dir():
        return []
    results: List[Dict[str, Any]] = []
    for f in sorted(runs_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "task_id" in data:
                results.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return results


def create_app(data_dir: str = ".pokant") -> FastAPI:
    """Create the Pokant local dashboard application.

    Args:
        data_dir: Path to the .pokant data directory.

    Returns:
        A configured FastAPI app ready to serve.
    """
    base = Path(data_dir).resolve()
    runs_dir = base / "runs"
    screenshots_dir = base / "screenshots"
    replays_dir = base / "replays"
    workflows_dir = base / "workflows"

    app = FastAPI(title="Pokant Dashboard")

    # -- API routes --------------------------------------------------------

    @app.get("/api/runs")
    def list_runs() -> List[Dict[str, Any]]:
        return _load_runs(runs_dir)

    @app.get("/api/runs/{task_id}")
    def get_run(task_id: str) -> Dict[str, Any]:
        run_file = runs_dir / f"{task_id}.json"
        if not run_file.is_file():
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            data = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail="Corrupt run file") from exc
        return data

    @app.get("/api/stats")
    def get_stats() -> Dict[str, Any]:
        all_runs = _load_runs(runs_dir)
        total = len(all_runs)
        completed = sum(1 for r in all_runs if r.get("status") == "completed")
        failed = sum(1 for r in all_runs if r.get("status") == "failed")
        timeout = sum(
            1 for r in all_runs
            if r.get("error_category") == "timeout"
            or r.get("status") == "timeout"
        )
        total_cost = sum(r.get("cost_cents", 0) for r in all_runs)
        durations = [r["duration_ms"] for r in all_runs if r.get("duration_ms")]
        avg_duration = int(sum(durations) / len(durations)) if durations else 0

        error_categories: Dict[str, int] = {}
        for r in all_runs:
            cat = r.get("error_category")
            if cat:
                error_categories[cat] = error_categories.get(cat, 0) + 1

        # Adaptive retry stats
        retry_runs = [r for r in all_runs if r.get("total_attempts", 1) > 1]
        retry_category_counts: Dict[str, int] = {}
        total_diag_cost = 0.0
        for r in retry_runs:
            for a in r.get("attempts", []):
                diag = a.get("diagnosis")
                if diag:
                    total_diag_cost += diag.get("analysis_cost_cents", 0)
                    cat = diag.get("category")
                    if cat:
                        retry_category_counts[cat] = (
                            retry_category_counts.get(cat, 0) + 1
                        )

        return {
            "total_runs": total,
            "completed": completed,
            "failed": failed,
            "timeout": timeout,
            "success_rate": round(completed / total * 100, 1) if total else 0,
            "total_cost_cents": total_cost,
            "avg_duration_ms": avg_duration,
            "error_categories": error_categories,
            "adaptive_retry_stats": {
                "runs_retried": len(retry_runs),
                "avg_attempts": round(
                    sum(r.get("total_attempts", 1) for r in retry_runs)
                    / max(len(retry_runs), 1),
                    1,
                ),
                "total_diagnosis_cost_cents": round(total_diag_cost, 4),
                "category_counts": retry_category_counts,
            },
        }

    @app.get("/screenshots/{path:path}")
    def serve_screenshot(path: str) -> FileResponse:
        file_path = (screenshots_dir / path).resolve()
        # Prevent path traversal
        if not str(file_path).startswith(str(screenshots_dir.resolve())):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="Screenshot not found")
        return FileResponse(file_path, media_type="image/png")

    @app.get("/replays/{task_id}")
    def serve_replay(task_id: str) -> HTMLResponse:
        replay_file = replays_dir / f"{task_id}.html"
        if not replay_file.is_file():
            raise HTTPException(status_code=404, detail="Replay not found")
        return HTMLResponse(replay_file.read_text(encoding="utf-8"))

    # -- Workflow routes ----------------------------------------------------

    @app.get("/api/workflows")
    def list_workflows() -> List[Dict[str, Any]]:
        if not workflows_dir.is_dir():
            return []
        results: List[Dict[str, Any]] = []
        for f in sorted(workflows_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "steps" in data:
                    results.append(data)
            except (OSError, json.JSONDecodeError):
                continue
        return results

    @app.get("/api/workflows/{name}")
    def get_workflow(name: str) -> Dict[str, Any]:
        wf_file = (workflows_dir / f"{name}.json").resolve()
        # Prevent path traversal
        if not str(wf_file).startswith(str(workflows_dir.resolve())):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not wf_file.is_file():
            raise HTTPException(status_code=404, detail="Workflow not found")
        try:
            data = json.loads(wf_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=500, detail="Corrupt workflow file"
            ) from exc
        return data

    @app.get("/")
    def index() -> HTMLResponse:
        html_path = Path(__file__).parent / "static" / "index.html"
        if not html_path.is_file():
            raise HTTPException(
                status_code=500, detail="Dashboard HTML not found"
            )
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    return app
