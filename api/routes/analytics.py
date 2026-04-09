"""
api/routes/analytics.py — Fleet health analytics endpoint.

GET /api/v1/analytics/health   Pre-computed aggregates for fleet health monitoring
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.middleware.auth import get_current_account
from api.models.account import Account
from api.schemas.analytics import (
    AlertSummary,
    ErrorCategoryCount,
    ExecutorBreakdown,
    ExecutorStats,
    FailingUrl,
    HealthAnalyticsResponse,
    HourlyBucket,
    RetryStats,
)

logger = structlog.get_logger("api.analytics")

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

# Period → (timedelta, date_trunc bucket).
# bucket values are hardcoded strings — safe for SQL interpolation.
PERIOD_CONFIG: dict[str, tuple[timedelta, str]] = {
    "1h": (timedelta(hours=1), "minute"),
    "6h": (timedelta(hours=6), "hour"),
    "24h": (timedelta(hours=24), "hour"),
    "7d": (timedelta(days=7), "day"),
    "30d": (timedelta(days=30), "day"),
    "90d": (timedelta(days=90), "day"),
}


class ReliabilityResponse(BaseModel):
    success_rate: float
    repair_success_rate: float
    failure_distribution: dict[str, int]
    repair_distribution: dict[str, dict[str, Any]]
    circuit_breaker_trips: int
    avg_repairs_per_task: float
    top_failing_domains: list[dict[str, Any]]


@router.get("/health", response_model=HealthAnalyticsResponse)
async def get_health_analytics(
    period: Literal["1h", "6h", "24h", "7d", "30d"] = Query(default="24h"),
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> HealthAnalyticsResponse:
    """Return pre-computed fleet health aggregates for the given period."""
    delta, bucket = PERIOD_CONFIG[period]
    assert bucket in ("minute", "hour", "day"), f"invalid bucket: {bucket!r}"
    now = datetime.now(timezone.utc)
    cutoff = now - delta
    prev_cutoff = cutoff - delta
    params: dict = {
        "account_id": account.id,
        "cutoff": cutoff,
        "prev_cutoff": prev_cutoff,
    }

    # 1. Main aggregates --------------------------------------------------
    main = (
        await db.execute(
            text("""
                SELECT
                    COUNT(*)                                              AS total_runs,
                    COUNT(*) FILTER (WHERE status = 'completed')          AS completed,
                    COUNT(*) FILTER (WHERE status = 'failed')             AS failed,
                    COUNT(*) FILTER (WHERE status = 'timeout')            AS timeout,
                    COALESCE(SUM(cost_cents)::float, 0)                   AS total_cost_cents,
                    COALESCE(AVG(cost_cents)::float, 0)                   AS avg_cost_per_run,
                    COALESCE(SUM(COALESCE(total_tokens_in, 0)
                               + COALESCE(total_tokens_out, 0)), 0)::bigint AS total_tokens,
                    COALESCE(AVG(duration_ms)::int, 0)                    AS avg_duration_ms
                FROM tasks
                WHERE account_id = :account_id AND created_at >= :cutoff
            """),
            params,
        )
    ).mappings().one()

    # 2. Previous-period success rate (for trend) -------------------------
    prev = (
        await db.execute(
            text("""
                SELECT
                    COUNT(*)                                     AS total,
                    COUNT(*) FILTER (WHERE status = 'completed') AS completed
                FROM tasks
                WHERE account_id = :account_id
                  AND created_at >= :prev_cutoff
                  AND created_at <  :cutoff
            """),
            params,
        )
    ).mappings().one()

    current_rate = main["completed"] / main["total_runs"] if main["total_runs"] else 0.0
    prev_rate = prev["completed"] / prev["total"] if prev["total"] else 0.0
    trend = round(current_rate - prev_rate, 4) if prev["total"] else 0.0

    # 3. Top error categories ---------------------------------------------
    top_errors = [
        ErrorCategoryCount(category=r.category, count=r.count)
        for r in await db.execute(
            text("""
                SELECT error_category AS category, COUNT(*) AS count
                FROM tasks
                WHERE account_id = :account_id
                  AND created_at >= :cutoff
                  AND error_category IS NOT NULL
                GROUP BY error_category
                ORDER BY count DESC
                LIMIT 10
            """),
            params,
        )
    ]

    # 4. Top failing URLs -------------------------------------------------
    top_failing_urls = [
        FailingUrl(url=r.url, failure_count=r.failure_count, last_failure=r.last_failure)
        for r in await db.execute(
            text("""
                SELECT url,
                       COUNT(*)        AS failure_count,
                       MAX(created_at) AS last_failure
                FROM tasks
                WHERE account_id = :account_id
                  AND created_at >= :cutoff
                  AND status IN ('failed', 'timeout')
                  AND url IS NOT NULL AND url != ''
                GROUP BY url
                ORDER BY failure_count DESC
                LIMIT 10
            """),
            params,
        )
    ]

    # 5. Time-series breakdown --------------------------------------------
    # bucket comes from PERIOD_CONFIG (hardcoded) — safe for interpolation.
    hourly_breakdown = [
        HourlyBucket(
            hour=r.bucket_ts,
            completed=r.completed,
            failed=r.failed,
            cost_cents=round(r.cost_cents, 4),
        )
        for r in await db.execute(
            text(f"""
                SELECT
                    date_trunc('{bucket}', created_at AT TIME ZONE 'UTC') AS bucket_ts,
                    COUNT(*) FILTER (WHERE status = 'completed')          AS completed,
                    COUNT(*) FILTER (WHERE status = 'failed')             AS failed,
                    COALESCE(SUM(cost_cents)::float, 0)                   AS cost_cents
                FROM tasks
                WHERE account_id = :account_id AND created_at >= :cutoff
                GROUP BY bucket_ts
                ORDER BY bucket_ts
            """),
            params,
        )
    ]

    # 6. Executor breakdown -----------------------------------------------
    exec_map: dict[str, ExecutorStats] = {}
    for r in await db.execute(
        text("""
            SELECT
                COALESCE(executor_mode, 'browser_use')                      AS mode,
                COUNT(*)                                                    AS count,
                COUNT(*) FILTER (WHERE status = 'completed')::float
                    / NULLIF(COUNT(*), 0)                                   AS success_rate,
                COALESCE(AVG(cost_cents)::float, 0)                         AS avg_cost
            FROM tasks
            WHERE account_id = :account_id AND created_at >= :cutoff
            GROUP BY mode
        """),
        params,
    ):
        exec_map[r.mode] = ExecutorStats(
            count=r.count,
            success_rate=round(r.success_rate or 0.0, 4),
            avg_cost=round(r.avg_cost, 4),
        )
    empty = ExecutorStats(count=0, success_rate=0.0, avg_cost=0.0)
    executor_breakdown = ExecutorBreakdown(
        browser_use=exec_map.get("browser_use", empty),
        native=exec_map.get("native", empty),
        sdk=exec_map.get("sdk", empty),
    )

    # 7. Retry stats ------------------------------------------------------
    retry_row = (
        await db.execute(
            text("""
                SELECT
                    COUNT(*)                                          AS total_retried,
                    COUNT(*) FILTER (WHERE status = 'completed')::float
                        / NULLIF(COUNT(*), 0)                         AS retry_success_rate,
                    COALESCE(AVG(retry_count)::float, 0)              AS avg_attempts
                FROM tasks
                WHERE account_id = :account_id
                  AND created_at >= :cutoff
                  AND retry_of_task_id IS NOT NULL
            """),
            params,
        )
    ).mappings().one()

    # 7b. Category breakdown + diagnosis cost from analysis_json ----------
    category_counts: dict[str, int] = {}
    total_diagnosis_cost_cents: float = 0.0
    for r in await db.execute(
        text("""
            SELECT
                error_category,
                analysis_json -> 'wasted_cost_cents' AS diag_cost
            FROM tasks
            WHERE account_id = :account_id
              AND created_at >= :cutoff
              AND error_category IS NOT NULL
        """),
        params,
    ):
        cat = r.error_category
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1
        if r.diag_cost is not None:
            try:
                total_diagnosis_cost_cents += float(r.diag_cost)
            except (TypeError, ValueError):
                pass

    retry_stats = RetryStats(
        total_retried=retry_row["total_retried"],
        retry_success_rate=round(retry_row["retry_success_rate"] or 0.0, 4),
        avg_attempts=round(retry_row["avg_attempts"], 2),
        category_counts=category_counts or None,
        total_diagnosis_cost_cents=round(total_diagnosis_cost_cents, 4) if total_diagnosis_cost_cents else None,
    )

    # 8. Alerts — graceful degradation if table doesn't exist -------------
    # Only catch ProgrammingError (covers "relation does not exist" / pgcode 42P01).
    # Other exceptions (connection errors, data errors) should propagate.
    alerts: list[AlertSummary] = []
    try:
        alerts = [
            AlertSummary(
                id=r.id,
                alert_type=r.alert_type,
                message=r.message,
                created_at=r.created_at,
            )
            for r in await db.execute(
                text("""
                    SELECT id, alert_type, message, created_at
                    FROM alerts
                    WHERE account_id = :account_id AND acknowledged = false
                    ORDER BY created_at DESC
                    LIMIT 10
                """),
                {"account_id": account.id},
            )
        ]
    except ProgrammingError:
        logger.debug("alerts_table_unavailable", account_id=str(account.id))

    return HealthAnalyticsResponse(
        period=period,
        total_runs=main["total_runs"],
        completed=main["completed"],
        failed=main["failed"],
        timeout=main["timeout"],
        success_rate=round(current_rate, 4),
        success_rate_trend=trend,
        total_cost_cents=round(main["total_cost_cents"], 4),
        avg_cost_per_run=round(main["avg_cost_per_run"], 4),
        total_tokens=main["total_tokens"],
        avg_duration_ms=main["avg_duration_ms"],
        top_errors=top_errors,
        top_failing_urls=top_failing_urls,
        hourly_breakdown=hourly_breakdown,
        executor_breakdown=executor_breakdown,
        retry_stats=retry_stats,
        alerts=alerts,
    )


@router.get("/reliability", response_model=ReliabilityResponse)
async def get_reliability_analytics(
    period: Literal["1h", "6h", "24h", "7d", "30d", "90d"] = Query(default="7d"),
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> ReliabilityResponse:
    """Return reliability metrics: failure distribution, repair effectiveness, circuit breaker trips."""
    delta, _ = PERIOD_CONFIG[period]
    now = datetime.now(timezone.utc)
    cutoff = now - delta
    params: dict = {"account_id": account.id, "cutoff": cutoff}

    # 1. Overall success rate
    totals = (
        await db.execute(
            text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE success = true) AS succeeded
                FROM tasks
                WHERE account_id = :account_id AND created_at >= :cutoff
            """),
            params,
        )
    ).mappings().one()
    success_rate = round(totals["succeeded"] / totals["total"], 4) if totals["total"] else 0.0

    # 2. Repair success rate (tasks that had at least one patch, what % succeeded)
    repair_tasks = (
        await db.execute(
            text("""
                SELECT
                    COUNT(DISTINCT t.id) AS total_with_repairs,
                    COUNT(DISTINCT t.id) FILTER (WHERE t.success = true) AS succeeded_with_repairs
                FROM tasks t
                WHERE t.account_id = :account_id
                  AND t.created_at >= :cutoff
                  AND EXISTS (
                      SELECT 1 FROM task_steps ts
                      WHERE ts.task_id = t.id AND ts.patch_applied IS NOT NULL
                  )
            """),
            params,
        )
    ).mappings().one()
    repair_success_rate = (
        round(repair_tasks["succeeded_with_repairs"] / repair_tasks["total_with_repairs"], 4)
        if repair_tasks["total_with_repairs"]
        else 0.0
    )

    # 3. Failure distribution by failure_class
    failure_rows = await db.execute(
        text("""
            SELECT ts.failure_class, COUNT(*) AS cnt
            FROM task_steps ts
            JOIN tasks t ON t.id = ts.task_id
            WHERE t.account_id = :account_id
              AND t.created_at >= :cutoff
              AND ts.failure_class IS NOT NULL
            GROUP BY ts.failure_class
            ORDER BY cnt DESC
            LIMIT 20
        """),
        params,
    )
    failure_distribution: dict[str, int] = {r.failure_class: r.cnt for r in failure_rows}

    # 4. Repair distribution by action
    repair_rows = await db.execute(
        text("""
            SELECT
                ts.patch_applied->>'action' AS action,
                COUNT(*) AS attempts,
                COUNT(*) FILTER (WHERE (ts.patch_applied->>'success')::boolean = true) AS successes
            FROM task_steps ts
            JOIN tasks t ON t.id = ts.task_id
            WHERE t.account_id = :account_id
              AND t.created_at >= :cutoff
              AND ts.patch_applied IS NOT NULL
              AND ts.patch_applied->>'action' IS NOT NULL
            GROUP BY action
            ORDER BY attempts DESC
            LIMIT 20
        """),
        params,
    )
    repair_distribution: dict[str, dict[str, Any]] = {
        r.action: {"attempts": r.attempts, "successes": r.successes}
        for r in repair_rows
    }

    # 5. Circuit breaker trips (tasks that failed with non-empty failure_counts)
    cb_row = (
        await db.execute(
            text("""
                SELECT COUNT(*) AS trips
                FROM tasks
                WHERE account_id = :account_id
                  AND created_at >= :cutoff
                  AND status = 'failed'
                  AND failure_counts IS NOT NULL
                  AND failure_counts != '{}'::jsonb
            """),
            params,
        )
    ).mappings().one()
    circuit_breaker_trips = cb_row["trips"]

    # 6. Average repairs per task
    avg_row = (
        await db.execute(
            text("""
                SELECT COALESCE(AVG(repair_count), 0) AS avg_repairs
                FROM (
                    SELECT t.id, COUNT(ts.id) AS repair_count
                    FROM tasks t
                    LEFT JOIN task_steps ts ON ts.task_id = t.id AND ts.patch_applied IS NOT NULL
                    WHERE t.account_id = :account_id AND t.created_at >= :cutoff
                    GROUP BY t.id
                ) sub
            """),
            params,
        )
    ).mappings().one()
    avg_repairs_per_task = round(float(avg_row["avg_repairs"]), 2)

    # 7. Top failing domains
    domain_rows = await db.execute(
        text("""
            SELECT
                split_part(regexp_replace(url, '^https?://', ''), '/', 1) AS domain,
                COUNT(*) AS failure_count,
                MODE() WITHIN GROUP (ORDER BY ts.failure_class) AS top_failure
            FROM tasks t
            JOIN task_steps ts ON ts.task_id = t.id
            WHERE t.account_id = :account_id
              AND t.created_at >= :cutoff
              AND t.status IN ('failed', 'timeout')
              AND ts.failure_class IS NOT NULL
              AND t.url IS NOT NULL AND t.url != ''
            GROUP BY domain
            ORDER BY failure_count DESC
            LIMIT 10
        """),
        params,
    )
    top_failing_domains = [
        {"domain": r.domain or "unknown", "failure_count": r.failure_count, "top_failure": r.top_failure or "unknown"}
        for r in domain_rows
    ]

    return ReliabilityResponse(
        success_rate=success_rate,
        repair_success_rate=repair_success_rate,
        failure_distribution=failure_distribution,
        repair_distribution=repair_distribution,
        circuit_breaker_trips=circuit_breaker_trips,
        avg_repairs_per_task=avg_repairs_per_task,
        top_failing_domains=top_failing_domains,
    )
