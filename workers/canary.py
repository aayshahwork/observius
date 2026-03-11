"""
workers/canary.py — Canary deployment metrics and evaluation.

Maintains rolling baseline metrics from the production fleet and compares
canary instance metrics against them.  Auto-rollback triggers are exported
as Prometheus gauges for Grafana alerting.

Rollback triggers:
- Success rate drops > 10 percentage points below baseline.
- P99 latency > 2x baseline.
- Mean step cost > 1.5x baseline.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import List

from prometheus_client import Gauge

from workers.config import worker_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

MIN_SAMPLES_FOR_EVALUATION = 10


@dataclass
class TaskObservation:
    """A single task completion observation."""

    timestamp: float  # time.monotonic()
    duration_seconds: float
    success: bool
    cost_cents: float
    steps: int


@dataclass
class BaselineWindow:
    """Aggregated metrics over a rolling window."""

    task_success_rate: float = 1.0
    p50_latency_seconds: float = 0.0
    p95_latency_seconds: float = 0.0
    p99_latency_seconds: float = 0.0
    mean_step_cost_cents: float = 0.0
    sample_count: int = 0


@dataclass
class CanaryVerdict:
    """Result of comparing canary metrics to baseline."""

    healthy: bool
    success_rate_delta: float  # negative = canary is worse
    p99_ratio: float  # canary_p99 / baseline_p99
    cost_ratio: float  # canary_cost / baseline_cost
    reason: str = ""


# ---------------------------------------------------------------------------
# BaselineCalculator
# ---------------------------------------------------------------------------


class BaselineCalculator:
    """Maintains a rolling window of task observations.

    Thread-safe.  Expired observations are evicted on each ``record()``
    and ``snapshot()`` call.
    """

    def __init__(self, window_seconds: int = 86_400) -> None:
        self._window_seconds = window_seconds
        self._observations: deque[TaskObservation] = deque()
        self._lock = threading.Lock()

    def record(
        self,
        duration_seconds: float,
        success: bool,
        cost_cents: float,
        steps: int,
    ) -> None:
        """Record a completed task observation."""
        now = time.monotonic()
        obs = TaskObservation(
            timestamp=now,
            duration_seconds=duration_seconds,
            success=success,
            cost_cents=cost_cents,
            steps=steps,
        )
        with self._lock:
            self._observations.append(obs)
            self._evict(now)

    def snapshot(self) -> BaselineWindow:
        """Return aggregated metrics over the current window."""
        with self._lock:
            self._evict(time.monotonic())
            obs_list = list(self._observations)

        if not obs_list:
            return BaselineWindow()

        count = len(obs_list)
        successes = sum(1 for o in obs_list if o.success)
        durations = sorted(o.duration_seconds for o in obs_list)
        total_cost = sum(o.cost_cents for o in obs_list)
        total_steps = sum(o.steps for o in obs_list)

        return BaselineWindow(
            task_success_rate=successes / count,
            p50_latency_seconds=_percentile(durations, 50),
            p95_latency_seconds=_percentile(durations, 95),
            p99_latency_seconds=_percentile(durations, 99),
            mean_step_cost_cents=total_cost / total_steps if total_steps else 0.0,
            sample_count=count,
        )

    def _evict(self, now: float) -> None:
        """Remove observations older than the window."""
        cutoff = now - self._window_seconds
        while self._observations and self._observations[0].timestamp < cutoff:
            self._observations.popleft()


# ---------------------------------------------------------------------------
# CanaryEvaluator
# ---------------------------------------------------------------------------


ROLLBACK_SUCCESS_RATE_DROP = 0.10  # 10 percentage points
ROLLBACK_P99_RATIO = 2.0  # 2x baseline
ROLLBACK_COST_RATIO = 1.5  # 1.5x baseline


class CanaryEvaluator:
    """Compares canary metrics against a production baseline."""

    def __init__(self, baseline: BaselineCalculator) -> None:
        self._baseline = baseline
        # Canary uses a shorter 5-minute window
        self._canary = BaselineCalculator(window_seconds=300)

    def record_canary(
        self,
        duration_seconds: float,
        success: bool,
        cost_cents: float,
        steps: int,
    ) -> None:
        """Record a canary task observation."""
        self._canary.record(duration_seconds, success, cost_cents, steps)

    def evaluate(self) -> CanaryVerdict:
        """Compare canary to baseline and return a verdict."""
        baseline = self._baseline.snapshot()
        canary = self._canary.snapshot()

        # Not enough data to evaluate
        if canary.sample_count < MIN_SAMPLES_FOR_EVALUATION:
            return CanaryVerdict(
                healthy=True,
                success_rate_delta=0.0,
                p99_ratio=0.0,
                cost_ratio=0.0,
                reason="insufficient canary samples",
            )

        if baseline.sample_count < MIN_SAMPLES_FOR_EVALUATION:
            return CanaryVerdict(
                healthy=True,
                success_rate_delta=0.0,
                p99_ratio=0.0,
                cost_ratio=0.0,
                reason="insufficient baseline samples",
            )

        # Compute deltas
        success_rate_delta = canary.task_success_rate - baseline.task_success_rate
        p99_ratio = (
            canary.p99_latency_seconds / baseline.p99_latency_seconds
            if baseline.p99_latency_seconds > 0
            else 0.0
        )
        cost_ratio = (
            canary.mean_step_cost_cents / baseline.mean_step_cost_cents
            if baseline.mean_step_cost_cents > 0
            else 0.0
        )

        # Check rollback triggers
        reasons: List[str] = []
        if success_rate_delta < -ROLLBACK_SUCCESS_RATE_DROP:
            reasons.append(
                f"success rate dropped {abs(success_rate_delta):.1%} "
                f"(baseline={baseline.task_success_rate:.1%}, canary={canary.task_success_rate:.1%})"
            )
        if p99_ratio > ROLLBACK_P99_RATIO:
            reasons.append(
                f"P99 latency {p99_ratio:.1f}x baseline "
                f"({canary.p99_latency_seconds:.1f}s vs {baseline.p99_latency_seconds:.1f}s)"
            )
        if cost_ratio > ROLLBACK_COST_RATIO:
            reasons.append(
                f"cost {cost_ratio:.1f}x baseline "
                f"({canary.mean_step_cost_cents:.2f}c vs {baseline.mean_step_cost_cents:.2f}c)"
            )

        healthy = len(reasons) == 0
        return CanaryVerdict(
            healthy=healthy,
            success_rate_delta=success_rate_delta,
            p99_ratio=p99_ratio,
            cost_ratio=cost_ratio,
            reason="; ".join(reasons) if reasons else "all checks passed",
        )


# ---------------------------------------------------------------------------
# Prometheus gauges
# ---------------------------------------------------------------------------

canary_healthy = Gauge(
    "canary_healthy",
    "1 if canary passes all checks, 0 if rollback recommended",
    multiprocess_mode="liveall",
)

canary_success_rate_delta = Gauge(
    "canary_success_rate_delta",
    "Canary success rate minus baseline (negative = worse)",
    multiprocess_mode="liveall",
)

canary_p99_ratio = Gauge(
    "canary_p99_ratio",
    "Canary P99 latency divided by baseline P99",
    multiprocess_mode="liveall",
)

canary_cost_ratio = Gauge(
    "canary_cost_ratio",
    "Canary mean step cost divided by baseline mean step cost",
    multiprocess_mode="liveall",
)

baseline_success_rate = Gauge(
    "baseline_success_rate",
    "Rolling 24h production success rate",
    multiprocess_mode="liveall",
)

baseline_p99_latency = Gauge(
    "baseline_p99_latency_seconds",
    "Rolling 24h P99 latency in seconds",
    multiprocess_mode="liveall",
)


# ---------------------------------------------------------------------------
# Module-level instances
# ---------------------------------------------------------------------------

_baseline_calculator = BaselineCalculator(window_seconds=86_400)
_canary_evaluator = CanaryEvaluator(baseline=_baseline_calculator)


def record_and_evaluate(
    duration_seconds: float,
    success: bool,
    cost_cents: float,
    steps: int,
) -> None:
    """Record a task observation and evaluate canary health.

    Called from workers/metrics.py signal handlers.  Runs inline in each
    child process (prefork), so each child evaluates its own data and
    exports to Prometheus gauges (which are aggregated across processes
    by the multiprocess collector).
    """
    if worker_settings.CANARY_DEPLOYMENT:
        _canary_evaluator.record_canary(duration_seconds, success, cost_cents, steps)
    else:
        _baseline_calculator.record(duration_seconds, success, cost_cents, steps)

    # Evaluate canary health and export to Prometheus gauges
    try:
        verdict = _canary_evaluator.evaluate()
        canary_healthy.set(1.0 if verdict.healthy else 0.0)
        canary_success_rate_delta.set(verdict.success_rate_delta)
        canary_p99_ratio.set(verdict.p99_ratio)
        canary_cost_ratio.set(verdict.cost_ratio)

        snap = _baseline_calculator.snapshot()
        baseline_success_rate.set(snap.task_success_rate)
        baseline_p99_latency.set(snap.p99_latency_seconds)

        if not verdict.healthy:
            logger.warning("Canary unhealthy: %s", verdict.reason)
    except Exception:
        logger.exception("Canary evaluation error")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Compute a percentile from a pre-sorted list."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    rank = (pct / 100.0) * (n - 1)
    lower = int(math.floor(rank))
    upper = min(lower + 1, n - 1)
    frac = rank - lower
    return sorted_values[lower] + frac * (sorted_values[upper] - sorted_values[lower])
