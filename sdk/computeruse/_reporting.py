"""Best-effort reporting of run results to the Pokant API."""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("pokant")


def _ensure_uuid(task_id: str) -> str:
    """Return *task_id* unchanged if it's already a valid UUID, otherwise
    generate a fresh UUID (keeping the original in ``task_description``)."""
    try:
        uuid.UUID(task_id)
        return task_id
    except (ValueError, AttributeError):
        return str(uuid.uuid4())


def _try_compile_workflow(
    task_id: str,
    steps: list[Any],
    url: str,
    status: str,
) -> dict | None:
    """Best-effort compilation of steps into a replayable workflow.

    Returns the compiled workflow as a dict, or None on any failure.
    Never raises.
    """
    if not steps:
        return None
    try:
        from dataclasses import asdict

        from .compiler import WorkflowCompiler

        compiler = WorkflowCompiler()
        workflow = compiler.compile_from_steps(
            steps=steps,
            start_url=url,
            source_task_id=task_id,
            name=task_id,
        )
        return asdict(workflow)
    except Exception:
        logger.debug("Workflow compilation skipped for %s", task_id, exc_info=True)
        return None


def _report_to_api_sync(
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
    analysis: dict | None = None,
    url: str = "",
    result: dict | None = None,
    attempts: list[dict] | None = None,
) -> bool:
    """Synchronous POST of run results to the Pokant API ingest endpoint.

    Returns True if successful, False otherwise.
    Never raises -- all errors are caught and logged.
    """
    try:
        api_task_id = _ensure_uuid(task_id)
        # If the caller used a human-readable name, include it in the description
        if api_task_id != task_id:
            task_description = f"[{task_id}] {task_description}".strip()

        payload = {
            "task_id": api_task_id,
            "url": url,
            "task_description": task_description,
            "status": status,
            "result": result,
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
                    "context": _build_step_context(s),
                }
                for i, s in enumerate(steps)
            ],
            "created_at": (
                created_at.isoformat()
                if isinstance(created_at, datetime)
                else datetime.now(timezone.utc).isoformat()
            ),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "analysis": serialize_analysis(analysis),
        }

        # Merge adaptive retry attempt history into the analysis dict
        if attempts:
            if payload["analysis"] is None:
                payload["analysis"] = {}
            payload["analysis"]["attempts"] = attempts
            payload["analysis"]["total_attempts"] = len(attempts)
            payload["analysis"]["adaptive_retry_used"] = True

        compiled = _try_compile_workflow(task_id, steps, url, status)
        if compiled is not None:
            payload["compiled_workflow"] = compiled

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
        logger.warning("Failed to report run %s to %s", task_id, api_url, exc_info=True)
        return False


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
    analysis: dict | None = None,
    url: str = "",
    result: dict | None = None,
    attempts: list[dict] | None = None,
) -> bool:
    """Async wrapper around :func:`_report_to_api_sync`.

    Returns True if successful, False otherwise.
    Never raises -- all errors are caught and logged.
    """
    return _report_to_api_sync(
        api_url=api_url,
        api_key=api_key,
        task_id=task_id,
        task_description=task_description,
        status=status,
        steps=steps,
        cost_cents=cost_cents,
        error_category=error_category,
        error_message=error_message,
        duration_ms=duration_ms,
        created_at=created_at,
        analysis=analysis,
        url=url,
        result=result,
        attempts=attempts,
    )


def serialize_analysis(analysis: Any) -> dict | None:
    """Convert a RunAnalysis dataclass (or dict) to a JSON-safe dict."""
    if analysis is None:
        return None
    if isinstance(analysis, dict):
        return analysis
    try:
        from dataclasses import asdict
        return asdict(analysis)
    except Exception:
        return None


_ENRICHMENT_FIELDS = (
    "selectors", "intent", "intent_detail", "pre_url", "post_url",
    "expected_url_pattern", "expected_element", "expected_text",
    "fill_value_template", "element_text", "element_tag", "element_role",
    "verification_result",
)


def _build_step_context(step: Any) -> dict | None:
    """Merge explicit context with enrichment fields into a single dict."""
    ctx = dict(getattr(step, "context", None) or {})
    for key in _ENRICHMENT_FIELDS:
        val = getattr(step, key, None)
        if val is not None and val != "" and val != []:
            ctx[key] = val
    if not ctx:
        return None
    try:
        json.dumps(ctx, default=str)
        return ctx
    except (TypeError, ValueError):
        return None


def _encode_screenshot(step: Any) -> str | None:
    """Base64-encode screenshot bytes if present."""
    screenshot_bytes = getattr(step, "screenshot_bytes", None)
    if not screenshot_bytes:
        return None
    try:
        if isinstance(screenshot_bytes, str):
            return screenshot_bytes
        return base64.b64encode(screenshot_bytes).decode("ascii")
    except Exception:
        return None
