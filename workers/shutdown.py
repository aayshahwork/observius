"""
workers/shutdown.py — Graceful shutdown handler for Celery workers.

On SIGTERM (via Celery's worker_shutting_down signal):
1. Stop accepting new tasks.
2. Set Redis shutdown flag so child processes can detect shutdown.
3. Wait up to GRACE_PERIOD_SECONDS for in-flight tasks to finish.
4. Save partial state and requeue any unfinished tasks.
5. Release browsers and Redis locks.
6. Exit cleanly.

Cross-process signaling:
    Celery prefork forks child processes, so threading.Event is process-local.
    We use a Redis key as a cross-process shutdown flag.  The main process
    sets it on SIGTERM; children poll it via ``is_shutting_down()``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, List

from celery import signals
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (process-local in prefork model — each child has its own)
# ---------------------------------------------------------------------------

GRACE_PERIOD_SECONDS = 30
_SHUTDOWN_REDIS_KEY = "worker_shutdown_flag"
_SHUTDOWN_POLL_INTERVAL = 3.0  # seconds between Redis polls

_shutting_down = threading.Event()
_in_flight: dict[str, InFlightTask] = {}
_lock = threading.Lock()

# Cached Redis check state for is_shutting_down()
_last_redis_check: float = 0.0
_redis_shutdown_cached: bool = False


@dataclass
class InFlightTask:
    """Tracks an in-flight task for graceful shutdown cleanup."""

    task_id: str
    lock: Any  # redis Lock instance
    browser_manager: Any  # BrowserManager instance or None
    step_data: List[Any]  # list[StepData] accumulated so far
    config_json: str  # original config JSON for potential requeue
    started_at: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Public API (called from workers/tasks.py)
# ---------------------------------------------------------------------------


def register_in_flight(
    task_id: str,
    lock: Any,
    browser_manager: Any,
    step_data: List[Any],
    config_json: str,
) -> None:
    """Register a task as in-flight for graceful shutdown tracking."""
    with _lock:
        _in_flight[task_id] = InFlightTask(
            task_id=task_id,
            lock=lock,
            browser_manager=browser_manager,
            step_data=step_data,
            config_json=config_json,
        )


def deregister_in_flight(task_id: str) -> None:
    """Remove a task from in-flight tracking (normal completion)."""
    with _lock:
        _in_flight.pop(task_id, None)


def is_shutting_down() -> bool:
    """Check whether a graceful shutdown is in progress.

    Uses a hybrid approach:
    - Fast path: check in-memory ``threading.Event`` (set in same process).
    - Slow path: poll Redis key every ``_SHUTDOWN_POLL_INTERVAL`` seconds
      to detect shutdown initiated by the main process (cross-process).
    """
    if _shutting_down.is_set():
        return True

    global _last_redis_check, _redis_shutdown_cached
    if _redis_shutdown_cached:
        return True

    now = time.monotonic()
    if now - _last_redis_check >= _SHUTDOWN_POLL_INTERVAL:
        _last_redis_check = now
        try:
            import redis as redis_lib

            from workers.config import worker_settings

            r = redis_lib.Redis.from_url(worker_settings.REDIS_URL, decode_responses=True)
            if r.exists(_SHUTDOWN_REDIS_KEY):
                _redis_shutdown_cached = True
                _shutting_down.set()
                return True
        except Exception:
            pass

    return False


# ---------------------------------------------------------------------------
# GracefulShutdownHandler
# ---------------------------------------------------------------------------


class GracefulShutdownHandler:
    """Handles graceful worker shutdown with partial-state rescue.

    Connects to Celery's ``worker_shutting_down`` signal (main process)
    and ``worker_process_shutdown`` signal (child processes).
    """

    def __init__(self, grace_period: int = GRACE_PERIOD_SECONDS) -> None:
        self.grace_period = grace_period

    def register(self) -> None:
        """Connect to Celery lifecycle signals. Call once at worker startup."""
        signals.worker_shutting_down.connect(self._on_worker_shutting_down)
        signals.worker_process_shutdown.connect(self._on_worker_process_shutdown)

    # -- Signal handlers ----------------------------------------------------

    def _on_worker_shutting_down(self, sig: str, how: str, exitcode: int, **kwargs: Any) -> None:
        """Main process: set shutdown flag via Redis so children can react."""
        _shutting_down.set()
        logger.info("Shutting down, no new tasks (sig=%s, how=%s)", sig, how)

        # Set Redis flag so forked children can detect shutdown
        try:
            import redis as redis_lib

            from workers.config import worker_settings

            r = redis_lib.Redis.from_url(worker_settings.REDIS_URL, decode_responses=True)
            r.setex(_SHUTDOWN_REDIS_KEY, 120, "1")  # 120s TTL auto-cleanup
        except Exception:
            logger.warning("Failed to set Redis shutdown flag")

    def _on_worker_process_shutdown(self, pid: int, exitcode: int, **kwargs: Any) -> None:
        """Child process: cleanup any remaining in-flight tasks before exit.

        By the time this fires, the task has normally finished and deregistered.
        This is a safety net for any stragglers.
        """
        _shutting_down.set()

        with _lock:
            remaining = list(_in_flight.items())

        if not remaining:
            logger.info("Shutdown: no in-flight tasks in pid=%d", pid)
            return

        # Immediate cleanup — no grace period wait here because the task
        # has either already finished (deregistered) or the process is being
        # forced down.  Waiting would just delay the inevitable.
        for task_id, entry in remaining:
            logger.warning("Shutdown: force-cleaning task %s in pid=%d", task_id, pid)
            try:
                _save_partial_and_requeue(task_id, entry)
            except Exception:
                logger.exception("Shutdown: failed to requeue task %s", task_id)
            try:
                _release_resources(entry)
            except Exception:
                logger.exception("Shutdown: failed to release resources for task %s", task_id)

        with _lock:
            _in_flight.clear()

        logger.info("Shutdown complete: cleaned up %d tasks in pid=%d", len(remaining), pid)


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


def _save_partial_and_requeue(task_id: str, entry: InFlightTask) -> None:
    """Save partial replay and reset task to 'queued' for another worker."""
    from workers.db import get_sync_session

    # Save partial replay if we have enough steps
    if len(entry.step_data) >= 2:
        try:
            import tempfile

            from workers.replay import ReplayGenerator

            replay_gen = ReplayGenerator(
                steps=entry.step_data,
                task_metadata={
                    "task_id": task_id,
                    "duration_ms": int((time.monotonic() - entry.started_at) * 1000),
                    "success": False,
                },
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                replay_path = f"{tmpdir}/{task_id}_partial.html"
                replay_gen.generate(replay_path)
                # Upload is best-effort; skip if R2 is not configured
                from workers.config import is_r2_configured
                if not is_r2_configured():
                    logger.info("R2 not configured, skipping partial replay upload for task %s", task_id)
                else:
                    try:
                        import boto3

                        from workers.config import worker_settings

                        s3_key = f"replays/{task_id}/replay_partial.html"
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
                            ExtraArgs={"ContentType": "text/html"},
                        )
                        logger.info("Partial replay uploaded: %s", s3_key)
                    except Exception:
                        logger.warning("Failed to upload partial replay for task %s", task_id)
        except Exception:
            logger.warning("Failed to generate partial replay for task %s", task_id)

    # Reset task to queued so another worker picks it up
    session = get_sync_session()
    try:
        session.execute(
            text(
                "UPDATE tasks "
                "SET status = 'queued', "
                "    worker_id = NULL, "
                "    started_at = NULL "
                "WHERE id = :task_id::uuid AND status = 'running'"
            ),
            {"task_id": task_id},
        )
        session.commit()
        logger.info("Task %s requeued after graceful shutdown", task_id)
    except Exception:
        session.rollback()
        logger.exception("Failed to requeue task %s", task_id)
    finally:
        session.close()


def _release_resources(entry: InFlightTask) -> None:
    """Release Redis lock and browser resources."""
    # Release Redis lock first (before requeue to avoid contention)
    if entry.lock is not None:
        try:
            entry.lock.release()
            logger.info("Released lock for task %s", entry.task_id)
        except Exception:
            logger.warning("Failed to release lock for task %s", entry.task_id)

    # Release browser resources (async operations)
    if entry.browser_manager is not None:
        try:
            loop = asyncio.new_event_loop()
            try:
                # Release Browserbase cloud session if active
                loop.run_until_complete(
                    entry.browser_manager._release_cloud_session()
                )
                # Stop Playwright process
                if entry.browser_manager._playwright is not None:
                    loop.run_until_complete(
                        entry.browser_manager._playwright.stop()
                    )
                    entry.browser_manager._playwright = None
            finally:
                loop.close()
            logger.info("Released browser resources for task %s", entry.task_id)
        except Exception:
            logger.warning("Failed to release browser for task %s", entry.task_id)
