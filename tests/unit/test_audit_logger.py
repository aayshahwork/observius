"""
tests/unit/test_audit_logger.py — Tests for the AuditLogger service.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from api.services.audit_logger import (
    API_KEY_CREATED,
    TASK_CREATED,
    AuditLogger,
)


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def logger(mock_db):
    return AuditLogger(mock_db)


class TestAuditLoggerLog:
    @pytest.mark.asyncio
    async def test_log_inserts_row(self, logger, mock_db):
        account_id = uuid.uuid4()
        await logger.log(
            account_id=account_id,
            actor_type="user",
            actor_id=str(account_id),
            action=TASK_CREATED,
            resource_type="task",
            resource_id=str(uuid.uuid4()),
        )
        mock_db.execute.assert_awaited_once()
        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert params["account_id"] == str(account_id)
        assert params["actor_type"] == "user"
        assert params["action"] == "task.created"
        assert params["resource_type"] == "task"
        assert params["metadata"] is None
        assert params["ip_address"] is None

    @pytest.mark.asyncio
    async def test_log_with_metadata(self, logger, mock_db):
        account_id = uuid.uuid4()
        metadata = {"url": "https://example.com", "label": "test"}
        await logger.log(
            account_id=account_id,
            actor_type="user",
            actor_id=str(account_id),
            action=API_KEY_CREATED,
            resource_type="api_key",
            resource_id=str(uuid.uuid4()),
            metadata=metadata,
        )
        params = mock_db.execute.call_args[0][1]
        assert params["metadata"] == json.dumps(metadata)

    @pytest.mark.asyncio
    async def test_log_with_ip_address(self, logger, mock_db):
        account_id = uuid.uuid4()
        await logger.log(
            account_id=account_id,
            actor_type="user",
            actor_id=str(account_id),
            action=TASK_CREATED,
            resource_type="task",
            resource_id=str(uuid.uuid4()),
            ip_address="192.168.1.1",
        )
        params = mock_db.execute.call_args[0][1]
        assert params["ip_address"] == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_log_does_not_commit(self, logger, mock_db):
        """Audit log should NOT commit — it participates in caller's transaction."""
        await logger.log(
            account_id=uuid.uuid4(),
            actor_type="webhook",
            actor_id="stripe",
            action=TASK_CREATED,
            resource_type="task",
            resource_id=str(uuid.uuid4()),
        )
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sql_uses_jsonb_cast(self, logger, mock_db):
        """SQL should use CAST(:metadata AS jsonb) for explicit type binding."""
        await logger.log(
            account_id=uuid.uuid4(),
            actor_type="user",
            actor_id="user-1",
            action=TASK_CREATED,
            resource_type="task",
            resource_id=str(uuid.uuid4()),
            metadata={"key": "value"},
        )
        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "CAST(:metadata AS jsonb)" in sql_text
