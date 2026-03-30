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
                "WHERE id = CAST(:task_id AS uuid) AND status = 'queued' "
                "RETURNING account_id"
            ),
            {"task_id": task_id, "worker_id": self.request.hostname},
        )
        row = result.first()
        session.commit()

        if row is None:
            logger.warning("Task %s already claimed or not queued, skipping", task_id)
            return

        account_id = str(row.account_id)
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
        from workers.shutdown import (
            deregister_in_flight,
            is_shutting_down,
            register_in_flight,
        )

        config = TaskConfig(
            url=config_dict["url"],
            task=config_dict["task"],
            credentials=config_dict.get("credentials"),
            output_schema=config_dict.get("output_schema"),
            max_steps=config_dict.get("max_steps", 50),
            timeout_seconds=timeout_seconds,
            max_cost_cents=config_dict.get("max_cost_cents"),
            session_id=config_dict.get("session_id"),
            executor_mode=config_dict.get("executor_mode", "browser_use"),
        )

        from anthropic import Anthropic

        llm_client = Anthropic(api_key=worker_settings.ANTHROPIC_API_KEY)
        browser_manager = BrowserManager(
            browserbase_api_key=worker_settings.BROWSERBASE_API_KEY or None,
            browserbase_project_id=worker_settings.BROWSERBASE_PROJECT_ID or None,
        )

        # Shared step list: executor appends to this, shutdown handler reads it
        shared_steps: list = []

        # Register for graceful shutdown tracking
        register_in_flight(
            task_id=task_id,
            lock=lock,
            browser_manager=browser_manager,
            step_data=shared_steps,
            config_json=task_config_json,
        )

        executor = TaskExecutor(
            config=config,
            browser_manager=browser_manager,
            llm_client=llm_client,
            use_cloud=bool(worker_settings.BROWSERBASE_API_KEY),
            shutdown_check=is_shutting_down,
            step_data=shared_steps,
            account_id=account_id,
        )

        task_result: TaskResult = asyncio.run(executor.execute())

        # Record cost for Prometheus metrics and canary tracking.
        # Key by Celery task ID (self.request.id), not our application task_id,
        # because signal handlers look up metadata by sender.request.id.
        from workers.metrics import record_task_cost

        record_task_cost(
            self.request.id,
            task_result.cost_cents,
            task_result.steps,
            tokens_in=task_result.total_tokens_in,
            tokens_out=task_result.total_tokens_out,
        )

        # ── 4-7. Persist result ────────────────────────────────────────────
        _persist_result(task_id, task_result, config_dict)

        # ── Auto-retry for tasks that completed with failure status ────────
        if not task_result.success:
            try:
                from workers.error_classifier import classify_error_message

                classified = classify_error_message(task_result.error or "")
                _maybe_auto_retry(
                    task_id,
                    classified.category,
                    config_dict,
                    classified.retry_after_seconds,
                )
            except Exception:
                logger.warning(
                    "Auto-retry evaluation failed for task %s", task_id
                )

    except Exception as exc:
        logger.exception("Task %s failed with exception", task_id)
        _persist_failure(task_id, str(exc), config_dict)
        # Classify error and attempt auto-retry
        try:
            from workers.error_classifier import classify_error

            classified = classify_error(exc)
            _maybe_auto_retry(
                task_id,
                classified.category,
                config_dict,
                classified.retry_after_seconds,
            )
        except Exception:
            logger.warning("Auto-retry evaluation failed for task %s", task_id)
    finally:
        try:
            deregister_in_flight(task_id)
        except NameError:
            pass  # shutdown module import failed earlier
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
        task.total_tokens_in = result.total_tokens_in or sum(s.tokens_in for s in result.step_data)
        task.total_tokens_out = result.total_tokens_out or sum(s.tokens_out for s in result.step_data)
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
# Auto-retry logic
# ---------------------------------------------------------------------------


def _maybe_auto_retry(
    task_id: str,
    error_category: str,
    config_dict: dict,
    retry_after_seconds: int | None = None,
) -> None:
    """Auto-retry a failed task if the error is transient and retries remain.

    Creates a new task row (flat retry chain via retry_of_task_id) and
    enqueues it to Celery with a backoff delay.
    """
    from api.models.account import Account
    from api.models.task import Task
    from workers.db import get_sync_session
    from workers.retry_policy import should_retry_task

    session = get_sync_session()
    try:
        task = session.get(Task, uuid.UUID(task_id))
        if task is None:
            return

        # Persist error category on the failed task
        task.error_category = error_category
        session.commit()

        max_retries = config_dict.get("retry_attempts", 3)
        base_delay = config_dict.get("retry_delay_seconds", 2)

        decision = should_retry_task(
            error_category=error_category,
            retry_count=task.retry_count or 0,
            max_retries=max_retries,
            base_delay=base_delay,
            retry_after_seconds=retry_after_seconds,
        )

        if not decision.should_retry:
            logger.info(
                "No auto-retry for task %s: %s", task_id, decision.reason
            )
            return

        # Determine queue from account tier
        account = session.get(Account, task.account_id)
        tier = account.tier if account else "free"
        queue = f"tasks:{tier}"

        # Create retry task row (flat chain)
        new_task_id = uuid.uuid4()
        new_task = Task(
            id=new_task_id,
            account_id=task.account_id,
            status="queued",
            url=task.url,
            task_description=task.task_description,
            output_schema=task.output_schema,
            webhook_url=task.webhook_url,
            max_cost_cents=task.max_cost_cents,
            session_id=task.session_id,
            retry_count=(task.retry_count or 0) + 1,
            retry_of_task_id=task.retry_of_task_id or task.id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(new_task)
        session.commit()

        # Enqueue with backoff delay
        celery_app.send_task(
            "computeruse.execute_task",
            args=[str(new_task_id), json.dumps(config_dict)],
            queue=queue,
            task_id=str(new_task_id),
            countdown=decision.delay_seconds,
        )

        logger.info(
            "Auto-retry task %s -> %s (attempt %d, delay %ds: %s)",
            task_id,
            str(new_task_id),
            new_task.retry_count,
            decision.delay_seconds,
            decision.reason,
        )
    except Exception:
        session.rollback()
        logger.exception("Failed to auto-retry task %s", task_id)
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
