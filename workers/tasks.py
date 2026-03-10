"""
workers/tasks.py — Celery task definitions for async browser automation.

The Celery worker is started separately from the API server:
    celery -A workers.main worker --loglevel=info --concurrency=4
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
from celery import Celery
from celery.utils.log import get_task_logger

from computeruse.executor import TaskExecutor
from computeruse.models import TaskConfig

logger = get_task_logger(__name__)

# ---------------------------------------------------------------------------
# Celery application
# ---------------------------------------------------------------------------

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "computeruse",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Reliability
    task_acks_late=True,               # ack only after the task finishes
    worker_prefetch_multiplier=1,      # one task at a time per worker process
    task_reject_on_worker_lost=True,   # re-queue if the worker dies mid-task
    # Result expiry
    result_expires=86_400,             # keep results for 24 hours
    # Retry defaults (overridden per-task)
    task_max_retries=2,
    task_default_retry_delay=10,
)

# ---------------------------------------------------------------------------
# Database helpers (stubs — replace with real async DB calls via shared/db.py)
# ---------------------------------------------------------------------------

def _db_update(task_id: str, fields: Dict[str, Any]) -> None:
    """Persist task state changes.

    In production this should issue an UPDATE to the tasks table.
    Replace with:
        asyncio.run(db.execute(
            "UPDATE tasks SET … WHERE task_id = :task_id", {…}
        ))
    """
    logger.debug("DB update task=%s fields=%s", task_id, list(fields.keys()))


def _db_fetch(task_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a task row by ID.

    In production replace with:
        return asyncio.run(db.fetch_one(
            "SELECT * FROM tasks WHERE task_id = :task_id", {"task_id": task_id}
        ))
    """
    return None


