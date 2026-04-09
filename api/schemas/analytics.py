"""
api/schemas/analytics.py — Pydantic v2 models for the Analytics API.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ErrorCategoryCount(BaseModel):
    category: str
    count: int


class FailingUrl(BaseModel):
    url: str
    failure_count: int
    last_failure: datetime


class HourlyBucket(BaseModel):
    hour: datetime
    completed: int
    failed: int
    cost_cents: float


class ExecutorStats(BaseModel):
    count: int
    success_rate: float
    avg_cost: float


class ExecutorBreakdown(BaseModel):
    browser_use: ExecutorStats
    native: ExecutorStats
    sdk: ExecutorStats


class RetryStats(BaseModel):
    total_retried: int
    retry_success_rate: float
    avg_attempts: float
    category_counts: dict[str, int] | None = None
    total_diagnosis_cost_cents: float | None = None


class AlertSummary(BaseModel):
    id: uuid.UUID
    alert_type: str
    message: str
    created_at: datetime


class HealthAnalyticsResponse(BaseModel):
    period: str
    total_runs: int
    completed: int
    failed: int
    timeout: int
    success_rate: float
    success_rate_trend: float
    total_cost_cents: float
    avg_cost_per_run: float
    total_tokens: int
    avg_duration_ms: int
    top_errors: list[ErrorCategoryCount]
    top_failing_urls: list[FailingUrl]
    hourly_breakdown: list[HourlyBucket]
    executor_breakdown: ExecutorBreakdown
    retry_stats: RetryStats
    alerts: list[AlertSummary]
