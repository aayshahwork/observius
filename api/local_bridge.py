"""
api/local_bridge.py — Local bridge API for demo/dev mode.

Serves task results from .local_tasks.json to the dashboard at localhost:3000.
No auth. CORS enabled. Computes health analytics from stored tasks.

Usage:
    python api/local_bridge.py
"""

from __future__ import annotations

import base64
import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
STORAGE_FILE = REPO_ROOT / ".local_tasks.json"


def _load() -> Dict[str, Any]:
    if STORAGE_FILE.exists():
        try:
            return json.loads(STORAGE_FILE.read_text())
        except Exception:
            pass
    return {"tasks": [], "steps": {}}


def _save(data: Dict[str, Any]) -> None:
    STORAGE_FILE.write_text(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="ComputerUse Local Bridge", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCREENSHOT_DIR = REPO_ROOT / "replays" / "screenshots"
BRIDGE_BASE_URL = "http://localhost:8000"


def _save_step_screenshot(task_id: str, step_number: int, b64: str) -> Optional[str]:
    """Decode base64 PNG screenshot, write to disk, return a URL the dashboard can fetch."""
    try:
        png_bytes = base64.b64decode(b64)
    except Exception:
        return None
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{task_id}_step_{step_number:04d}.png"
        (SCREENSHOT_DIR / filename).write_bytes(png_bytes)
        return f"{BRIDGE_BASE_URL}/api/v1/local-files/screenshots/{filename}"
    except Exception:
        return None


_TASK_DEFAULTS: Dict[str, Any] = {
    "url": None,
    "task_description": None,
    "status": "completed",
    "success": True,
    "result": None,
    "error": None,
    "replay_url": None,
    "steps": 0,
    "duration_ms": 0,
    "created_at": None,
    "completed_at": None,
    "retry_count": 0,
    "retry_of_task_id": None,
    "error_category": None,
    "cost_cents": 0,
    "total_tokens_in": 0,
    "total_tokens_out": 0,
    "executor_mode": "sdk",
    "analysis": None,
}

_TERMINAL = {"completed", "failed", "timeout", "cancelled"}


def _normalise_task(raw: Dict[str, Any]) -> Dict[str, Any]:
    task = dict(_TASK_DEFAULTS)
    task.update({k: v for k, v in raw.items() if k in _TASK_DEFAULTS or k == "task_id"})
    if not task.get("task_id"):
        task["task_id"] = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    if not task.get("created_at"):
        task["created_at"] = now
    if not task.get("completed_at") and task.get("status") in _TERMINAL:
        task["completed_at"] = now
    return task


def _parse_dt(s: Optional[str]) -> datetime:
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _period_hours(period: str) -> int:
    return {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}.get(period, 24)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.post("/api/v1/tasks/ingest")
async def ingest_task(request: Request) -> Dict[str, Any]:
    """Accepts SDK _reporting.py payload (ObserviusTracker / wrap / track).

    _reporting.py sends to POST {api_url}/api/v1/tasks/ingest with:
      task_id, task_description, status, cost_cents, total_tokens_in,
      total_tokens_out, error_category, error_message, executor_mode,
      duration_ms, steps[], created_at, completed_at, analysis.

    This endpoint also accepts two optional extension fields that the demo
    script adds manually:
      url     — target URL scraped
      result  — extracted structured data
    """
    body = await request.json()
    raw_steps = body.get("steps") or []

    task_id = body.get("task_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Build the incoming fields (only non-None values override existing ones)
    incoming: Dict[str, Any] = {"task_id": task_id}
    _map = {
        "url": "url",
        "task_description": "task_description",
        "status": "status",
        "result": "result",
        "error_message": "error",
        "duration_ms": "duration_ms",
        "created_at": "created_at",
        "completed_at": "completed_at",
        "error_category": "error_category",
        "cost_cents": "cost_cents",
        "total_tokens_in": "total_tokens_in",
        "total_tokens_out": "total_tokens_out",
        "executor_mode": "executor_mode",
        "analysis": "analysis",
    }
    for src, dst in _map.items():
        if src in body and body[src] is not None:
            incoming[dst] = body[src]
    if "status" in incoming:
        incoming["success"] = incoming["status"] == "completed"
    if raw_steps:
        incoming["steps"] = len(raw_steps)

    # Merge into existing task (upsert: new fields win, existing fields kept)
    data = _load()
    existing = next((t for t in data["tasks"] if t.get("task_id") == task_id), None)
    if existing:
        task = dict(existing)
        task.update({k: v for k, v in incoming.items() if v is not None})
    else:
        task = dict(_TASK_DEFAULTS)
        task.update(incoming)
        if not task.get("created_at"):
            task["created_at"] = now
        if not task.get("completed_at") and task.get("status") in _TERMINAL:
            task["completed_at"] = now

    def _resolve_screenshot(s: Dict[str, Any], step_num: int) -> Optional[str]:
        # Prefer an already-resolved URL (e.g. from _patch_ingest in demo scripts)
        if s.get("screenshot_url"):
            return s["screenshot_url"]
        # Decode base64 screenshot sent by _reporting.py → save PNG → return URL
        b64 = s.get("screenshot_base64")
        if b64:
            return _save_step_screenshot(task_id, step_num, b64)
        return None

    steps = [
        {
            "step_number": s.get("step_number", i + 1),
            "action_type": s.get("action_type", "unknown"),
            "description": s.get("description"),
            "screenshot_url": _resolve_screenshot(s, s.get("step_number", i + 1)),
            "tokens_in": s.get("tokens_in", 0),
            "tokens_out": s.get("tokens_out", 0),
            "duration_ms": s.get("duration_ms", 0),
            "success": s.get("success", True),
            "error": s.get("error"),
            "created_at": now,
            "context": s.get("context"),
        }
        for i, s in enumerate(raw_steps)
    ]

    data["tasks"] = [t for t in data["tasks"] if t.get("task_id") != task_id]
    data["tasks"].append(task)
    if steps:
        data.setdefault("steps", {})[task_id] = steps
    _save(data)
    return {"status": "ok", "task_id": task_id}


@app.get("/api/v1/tasks")
async def list_tasks(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    retry_of_task_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    data = _load()
    tasks = list(data["tasks"])

    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            tasks = [t for t in tasks if _parse_dt(t.get("created_at")) >= since_dt]
        except ValueError:
            pass
    if session_id:
        tasks = [t for t in tasks if t.get("session_id") == session_id]
    if retry_of_task_id:
        tasks = [t for t in tasks if t.get("retry_of_task_id") == retry_of_task_id]

    tasks.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    total = len(tasks)
    return {
        "tasks": tasks[offset: offset + limit],
        "total": total,
        "has_more": (offset + limit) < total,
    }


@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str) -> Dict[str, Any]:
    data = _load()
    for task in data["tasks"]:
        if task.get("task_id") == task_id:
            return task
    raise HTTPException(status_code=404, detail="Task not found")


@app.post("/api/v1/tasks")
async def create_task(request: Request) -> Dict[str, Any]:
    body = await request.json()
    step_data = body.pop("_step_data", [])
    task = _normalise_task(body)
    task_id = task["task_id"]

    data = _load()
    data["tasks"] = [t for t in data["tasks"] if t.get("task_id") != task_id]
    data["tasks"].append(task)
    if step_data:
        data.setdefault("steps", {})[task_id] = step_data
    _save(data)
    return task


@app.get("/api/v1/tasks/{task_id}/steps")
async def get_task_steps(task_id: str) -> List[Dict[str, Any]]:
    data = _load()
    return data.get("steps", {}).get(task_id, [])


@app.delete("/api/v1/tasks/{task_id}")
async def cancel_task(task_id: str) -> Dict[str, str]:
    return {"task_id": task_id, "status": "cancelled"}


@app.post("/api/v1/tasks/{task_id}/retry")
async def retry_task(task_id: str) -> Dict[str, Any]:
    data = _load()
    for task in data["tasks"]:
        if task.get("task_id") == task_id:
            return task
    raise HTTPException(status_code=404, detail="Task not found")


@app.get("/api/v1/tasks/{task_id}/replay")
async def get_replay(task_id: str) -> Dict[str, Any]:
    return {"task_id": task_id, "replay_url": None, "available": False}


# ---------------------------------------------------------------------------
# Local file serving (screenshots + replays from ./replays/)
# ---------------------------------------------------------------------------

@app.get("/api/v1/local-files/{file_path:path}")
async def serve_local_file(file_path: str) -> FileResponse:
    full_path = REPO_ROOT / "replays" / file_path
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # Safety: ensure resolved path stays under replays/
    try:
        full_path.resolve().relative_to((REPO_ROOT / "replays").resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    return FileResponse(str(full_path))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@app.get("/api/v1/sessions")
async def list_sessions() -> List[Dict[str, Any]]:
    return []


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@app.get("/api/v1/alerts")
async def list_alerts(
    limit: int = Query(50),
    offset: int = Query(0),
    acknowledged: Optional[bool] = Query(None),
) -> Dict[str, Any]:
    return {"alerts": [], "total": 0, "has_more": False}


@app.post("/api/v1/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str) -> Dict[str, Any]:
    return {"id": alert_id, "acknowledged": True}


# ---------------------------------------------------------------------------
# Billing / Account (stubs — enough to stop the dashboard erroring)
# ---------------------------------------------------------------------------

@app.get("/api/v1/billing/usage")
async def get_billing_usage() -> Dict[str, Any]:
    data = _load()
    total_steps = sum(t.get("steps", 0) or 0 for t in data["tasks"])
    return {
        "monthly_steps_used": total_steps,
        "monthly_step_limit": 500,
        "tier": "free",
        "daily_usage": [],
    }


@app.get("/api/v1/account/api-keys")
async def list_api_keys() -> List[Dict[str, Any]]:
    return [
        {
            "key_id": "local-bridge-key",
            "label": "Local Bridge",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used_at": None,
        }
    ]


# ---------------------------------------------------------------------------
# Analytics / Health — computed live from stored tasks
# ---------------------------------------------------------------------------

@app.get("/api/v1/analytics/health")
async def get_health_analytics(period: str = Query("24h")) -> Dict[str, Any]:
    data = _load()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_period_hours(period))
    tasks = [t for t in data["tasks"] if _parse_dt(t.get("created_at")) >= cutoff]

    total = len(tasks)
    completed = sum(1 for t in tasks if t.get("status") == "completed")
    failed = sum(1 for t in tasks if t.get("status") == "failed")
    timed_out = sum(1 for t in tasks if t.get("status") == "timeout")
    success_rate = round(completed / total * 100, 1) if total else 0.0
    total_cost = sum(t.get("cost_cents", 0) or 0 for t in tasks)
    avg_cost = round(total_cost / total, 2) if total else 0.0
    total_tokens = sum(
        (t.get("total_tokens_in", 0) or 0) + (t.get("total_tokens_out", 0) or 0)
        for t in tasks
    )
    durations = [t.get("duration_ms", 0) or 0 for t in tasks if t.get("duration_ms")]
    avg_duration = int(sum(durations) / len(durations)) if durations else 0

    # Error category breakdown
    error_counts: Dict[str, int] = defaultdict(int)
    for t in tasks:
        cat = t.get("error_category")
        if cat:
            error_counts[cat] += 1
    top_errors = [
        {"category": k, "count": v}
        for k, v in sorted(error_counts.items(), key=lambda x: -x[1])
    ]

    # Top failing URLs
    url_fail: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"failure_count": 0, "last_failure": ""})
    for t in tasks:
        if t.get("status") in ("failed", "timeout") and t.get("url"):
            u = t["url"]
            url_fail[u]["failure_count"] += 1
            ts = t.get("completed_at") or t.get("created_at") or ""
            if ts > url_fail[u]["last_failure"]:
                url_fail[u]["last_failure"] = ts
    top_failing = [
        {"url": u, **counts}
        for u, counts in sorted(url_fail.items(), key=lambda x: -x[1]["failure_count"])
    ][:5]

    # Hourly breakdown (24 buckets regardless of period)
    now = datetime.now(timezone.utc)
    hourly: Dict[str, Dict[str, Any]] = {}
    for i in range(24):
        h = (now - timedelta(hours=23 - i)).replace(minute=0, second=0, microsecond=0)
        key = h.isoformat()
        hourly[key] = {"hour": key, "completed": 0, "failed": 0, "cost_cents": 0}
    for t in tasks:
        dt = _parse_dt(t.get("created_at"))
        if dt == datetime.min.replace(tzinfo=timezone.utc):
            continue
        hkey = dt.replace(minute=0, second=0, microsecond=0).isoformat()
        if hkey in hourly:
            if t.get("status") == "completed":
                hourly[hkey]["completed"] += 1
            elif t.get("status") in ("failed", "timeout"):
                hourly[hkey]["failed"] += 1
            hourly[hkey]["cost_cents"] += t.get("cost_cents", 0) or 0

    def _executor_stats(mode: str) -> Dict[str, Any]:
        ex = [t for t in tasks if t.get("executor_mode") == mode]
        n = len(ex)
        sr = round(sum(1 for t in ex if t.get("status") == "completed") / n * 100, 1) if n else 0.0
        costs = [t.get("cost_cents", 0) or 0 for t in ex]
        return {"count": n, "success_rate": sr, "avg_cost": round(sum(costs) / n, 2) if n else 0.0}

    return {
        "period": period,
        "total_runs": total,
        "completed": completed,
        "failed": failed,
        "timeout": timed_out,
        "success_rate": success_rate,
        "success_rate_trend": 0.0,
        "total_cost_cents": round(total_cost, 2),
        "avg_cost_per_run": avg_cost,
        "total_tokens": total_tokens,
        "avg_duration_ms": avg_duration,
        "top_errors": top_errors,
        "top_failing_urls": top_failing,
        "hourly_breakdown": list(hourly.values()),
        "executor_breakdown": {
            "browser_use": _executor_stats("browser_use"),
            "native": _executor_stats("native"),
            "sdk": _executor_stats("sdk"),
        },
        "retry_stats": {"total_retried": 0, "retry_success_rate": 0.0, "avg_attempts": 1.0},
        "alerts": [],
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"ComputerUse local bridge → http://localhost:8000")
    print(f"Storage: {STORAGE_FILE}")
    print(f"Dashboard: http://localhost:3000  (login with any key)")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
