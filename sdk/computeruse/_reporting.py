"""Best-effort reporting of run results to the Observius API."""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("observius")


async def report_to_api(
    api_url: str,
    api_key: str,
    task_id: str,
    task_description: str,
    status: str,
    steps: list[Any],
    cost_cents: float,
    error_category: str | None,
    error_message: str | None,
    duration_ms: int,
    created_at: datetime | None,
) -> bool:
    """POST run results to the Observius API ingest endpoint.

    Returns True if successful, False otherwise.
    Never raises -- all errors are caught and logged at debug level.
    """
    try:
        payload = {
            "task_id": task_id,
            "task_description": task_description,
            "status": status,
            "cost_cents": cost_cents,
            "total_tokens_in": sum(getattr(s, "tokens_in", 0) for s in steps),
            "total_tokens_out": sum(getattr(s, "tokens_out", 0) for s in steps),
            "error_category": error_category,
            "error_message": error_message,
            "executor_mode": "sdk",
            "duration_ms": duration_ms,
            "steps": [
                {
                    "step_number": i + 1,
                    "action_type": getattr(s, "action_type", "unknown"),
                    "description": getattr(s, "description", ""),
                    "tokens_in": getattr(s, "tokens_in", 0),
                    "tokens_out": getattr(s, "tokens_out", 0),
                    "duration_ms": getattr(s, "duration_ms", 0),
                    "success": getattr(s, "success", True),
                    "error": getattr(s, "error", None),
                    "screenshot_base64": _encode_screenshot(s),
                }
                for i, s in enumerate(steps)
            ],
            "created_at": (
                created_at.isoformat()
                if isinstance(created_at, datetime)
                else datetime.now(timezone.utc).isoformat()
            ),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{api_url.rstrip('/')}/api/v1/tasks/ingest",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
            },
            method="POST",
        )

        urllib.request.urlopen(req, timeout=15)
        logger.debug("Reported run %s to %s", task_id, api_url)
        return True

    except Exception:
        logger.debug("Failed to report run %s to API", task_id, exc_info=True)
        return False


def _encode_screenshot(step: Any) -> str | None:
    """Base64-encode screenshot bytes if present."""
    screenshot_bytes = getattr(step, "screenshot_bytes", None)
    if not screenshot_bytes:
        return None
    try:
        return base64.b64encode(screenshot_bytes).decode("ascii")
    except Exception:
        return None
