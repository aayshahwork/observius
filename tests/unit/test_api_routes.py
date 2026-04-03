"""
tests/unit/test_api_routes.py — Tests for task and session API routes.

Tests:
- Task creation (201), invalid URL (422), too-long task (422)
- Task retrieval (200, 404)
- Rate limiter returns 429 with correct headers
- Idempotency: same key returns same result
- Credential stripping: credentials not in response
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import get_db, get_redis
from api.middleware.auth import get_current_account


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_ACCOUNT_ID = uuid.uuid4()


def _make_account(**overrides):
    defaults = dict(
        id=TEST_ACCOUNT_ID,
        email="test@computeruse.dev",
        name="Test Account",
        tier="free",
        monthly_step_limit=500,
        monthly_steps_used=0,
        encryption_key_id="enc-key-1",
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    acct = MagicMock()
    for k, v in defaults.items():
        setattr(acct, k, v)
    return acct


def _make_task(account_id: uuid.UUID, **overrides):
    defaults = dict(
        id=uuid.uuid4(),
        account_id=account_id,
        status="queued",
        success=False,
        url="https://example.com",
        task_description="Test task",
        output_schema=None,
        result=None,
        error_code=None,
        error_message=None,
        model_used=None,
        total_steps=0,
        duration_ms=None,
        replay_s3_key=None,
        session_id=None,
        idempotency_key=None,
        webhook_url=None,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
        retry_count=0,
        retry_of_task_id=None,
        error_category=None,
        cost_cents=0,
        total_tokens_in=0,
        total_tokens_out=0,
        executor_mode="browser_use",
        analysis_json=None,
    )
    defaults.update(overrides)
    task = MagicMock()
    for k, v in defaults.items():
        setattr(task, k, v)
    return task


@pytest.fixture
def test_account():
    return _make_account()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    return redis


@pytest.fixture
def client(test_account, mock_db, mock_redis):
    async def override_auth():
        return test_account

    async def override_db():
        yield mock_db

    async def override_redis():
        yield mock_redis

    app.dependency_overrides[get_current_account] = override_auth
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = override_redis

    # Patch the Celery send_task and URL validator so tests don't connect to Redis broker or DNS
    from unittest.mock import patch
    with (
        patch("api.routes.tasks._celery") as mock_celery,
        patch("api.routes.tasks.validate_url_async", new_callable=AsyncMock, return_value=("https://example.com", "93.184.216.34")),
        patch("api.routes.tasks.validate_webhook_url", new_callable=AsyncMock, return_value="https://hooks.example.com/cb"),
    ):
        mock_celery.send_task = MagicMock()
        yield TestClient(app)

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_returns_ok(self, client):
        from unittest.mock import patch

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.aclose = AsyncMock()

        with (
            patch("api.db.engine.async_session_factory", return_value=mock_session_ctx),
            patch("redis.asyncio.Redis.from_url", return_value=mock_redis),
        ):
            resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert data["redis"] == "ok"
        assert "version" in data


# ---------------------------------------------------------------------------
# POST /api/v1/tasks
# ---------------------------------------------------------------------------

class TestCreateTask:
    def test_valid_body_returns_201(self, client, mock_db):
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.add = MagicMock()

        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Click the login button",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "queued"
        assert "task_id" in body
        assert body["success"] is False

    def test_invalid_url_returns_422(self, client):
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "not-a-valid-url",
                "task": "Click the login button",
            },
        )
        assert resp.status_code == 422

    def test_too_long_task_returns_422(self, client):
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "x" * 2001,
            },
        )
        assert resp.status_code == 422

    def test_empty_task_returns_422(self, client):
        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "",
            },
        )
        assert resp.status_code == 422

    def test_credentials_not_in_response(self, client, mock_db):
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.add = MagicMock()

        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Log in and scrape data",
                "credentials": {"username": "admin", "password": "secret123"},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        response_text = json.dumps(body)
        assert "secret123" not in response_text
        assert "admin" not in response_text
        assert "credentials" not in body


class TestIdempotency:
    def test_same_key_returns_cached_result(self, client, mock_db, mock_redis):
        cached_response = json.dumps({
            "task_id": str(uuid.uuid4()),
            "status": "queued",
            "success": False,
            "result": None,
            "error": None,
            "replay_url": None,
            "steps": 0,
            "duration_ms": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        })
        mock_redis.get = AsyncMock(return_value=cached_response)

        resp = client.post(
            "/api/v1/tasks",
            json={
                "url": "https://example.com",
                "task": "Do something",
                "idempotency_key": "unique-key-123",
            },
        )
        assert resp.status_code == 201
        mock_db.add.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/{task_id}
# ---------------------------------------------------------------------------

class TestGetTask:
    def test_existing_task_returns_200(self, client, mock_db, test_account):
        task = _make_task(test_account.id)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        mock_db.execute = AsyncMock(return_value=result_mock)

        resp = client.get(f"/api/v1/tasks/{task.id}")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == str(task.id)

    def test_missing_task_returns_404(self, client, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        resp = client.get(f"/api/v1/tasks/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/tasks
# ---------------------------------------------------------------------------

class TestListTasks:
    def test_list_returns_paginated(self, client, mock_db, test_account):
        tasks = [_make_task(test_account.id) for _ in range(3)]

        count_result = MagicMock()
        count_result.scalar.return_value = 3

        tasks_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = tasks
        tasks_result.scalars.return_value = scalars_mock

        mock_db.execute = AsyncMock(side_effect=[count_result, tasks_result])

        resp = client.get("/api/v1/tasks?limit=10&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["tasks"]) == 3
        assert body["has_more"] is False


# ---------------------------------------------------------------------------
# DELETE /api/v1/tasks/{task_id}
# ---------------------------------------------------------------------------

class TestCancelTask:
    def test_cancel_queued_task(self, client, mock_db, test_account):
        task = _make_task(test_account.id, status="queued")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.commit = AsyncMock()

        resp = client.delete(f"/api/v1/tasks/{task.id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_completed_task_returns_409(self, client, mock_db, test_account):
        task = _make_task(test_account.id, status="completed")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        mock_db.execute = AsyncMock(return_value=result_mock)

        resp = client.delete(f"/api/v1/tasks/{task.id}")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_429(self, test_account):
        """Directly invoke check_rate_limit and verify it raises 429."""
        from fastapi import HTTPException
        from api.middleware.rate_limiter import check_rate_limit

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=61)  # exceeds free tier 60/min
        mock_redis.expire = AsyncMock()

        # Build a mock request with the state the rate limiter expects
        mock_request = MagicMock()
        mock_request.state.account = test_account
        mock_request.state.key_hash = "testhash"

        with pytest.raises(HTTPException) as exc_info:
            await check_rate_limit(request=mock_request, redis=mock_redis)

        assert exc_info.value.status_code == 429
        assert exc_info.value.headers["X-RateLimit-Limit"] == "60"
        assert "Retry-After" in exc_info.value.headers

    @pytest.mark.asyncio
    async def test_rate_limit_under_limit_passes(self, test_account):
        """Requests under the limit should pass without error."""
        from api.middleware.rate_limiter import check_rate_limit

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()

        mock_request = MagicMock()
        mock_request.state.account = test_account
        mock_request.state.key_hash = "testhash"

        # Should not raise
        await check_rate_limit(request=mock_request, redis=mock_redis)
        assert mock_request.state.rate_limit_remaining == 59


# ---------------------------------------------------------------------------
# Credential stripping
# ---------------------------------------------------------------------------

class TestCredentialStripping:
    def test_credentials_excluded_from_serialization(self):
        from api.schemas.task import TaskCreateRequest

        req = TaskCreateRequest(
            url="https://example.com",
            task="test",
            credentials={"user": "admin", "pass": "secret"},
        )
        dumped = req.model_dump()
        assert "credentials" not in dumped

        dumped_json = req.model_dump_json()
        assert "secret" not in dumped_json
        assert "credentials" not in dumped_json
