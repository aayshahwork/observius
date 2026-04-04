"""
tests/unit/test_audit_routes.py — Tests for audit log query endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import get_db
from api.middleware.auth import get_current_account


TEST_ACCOUNT_ID = uuid.uuid4()


def _make_account(**overrides):
    defaults = dict(
        id=TEST_ACCOUNT_ID,
        email="test@pokant.dev",
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


def _make_audit_entry(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        account_id=TEST_ACCOUNT_ID,
        actor_type="user",
        actor_id=str(TEST_ACCOUNT_ID),
        action="task.created",
        resource_type="task",
        resource_id=str(uuid.uuid4()),
        metadata_={"url": "https://example.com"},
        ip_address="127.0.0.1",
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    entry = MagicMock()
    for k, v in defaults.items():
        setattr(entry, k, v)
    return entry


@pytest.fixture
def test_account():
    return _make_account()


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def client(test_account, mock_db):
    async def override_auth():
        return test_account

    async def override_db():
        yield mock_db

    app.dependency_overrides[get_current_account] = override_auth
    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestListAuditLogs:
    def test_list_returns_paginated(self, client, mock_db):
        entries = [_make_audit_entry() for _ in range(3)]

        # Mock: first execute = count, second = rows
        count_result = MagicMock()
        count_result.scalar_one.return_value = 3

        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = entries

        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        resp = client.get("/api/v1/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["entries"]) == 3
        assert data["has_more"] is False

    def test_list_with_action_filter(self, client, mock_db):
        entry = _make_audit_entry(action="api_key.created")
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = [entry]
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        resp = client.get("/api/v1/audit?action=api_key.created")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_empty(self, client, mock_db):
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        resp = client.get("/api/v1/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["entries"] == []
        assert data["has_more"] is False


class TestResourceHistory:
    def test_resource_history_returns_entries(self, client, mock_db):
        resource_id = uuid.uuid4()
        entry = _make_audit_entry(resource_type="task", resource_id=str(resource_id))
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = [entry]
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        resp = client.get(f"/api/v1/audit/task/{resource_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["entries"]) == 1

    def test_resource_history_empty(self, client, mock_db):
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        resp = client.get(f"/api/v1/audit/task/{uuid.uuid4()}")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
