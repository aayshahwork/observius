"""
tests/unit/test_analytics.py — Unit tests for the analytics health endpoint.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import ProgrammingError

from api.dependencies import get_db
from api.middleware.auth import get_current_account
from api.routes.analytics import router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_ACCOUNT_ID = uuid.uuid4()


def _fake_account() -> MagicMock:
    acct = MagicMock()
    acct.id = FAKE_ACCOUNT_ID
    return acct


class _MockResult:
    """Simulate SQLAlchemy execute() result with both mapping and row access."""

    def __init__(self, rows=None, mapping=None):
        self._rows = rows or []
        self._mapping = mapping

    def mappings(self):
        return self

    def one(self):
        return self._mapping

    def __iter__(self):
        return iter(self._rows)


class _Row:
    """Row with attribute access."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _build_app(mock_db: AsyncMock) -> FastAPI:
    """Create a test app with dependency overrides."""
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_current_account] = _fake_account
    test_app.dependency_overrides[get_db] = lambda: mock_db
    return test_app


def _standard_mock_db(
    main: dict,
    prev: dict | None = None,
    retry: dict | None = None,
    error_rows: list | None = None,
    url_rows: list | None = None,
    hourly_rows: list | None = None,
    exec_rows: list | None = None,
    alerts_error: bool = False,
    alert_rows: list | None = None,
) -> AsyncMock:
    """Build a mock db whose execute() returns predictable results per call."""
    if prev is None:
        prev = {"total": 0, "completed": 0}
    if retry is None:
        retry = {"total_retried": 0, "retry_success_rate": None, "avg_attempts": 0.0}

    call_count = 0

    async def mock_execute(query, params=None):
        nonlocal call_count
        call_count += 1
        # Call order: 1=main, 2=prev, 3=errors, 4=urls, 5=hourly, 6=exec,
        #             7=retry, 8=category+diag_cost (7b in route), 9=alerts
        if call_count == 1:
            return _MockResult(mapping=main)
        if call_count == 2:
            return _MockResult(mapping=prev)
        if call_count == 3:
            return _MockResult(rows=error_rows or [])
        if call_count == 4:
            return _MockResult(rows=url_rows or [])
        if call_count == 5:
            return _MockResult(rows=hourly_rows or [])
        if call_count == 6:
            return _MockResult(rows=exec_rows or [])
        if call_count == 7:
            return _MockResult(mapping=retry)
        if call_count == 8:  # category breakdown + diagnosis cost (query 7b)
            return _MockResult(rows=[])
        # 9 = alerts
        if alerts_error:
            raise ProgrammingError(
                "SELECT", {}, Exception('relation "alerts" does not exist')
            )
        return _MockResult(rows=alert_rows or [])

    db = AsyncMock()
    db.execute = mock_execute
    return db


ZERO_MAIN = {
    "total_runs": 0,
    "completed": 0,
    "failed": 0,
    "timeout": 0,
    "total_cost_cents": 0.0,
    "avg_cost_per_run": 0.0,
    "total_tokens": 0,
    "avg_duration_ms": 0,
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_invalid_period_returns_422():
    """Periods outside the allowed set should return 422."""
    db = _standard_mock_db(ZERO_MAIN)
    app = _build_app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/analytics/health", params={"period": "2h"})
    assert resp.status_code == 422


async def test_empty_data_returns_zeros():
    """When no tasks exist, all metrics should be zero (no division by zero)."""
    db = _standard_mock_db(ZERO_MAIN)
    app = _build_app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/analytics/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_runs"] == 0
    assert data["success_rate"] == 0.0
    assert data["success_rate_trend"] == 0.0
    assert data["top_errors"] == []
    assert data["top_failing_urls"] == []
    assert data["hourly_breakdown"] == []
    assert data["retry_stats"]["total_retried"] == 0


async def test_trend_computation():
    """success_rate_trend = current_rate - previous_rate."""
    main = {**ZERO_MAIN, "total_runs": 10, "completed": 9, "failed": 1,
            "total_cost_cents": 50.0, "avg_cost_per_run": 5.0,
            "total_tokens": 10000, "avg_duration_ms": 30000}
    prev = {"total": 20, "completed": 19}  # 0.95

    db = _standard_mock_db(main, prev=prev)
    app = _build_app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/analytics/health")
    data = resp.json()
    assert data["success_rate"] == pytest.approx(0.9, abs=1e-4)
    assert data["success_rate_trend"] == pytest.approx(-0.05, abs=1e-4)  # 0.9 - 0.95


async def test_alerts_graceful_degradation():
    """If the alerts table doesn't exist, alerts should be an empty list."""
    main = {**ZERO_MAIN, "total_runs": 1, "completed": 1,
            "total_cost_cents": 1.0, "avg_cost_per_run": 1.0,
            "total_tokens": 100, "avg_duration_ms": 5000}
    db = _standard_mock_db(main, alerts_error=True)
    app = _build_app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/analytics/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["alerts"] == []
    assert data["total_runs"] == 1


async def test_response_includes_all_fields():
    """Verify the response shape contains all expected top-level keys."""
    main = {**ZERO_MAIN, "total_runs": 5, "completed": 3, "failed": 1, "timeout": 1,
            "total_cost_cents": 25.0, "avg_cost_per_run": 5.0,
            "total_tokens": 5000, "avg_duration_ms": 20000}
    prev = {"total": 5, "completed": 4}
    retry = {"total_retried": 1, "retry_success_rate": 1.0, "avg_attempts": 2.0}

    db = _standard_mock_db(main, prev=prev, retry=retry)
    app = _build_app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/analytics/health", params={"period": "7d"})

    assert resp.status_code == 200
    data = resp.json()

    expected_keys = {
        "period", "total_runs", "completed", "failed", "timeout",
        "success_rate", "success_rate_trend", "total_cost_cents",
        "avg_cost_per_run", "total_tokens", "avg_duration_ms",
        "top_errors", "top_failing_urls", "hourly_breakdown",
        "executor_breakdown", "retry_stats", "alerts",
    }
    assert set(data.keys()) == expected_keys
    assert data["period"] == "7d"
    assert data["executor_breakdown"]["browser_use"]["count"] == 0
    assert data["retry_stats"]["total_retried"] == 1


async def test_executor_breakdown_with_data():
    """Verify executor breakdown populates from query results."""
    main = {**ZERO_MAIN, "total_runs": 10, "completed": 8, "failed": 2,
            "total_cost_cents": 100.0, "avg_cost_per_run": 10.0,
            "total_tokens": 50000, "avg_duration_ms": 40000}
    exec_rows = [
        _Row(mode="browser_use", count=7, success_rate=0.857, avg_cost=9.5),
        _Row(mode="sdk", count=3, success_rate=0.667, avg_cost=11.2),
    ]
    db = _standard_mock_db(main, exec_rows=exec_rows)
    app = _build_app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/analytics/health")

    data = resp.json()
    assert data["executor_breakdown"]["browser_use"]["count"] == 7
    assert data["executor_breakdown"]["sdk"]["count"] == 3
    assert data["executor_breakdown"]["native"]["count"] == 0  # not in results → empty
