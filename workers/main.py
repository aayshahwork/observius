"""
workers/main.py — Celery application entrypoint.

Start the worker:
    celery -A workers.main worker --loglevel=info --pool=prefork --concurrency=2
"""

from __future__ import annotations

import logging

import structlog
from kombu import Queue

from workers.config import worker_settings
from workers.tasks import celery_app  # Also registers execute_task and deliver_webhook

import workers.metrics  # noqa: F401 — registers Celery signal handlers

# ---------------------------------------------------------------------------
# Tier-based queue routing with visibility timeouts
# ---------------------------------------------------------------------------

celery_app.conf.update(
    task_queues=[
        Queue("tasks:free", routing_key="free"),
        Queue("tasks:startup", routing_key="startup"),
        Queue("tasks:enterprise", routing_key="enterprise"),
    ],
    task_default_queue="tasks:free",
    # Visibility timeout: how long a message can be "invisible" (processing)
    # before Redis re-delivers it. Set to max tier (enterprise 600s + buffer).
    # Per-task time limits enforce the actual per-tier constraints.
    broker_transport_options={
        "visibility_timeout": 900,
    },
    # Worker config: prefork pool with 2 workers per container
    worker_pool="prefork",
    worker_concurrency=2,
)

# ---------------------------------------------------------------------------
# Structured JSON logging for production (Railway log drain)
# ---------------------------------------------------------------------------

if worker_settings.ENVIRONMENT != "development":
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)

__all__ = ["celery_app"]