# ---------------------------------------------------------------------------
# Main Celery task
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="computeruse.execute_task",
    max_retries=2,
    default_retry_delay=15,
    soft_time_limit=600,    # SIGTERM after 10 minutes
    time_limit=660,         # SIGKILL after 11 minutes
)
def execute_task(self, task_id: str, request: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """Execute a browser automation task asynchronously.

    This is the core Celery task.  It runs inside a worker process and is
    responsible for the full task lifecycle:

    1. Mark the task as ``"running"`` in the database.
    2. Build a :class:`TaskConfig` from the raw *request* dict.
    3. Run the :class:`TaskExecutor` inside a fresh event loop.
    4. Upload the replay artifact to S3 if one was produced.
    5. Write the final result back to the database.
    6. Fire the webhook (if a ``webhook_url`` was provided).

    Args:
        task_id:  Unique identifier for this task run.
        request:  Serialised :class:`TaskRequest` fields from the API layer.
        api_key:  The API key that created the task (used for scoped S3 paths).

    Returns:
        A dict representation of the final task state, which Celery stores
        in the result backend for polling.

    Raises:
        :exc:`celery.exceptions.Retry`: On recoverable errors (up to
        ``max_retries`` times).
    """
    logger.info("Starting task %s", task_id)

    # ── 1. Mark as running ─────────────────────────────────────────────────
    _db_update(task_id, {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "celery_task_id": self.request.id,
    })

    # ── 2. Build TaskConfig ────────────────────────────────────────────────
    config = TaskConfig(
        url=request["url"],
        task=request["task"],
        credentials=request.get("credentials"),
        output_schema=request.get("output_schema"),
        max_steps=request.get("max_steps", 50),
        timeout_seconds=request.get("timeout_seconds", 300),
    )

    # ── 3. Execute ─────────────────────────────────────────────────────────
    browserbase_api_key = os.environ.get("BROWSERBASE_API_KEY")
    executor = TaskExecutor(
        model=os.environ.get("DEFAULT_MODEL", "claude-sonnet-4-5"),
        headless=True,
        browserbase_api_key=browserbase_api_key,
    )

    try:
        task_result = asyncio.run(executor.execute(config))
    except Exception as exc:
        logger.exception("Executor raised an unexpected error for task %s", task_id)
        _handle_failure(task_id, request, str(exc))
        return _build_output(task_id, success=False, error=str(exc))

    # ── 4. Upload replay ───────────────────────────────────────────────────
    replay_url: Optional[str] = None
    if task_result.replay_path:
        try:
            replay_url = upload_replay(task_result.replay_path, task_id)
            logger.info("Replay uploaded for task %s → %s", task_id, replay_url)
        except Exception as exc:
            # A replay upload failure must not fail the whole task.
            logger.warning("Replay upload failed for task %s: %s", task_id, exc)

    # ── 5. Persist result ──────────────────────────────────────────────────
    completed_at = datetime.now(timezone.utc)
    final_status = "completed" if task_result.success else "failed"

    db_fields: Dict[str, Any] = {
        "status": final_status,
        "success": task_result.success,
        "result": task_result.result,
        "error": task_result.error,
        "replay_url": replay_url or task_result.replay_url,
        "replay_path": task_result.replay_path,
        "steps": task_result.steps,
        "duration_ms": task_result.duration_ms,
        "completed_at": completed_at.isoformat(),
    }
    _db_update(task_id, db_fields)
    logger.info(
        "Task %s finished: status=%s steps=%d duration=%dms",
        task_id,
        final_status,
        task_result.steps,
        task_result.duration_ms,
    )

    # ── 6. Fire webhook ────────────────────────────────────────────────────
    webhook_url: Optional[str] = request.get("webhook_url")
    if webhook_url:
        payload = {
            "task_id": task_id,
            "status": final_status,
            "success": task_result.success,
            "result": task_result.result,
            "error": task_result.error,
            "replay_url": replay_url,
            "steps": task_result.steps,
            "duration_ms": task_result.duration_ms,
            "completed_at": completed_at.isoformat(),
        }
        _fire_webhook(webhook_url, payload, task_id)

    return _build_output(
        task_id=task_id,
        success=task_result.success,
        result=task_result.result,
        error=task_result.error,
        replay_url=replay_url,
        steps=task_result.steps,
        duration_ms=task_result.duration_ms,
        completed_at=completed_at,
    )


# ---------------------------------------------------------------------------
# Helper: upload replay to S3
# ---------------------------------------------------------------------------

def upload_replay(file_path: str, task_id: str) -> str:
    """Upload a local replay file to S3 and return its public CDN URL.

    The file is stored at ``replays/<task_id>/<filename>`` in the configured
    S3 bucket.  The bucket must either be public or fronted by a CDN
    (CloudFront) that allows unauthenticated ``GetObject`` requests.

    Args:
        file_path:  Local filesystem path to the replay file.
        task_id:    Task identifier used to build the S3 key prefix.

    Returns:
        The HTTPS URL at which the replay can be accessed publicly.

    Raises:
        :exc:`RuntimeError`: If ``AWS_BUCKET_NAME`` is not configured.
        :exc:`botocore.exceptions.ClientError`: On S3 API errors.
        :exc:`FileNotFoundError`: If *file_path* does not exist.
    """
    bucket_name = os.environ.get("AWS_BUCKET_NAME", "computeruse-replays")
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    cdn_base = os.environ.get("AWS_CDN_BASE_URL")  # e.g. https://cdn.computeruse.dev

    import pathlib
    path = pathlib.Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Replay file not found: {file_path}")

    s3_key = f"replays/{task_id}/{path.name}"

    # Infer content type from extension.
    content_type = "application/json" if path.suffix == ".json" else "text/html"

    try:
        s3 = boto3.client(
            "s3",
            region_name=aws_region,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
        s3.upload_file(
            str(path),
            bucket_name,
            s3_key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=86400",
            },
        )
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"S3 upload failed for key '{s3_key}': {exc}") from exc

    if cdn_base:
        return f"{cdn_base.rstrip('/')}/{s3_key}"

    return f"https://{bucket_name}.s3.{aws_region}.amazonaws.com/{s3_key}"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fire_webhook(
    webhook_url: str,
    payload: Dict[str, Any],
    task_id: str,
    timeout: int = 10,
    retries: int = 3,
) -> None:
    """POST *payload* to *webhook_url* with simple linear retries.

    Failures are logged but never propagated — a webhook delivery failure
    must never affect the task's own success/failure status.

    Args:
        webhook_url: The URL to POST to.
        payload:     JSON-serialisable dict to send as the request body.
        task_id:     Used in log messages only.
        timeout:     Per-attempt HTTP timeout in seconds.
        retries:     Maximum number of delivery attempts.
    """
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=timeout,
                headers={"Content-Type": "application/json", "User-Agent": "ComputerUse/0.1"},
            )
            if response.status_code < 400:
                logger.info(
                    "Webhook delivered for task %s → %d (attempt %d)",
                    task_id, response.status_code, attempt,
                )
                return
            logger.warning(
                "Webhook attempt %d/%d for task %s returned HTTP %d",
                attempt, retries, task_id, response.status_code,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Webhook attempt %d/%d for task %s failed: %s",
                attempt, retries, task_id, exc,
            )

        if attempt < retries:
            time.sleep(2 ** attempt)  # 2s, 4s between retries

    logger.error(
        "Webhook delivery exhausted after %d attempts for task %s → %s",
        retries, task_id, webhook_url,
    )


def _handle_failure(task_id: str, request: Dict[str, Any], error: str) -> None:
    """Write a failed terminal state to the DB and optionally fire the webhook."""
    completed_at = datetime.now(timezone.utc)
    _db_update(task_id, {
        "status": "failed",
        "success": False,
        "error": error,
        "completed_at": completed_at.isoformat(),
    })

    webhook_url: Optional[str] = request.get("webhook_url")
    if webhook_url:
        _fire_webhook(
            webhook_url,
            {
                "task_id": task_id,
                "status": "failed",
                "success": False,
                "error": error,
                "completed_at": completed_at.isoformat(),
            },
            task_id,
        )


def _build_output(
    task_id: str,
    *,
    success: bool,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    replay_url: Optional[str] = None,
    steps: int = 0,
    duration_ms: int = 0,
    completed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the dict that Celery stores in the result backend.

    Keeping the result small and JSON-safe avoids serialisation surprises
    when consumers read it via :func:`celery.result.AsyncResult.get`.
    """
    return {
        "task_id": task_id,
        "status": "completed" if success else "failed",
        "success": success,
        "result": result,
        "error": error,
        "replay_url": replay_url,
        "steps": steps,
        "duration_ms": duration_ms,
        "completed_at": completed_at.isoformat() if completed_at else None,
    }
