"""
workers/metrics.py — Prometheus metrics for Celery workers.

Collects: task_duration_seconds, task_success_total, task_failure_total,
          queue_depth (gauge), worker_utilization (gauge).

Uses prometheus_client multiprocess mode so that metrics from forked Celery
prefork child processes are aggregated by the HTTP server in the main process.

Starts a background Prometheus HTTP server on METRICS_PORT (default 9090)
when the worker boots.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time

# Multiprocess mode must be configured BEFORE importing metric types.
# Each forked child writes to files in this directory; the HTTP server
# in the main process aggregates them on scrape.
if "PROMETHEUS_MULTIPROC_DIR" not in os.environ:
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = tempfile.mkdtemp()

from celery import signals
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, multiprocess

# ---------------------------------------------------------------------------
# Metrics (written to shared files in multiprocess mode)
# ---------------------------------------------------------------------------

task_duration_seconds = Histogram(
    "celery_task_duration_seconds",
    "Task execution duration in seconds",
    ["task_name"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 900],
)

task_success_total = Counter(
    "celery_task_success_total",
    "Total successful tasks",
    ["task_name"],
)

task_failure_total = Counter(
    "celery_task_failure_total",
    "Total failed tasks",
    ["task_name"],
)

queue_depth = Gauge(
    "celery_queue_depth",
    "Number of messages in queue",
    ["queue"],
    multiprocess_mode="liveall",
)

worker_utilization = Gauge(
    "celery_worker_utilization",
    "Fraction of worker pool slots in use (0-1)",
    multiprocess_mode="liveall",
)

task_cost_cents = Histogram(
    "celery_task_cost_cents",
    "Task cost in cents",
    ["task_name"],
    buckets=[0.1, 0.5, 1, 5, 10, 50, 100, 500],
)

task_tokens_in_total = Counter(
    "celery_task_tokens_in_total",
    "Total input tokens consumed",
    ["task_name"],
)

task_tokens_out_total = Counter(
    "celery_task_tokens_out_total",
    "Total output tokens consumed",
    ["task_name"],
)

# Session management metrics
celery_session_restored_total = Counter(
    "celery_session_restored_total",
    "Sessions restored from encrypted storage",
    ["domain"],
)
celery_session_stale_total = Counter(
    "celery_session_stale_total",
    "Stale sessions detected (login redirect)",
    ["domain"],
)
celery_session_saved_total = Counter(
    "celery_session_saved_total",
    "Sessions saved after successful execution",
    ["domain"],
)
celery_navigation_retry_total = Counter(
    "celery_navigation_retry_total",
    "Navigation retries due to transient errors",
)

# Stuck agent detection metrics
celery_stuck_visual_stagnation_total = Counter(
    "celery_stuck_visual_stagnation_total",
    "Stuck agent: consecutive identical screenshots",
    ["task_name"],
)
celery_stuck_action_repetition_total = Counter(
    "celery_stuck_action_repetition_total",
    "Stuck agent: same action repeated consecutively",
    ["task_name"],
)
celery_stuck_failure_spiral_total = Counter(
    "celery_stuck_failure_spiral_total",
    "Stuck agent: consecutive step failures",
    ["task_name"],
)

# Native executor metrics
celery_native_step_total = Counter(
    "celery_native_step_total",
    "Native executor steps by action type",
    ["task_name", "action_type"],
)
celery_native_llm_retry_total = Counter(
    "celery_native_llm_retry_total",
    "LLM API retries in native executor loop",
    ["task_name"],
)
celery_native_stuck_recovery_total = Counter(
    "celery_native_stuck_recovery_total",
    "Stuck recovery hints injected in native executor",
    ["task_name"],
)
celery_native_context_trim_total = Counter(
    "celery_native_context_trim_total",
    "Context window trims in native executor",
    ["task_name"],
)

# ---------------------------------------------------------------------------
# Task timing bookkeeping
# ---------------------------------------------------------------------------

_task_start_times: dict[str, float] = {}
_task_metadata: dict[str, dict] = {}  # task_id -> {cost_cents, steps}


def record_task_cost(
    task_id: str,
    cost_cents: float,
    steps: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Store task cost/steps/tokens for retrieval in signal handlers.

    Called from ``workers/tasks.py`` after execution completes.
    """
    _task_metadata[task_id] = {
        "cost_cents": cost_cents,
        "steps": steps,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


@signals.task_prerun.connect
def _on_task_prerun(sender=None, task_id=None, **kwargs):  # noqa: ARG001
    _task_start_times[task_id] = time.monotonic()


@signals.task_success.connect
def _on_task_success(sender=None, **kwargs):  # noqa: ARG001
    task_id = sender.request.id
    task_name = sender.name
    start = _task_start_times.pop(task_id, None)
    duration = time.monotonic() - start if start is not None else 0.0
    if start is not None:
        task_duration_seconds.labels(task_name=task_name).observe(duration)
    task_success_total.labels(task_name=task_name).inc()

    # Record cost, tokens, and canary observation
    meta = _task_metadata.pop(task_id, None)
    if meta:
        task_cost_cents.labels(task_name=task_name).observe(meta["cost_cents"])
        task_tokens_in_total.labels(task_name=task_name).inc(meta.get("tokens_in", 0))
        task_tokens_out_total.labels(task_name=task_name).inc(meta.get("tokens_out", 0))
        try:
            from workers.canary import record_and_evaluate

            record_and_evaluate(
                duration_seconds=duration,
                success=True,
                cost_cents=meta["cost_cents"],
                steps=meta["steps"],
            )
        except Exception:
            pass


@signals.task_failure.connect
def _on_task_failure(sender=None, **kwargs):  # noqa: ARG001
    task_id = sender.request.id
    task_name = sender.name
    start = _task_start_times.pop(task_id, None)
    duration = time.monotonic() - start if start is not None else 0.0
    task_failure_total.labels(task_name=task_name).inc()

    # Record canary observation for failures too
    meta = _task_metadata.pop(task_id, None)
    try:
        from workers.canary import record_and_evaluate

        record_and_evaluate(
            duration_seconds=duration,
            success=False,
            cost_cents=meta["cost_cents"] if meta else 0.0,
            steps=meta["steps"] if meta else 0,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Queue depth polling (background thread)
# ---------------------------------------------------------------------------


def _poll_queue_depth(interval: int = 15) -> None:
    """Periodically poll Redis for queue lengths."""
    import redis as redis_lib

    from workers.config import worker_settings

    r = redis_lib.Redis.from_url(worker_settings.REDIS_URL)
    queues = ["tasks:free", "tasks:startup", "tasks:enterprise"]

    while True:
        try:
            for q in queues:
                queue_depth.labels(queue=q).set(r.llen(q))  # type: ignore[arg-type]
        except Exception:
            pass
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Startup: launch metrics server when the Celery worker is ready
# ---------------------------------------------------------------------------


def _generate_latest_multiprocess() -> bytes:
    """Aggregate metrics from all forked worker processes."""
    from prometheus_client import generate_latest

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return generate_latest(registry)


@signals.worker_ready.connect
def _start_metrics_server(sender=None, **kwargs):  # noqa: ARG001
    """Start Prometheus HTTP server with multiprocess-aware registry."""
    from http.server import HTTPServer, BaseHTTPRequestHandler

    from prometheus_client import CONTENT_TYPE_LATEST

    port = int(os.environ.get("METRICS_PORT", "9090"))

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            data = _generate_latest_multiprocess()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args) -> None:  # noqa: A002, ARG002
            pass  # Silence access logs

    server = HTTPServer(("0.0.0.0", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    # Queue depth poller (runs in main process, reads Redis directly)
    t2 = threading.Thread(target=_poll_queue_depth, daemon=True)
    t2.start()

    # Canary evaluation runs inline in signal handlers (per-child process)
    # to avoid cross-process data visibility issues with prefork pool.
