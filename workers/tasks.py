"""
workers/tasks.py — Celery task definitions for async browser automation.

Two tasks:
- execute_task: Picks up queued tasks, runs the browser agent, persists results.
- deliver_webhook: Delivers webhook notifications with HMAC-SHA256 signing.

Start the worker:
    celery -A workers.main worker --loglevel=info --pool=prefork --concurrency=2
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

import redis as redis_lib
from celery import Celery
from celery.utils.log import get_task_logger
from sqlalchemy import text

from workers.config import worker_settings

logger = get_task_logger(__name__)

# ---------------------------------------------------------------------------
# Celery application
# ---------------------------------------------------------------------------

celery_app = Celery("computeruse", broker=worker_settings.REDIS_URL)

celery_app.conf.update(
    # No result backend — results are persisted to the database.
    result_backend=None,
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Reliability
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
)

# ---------------------------------------------------------------------------
# Redis client (for distributed locks)
# ---------------------------------------------------------------------------

_redis = redis_lib.Redis.from_url(worker_settings.REDIS_URL, decode_responses=True)

# ---------------------------------------------------------------------------
# Webhook retry backoff schedule (seconds)
# ---------------------------------------------------------------------------

WEBHOOK_BACKOFFS = [30, 60, 120, 240, 480]

# ---------------------------------------------------------------------------
# Main Celery task
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="computeruse.execute_task",
    max_retries=0,
    soft_time_limit=660,
    time_limit=720,
)
def execute_task(self, task_id: str, task_config_json: str) -> None:
    """Execute a browser automation task.

    Lifecycle:
    1. Atomic claim — UPDATE … WHERE status='queued' (duplicate prevention).
    2. Redis lock — prevents concurrent execution of the same task.
    3. Build TaskConfig, create TaskExecutor, run it.
    4. Persist result, steps, and cost to the database.
    5. Increment account.monthly_steps_used.
    6. Upload replay to R2/S3.
    7. Enqueue webhook delivery if webhook_url is present.
    """
    from workers.db import get_sync_session

    logger.info("Starting task %s on worker %s", task_id, self.request.hostname)

    config_dict = json.loads(task_config_json)

    # ── 1. Atomic claim ────────────────────────────────────────────────────
    session = get_sync_session()
    try:
        result = session.execute(
            text(
                "UPDATE tasks "
                "SET status = 'running', "
                "    started_at = now(), "
                "    worker_id = :worker_id "
                "WHERE id = :task_id::uuid AND status = 'queued'"
            ),
            {"task_id": task_id, "worker_id": self.request.hostname},
        )
        session.commit()

        if result.rowcount == 0:  # type: ignore[attr-defined]
            logger.warning("Task %s already claimed or not queued, skipping", task_id)
            return
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # ── 2. Redis lock ──────────────────────────────────────────────────────
    timeout_seconds = config_dict.get("timeout_seconds", 300)
    lock_ttl = timeout_seconds + 60
    lock = _redis.lock(f"task_lock:{task_id}", timeout=lock_ttl)

    if not lock.acquire(blocking=False):
        logger.warning("Task %s lock already held, skipping", task_id)
        return

    try:
        # ── 3. Build config and execute ────────────────────────────────────
        from workers.browser_manager import BrowserManager
        from workers.executor import TaskExecutor
        from workers.models import TaskConfig, TaskResult

        config = TaskConfig(
            url=config_dict["url"],
            task=config_dict["task"],
            credentials=config_dict.get("credentials"),
            output_schema=config_dict.get("output_schema"),
            max_steps=config_dict.get("max_steps", 50),
            timeout_seconds=timeout_seconds,
            max_cost_cents=config_dict.get("max_cost_cents"),
            session_id=config_dict.get("session_id"),
        )

        from anthropic import Anthropic

        llm_client = Anthropic(api_key=worker_settings.ANTHROPIC_API_KEY)
        browser_manager = BrowserManager(
            browserbase_api_key=worker_settings.BROWSERBASE_API_KEY or None,
            browserbase_project_id=worker_settings.BROWSERBASE_PROJECT_ID or None,
        )
        executor = TaskExecutor(
            config=config,
            browser_manager=browser_manager,
            llm_client=llm_client,
            use_cloud=bool(worker_settings.BROWSERBASE_API_KEY),
        )

        task_result: TaskResult = asyncio.run(executor.execute())

        # ── 4-7. Persist result ────────────────────────────────────────────
        _persist_result(task_id, task_result, config_dict)

    except Exception as exc:
        logger.exception("Task %s failed with exception", task_id)
        _persist_failure(task_id, str(exc), config_dict)
    finally:
        try:
            lock.release()
        except Exception:
            logger.warning("Failed to release lock for task %s", task_id)


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------


def _persist_result(task_id: str, result: Any, config_dict: dict) -> None:
    """Write task result, steps, and account usage to the database."""
    from api.models.account import Account
    from api.models.task import Task
    from api.models.task_step import TaskStep
    from workers.db import get_sync_session

    session = get_sync_session()
    try:
        task = session.get(Task, uuid.UUID(task_id))
        if task is None:
            logger.error("Task %s not found in DB during persist", task_id)
            return

        task.status = result.status
        task.success = result.success
        task.result = result.result
        task.error_message = result.error
        task.total_steps = result.steps
        task.duration_ms = result.duration_ms
        task.cost_cents = Decimal(str(result.cost_cents))
        task.total_tokens_in = sum(s.tokens_in for s in result.step_data)
        task.total_tokens_out = sum(s.tokens_out for s in result.step_data)
        task.completed_at = datetime.now(timezone.utc)

        # Upload replay
        if result.step_data:
            try:
                replay_key = _upload_replay(task_id, result)
                task.replay_s3_key = replay_key
            except Exception as exc:
                logger.warning("Replay upload failed for task %s: %s", task_id, exc)

        # Insert step data
        for step in result.step_data:
            task_step = TaskStep(
                task_id=uuid.UUID(task_id),
                step_number=step.step_number,
                action_type=str(step.action_type),
                description=step.description[:500] if step.description else None,
                llm_tokens_in=step.tokens_in,
                llm_tokens_out=step.tokens_out,
                duration_ms=step.duration_ms,
                success=step.success,
                error_message=step.error,
            )
            session.add(task_step)

        # Increment account.monthly_steps_used
        account = session.get(Account, task.account_id)
        if account:
            account.monthly_steps_used += result.steps

        session.commit()

        # Enqueue webhook if present
        webhook_url = config_dict.get("webhook_url") or task.webhook_url
        if webhook_url:
            deliver_webhook.apply_async(
                args=[task_id, webhook_url],
                countdown=1,
            )

        logger.info(
            "Task %s persisted: status=%s steps=%d cost=%.2fc",
            task_id,
            result.status,
            result.steps,
            result.cost_cents,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _persist_failure(task_id: str, error: str, config_dict: dict) -> None:
    """Write a failed terminal state to the database."""
    from api.models.task import Task
    from workers.db import get_sync_session

    session = get_sync_session()
    try:
        task = session.get(Task, uuid.UUID(task_id))
        if task is None:
            return

        task.status = "failed"
        task.success = False
        task.error_message = error[:2000]
        task.completed_at = datetime.now(timezone.utc)
        session.commit()

        webhook_url = config_dict.get("webhook_url") or task.webhook_url
        if webhook_url:
            deliver_webhook.apply_async(args=[task_id, webhook_url], countdown=1)
    except Exception:
        session.rollback()
        logger.exception("Failed to persist failure for task %s", task_id)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Replay upload
# ---------------------------------------------------------------------------


def _upload_replay(task_id: str, result: Any) -> str:
    """Generate HTML replay and upload to R2/S3. Returns the S3 key."""
    import tempfile

    import boto3

    from workers.replay import ReplayGenerator

    replay_gen = ReplayGenerator(
        steps=result.step_data,
        task_metadata={
            "task_id": task_id,
            "duration_ms": result.duration_ms,
            "success": result.success,
        },
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        replay_path = f"{tmpdir}/{task_id}.html"
        replay_gen.generate(replay_path)

        s3_key = f"replays/{task_id}/replay.html"
        s3 = boto3.client(
            "s3",
            endpoint_url=worker_settings.R2_ENDPOINT or None,
            aws_access_key_id=worker_settings.R2_ACCESS_KEY,
            aws_secret_access_key=worker_settings.R2_SECRET_KEY,
        )
        s3.upload_file(
            replay_path,
            worker_settings.R2_BUCKET_NAME,
            s3_key,
            ExtraArgs={
                "ContentType": "text/html",
                "CacheControl": "public, max-age=86400",
            },
        )

    return s3_key


# ---------------------------------------------------------------------------
# Webhook delivery task
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="computeruse.deliver_webhook",
    max_retries=5,
)
def deliver_webhook(self, task_id: str, webhook_url: str) -> None:
    """Deliver a webhook notification for a completed/failed task.

    Signs the payload with HMAC-SHA256 using the account's webhook_secret.
    Retries up to 5 times with backoff: 30s, 60s, 120s, 240s, 480s.
    On final failure, marks task.webhook_delivered = False.
    """
    import requests

    from api.models.account import Account
    from api.models.task import Task
    from workers.db import get_sync_session

    session = get_sync_session()
    try:
        task = session.get(Task, uuid.UUID(task_id))
        if task is None:
            logger.error("Webhook: task %s not found", task_id)
            return

        account = session.get(Account, task.account_id)

        # Build payload
        payload = {
            "task_id": task_id,
            "status": task.status,
            "result": task.result,
            "replay_url": (
                f"https://r2.computeruse.dev/{task.replay_s3_key}"
                if task.replay_s3_key
                else None
            ),
            "duration_ms": task.duration_ms,
        }
        payload_bytes = json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ).encode()

        # HMAC signature
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "ComputerUse/1.0",
        }

        if account and account.webhook_secret:
            signature = hmac.new(
                account.webhook_secret.encode(),
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-CU-Signature"] = signature

        # POST
        response = requests.post(
            webhook_url,
            data=payload_bytes,
            headers=headers,
            timeout=10,
        )

        if response.status_code >= 400:
            raise requests.RequestException(
                f"Webhook returned HTTP {response.status_code}"
            )

        # Mark delivered
        task.webhook_delivered = True
        session.commit()

        logger.info(
            "Webhook delivered for task %s -> %d (attempt %d/%d)",
            task_id,
            response.status_code,
            self.request.retries + 1,
            self.max_retries + 1,
        )

    except requests.RequestException as exc:
        session.rollback()
        if self.request.retries >= self.max_retries:
            _mark_webhook_failed(task_id)
            logger.error(
                "Webhook delivery exhausted after %d attempts for task %s -> %s",
                self.max_retries + 1,
                task_id,
                webhook_url,
            )
            return
        backoff = WEBHOOK_BACKOFFS[min(self.request.retries, len(WEBHOOK_BACKOFFS) - 1)]
        raise self.retry(countdown=backoff, exc=exc)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _mark_webhook_failed(task_id: str) -> None:
    """Mark webhook_delivered=False on final failure."""
    from api.models.task import Task
    from workers.db import get_sync_session

    session = get_sync_session()
    try:
        task = session.get(Task, uuid.UUID(task_id))
        if task:
            task.webhook_delivered = False
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()
