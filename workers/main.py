"""
workers/main.py — Celery application entrypoint.

Start the worker:
    celery -A workers.main worker --loglevel=info --concurrency=4
"""

from workers.tasks import celery_app

# Add tier-based queue routing on top of the base config from tasks.py
celery_app.conf.update(
    task_queues={
        "tasks:free": {"exchange": "tasks", "routing_key": "free"},
        "tasks:startup": {"exchange": "tasks", "routing_key": "startup"},
        "tasks:enterprise": {"exchange": "tasks", "routing_key": "enterprise"},
    },
    task_default_queue="tasks:free",
)

__all__ = ["celery_app"]
