from celery import Celery

from workers.config import worker_settings

celery_app = Celery(
    "computeruse",
    broker=worker_settings.REDIS_URL,
    backend=worker_settings.REDIS_URL,
)

celery_app.conf.update(
    task_queues={
        "tasks:free": {"exchange": "tasks", "routing_key": "free"},
        "tasks:startup": {"exchange": "tasks", "routing_key": "startup"},
        "tasks:enterprise": {"exchange": "tasks", "routing_key": "enterprise"},
    },
    task_default_queue="tasks:free",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
