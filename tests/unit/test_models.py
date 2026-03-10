"""Unit tests for SQLAlchemy models.

These tests validate model instantiation, constraint definitions, and enum
validation at the Python level (no database required).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Import models (uses stubs from conftest.py for heavy deps)
# ---------------------------------------------------------------------------

from api.models.account import Account
from api.models.api_key import ApiKey
from api.models.task import Task
from api.models.task_step import TaskStep
from api.models.session import Session
from api.models.audit_log import AuditLog


# ===========================================================================
# Account
# ===========================================================================

class TestAccount:
    def test_instantiate_with_valid_data(self):
        acct = Account(
            id=_uuid(),
            email="user@example.com",
            name="Test User",
            tier="free",
            monthly_step_limit=500,
            monthly_steps_used=0,
            encryption_key_id="enc_001",
            created_at=_now(),
        )
        assert acct.email == "user@example.com"
        assert acct.tier == "free"

    def test_valid_tiers(self):
        for tier in ("free", "startup", "growth", "enterprise"):
            acct = Account(
                id=_uuid(),
                email=f"{tier}@example.com",
                name="T",
                tier=tier,
                encryption_key_id="k",
            )
            assert acct.tier == tier

    def test_table_has_tier_check_constraint(self):
        constraints = {c.name for c in Account.__table__.constraints if hasattr(c, "name") and c.name}
        assert "accounts_tier_check" in constraints


# ===========================================================================
# ApiKey
# ===========================================================================

class TestApiKey:
    def test_instantiate_with_valid_data(self):
        ak = ApiKey(
            id=_uuid(),
            account_id=_uuid(),
            key_hash="abc123hash",
            key_prefix="cu_prod_",
            key_suffix="ab12",
            label="My Key",
            created_at=_now(),
        )
        assert ak.key_prefix == "cu_prod_"
        assert ak.key_suffix == "ab12"

    def test_optional_fields_default_none(self):
        ak = ApiKey(
            id=_uuid(),
            account_id=_uuid(),
            key_hash="h",
            key_prefix="p",
            key_suffix="s",
        )
        assert ak.label is None
        assert ak.expires_at is None
        assert ak.revoked_at is None


# ===========================================================================
# Task
# ===========================================================================

class TestTask:
    def test_instantiate_with_valid_data(self):
        t = Task(
            id=_uuid(),
            account_id=_uuid(),
            status="queued",
            url="https://example.com",
            task_description="Click the button",
        )
        assert t.status == "queued"
        assert t.url == "https://example.com"

    def test_valid_statuses(self):
        for status in ("queued", "running", "completed", "failed", "timeout", "cancelled"):
            t = Task(
                id=_uuid(),
                account_id=_uuid(),
                status=status,
                url="https://example.com",
                task_description="Do something",
            )
            assert t.status == status

    def test_table_has_status_check_constraint(self):
        constraints = {c.name for c in Task.__table__.constraints if hasattr(c, "name") and c.name}
        assert "tasks_status_check" in constraints

    def test_table_has_description_length_constraint(self):
        constraints = {c.name for c in Task.__table__.constraints if hasattr(c, "name") and c.name}
        assert "tasks_description_length_check" in constraints

    def test_table_has_max_cost_check_constraint(self):
        constraints = {c.name for c in Task.__table__.constraints if hasattr(c, "name") and c.name}
        assert "tasks_max_cost_check" in constraints


# ===========================================================================
# TaskStep
# ===========================================================================

class TestTaskStep:
    def test_instantiate_with_valid_data(self):
        ts = TaskStep(
            id=_uuid(),
            task_id=_uuid(),
            step_number=1,
            action_type="click",
            description="Clicked the login button",
            duration_ms=150,
        )
        assert ts.step_number == 1
        assert ts.action_type == "click"

    def test_optional_fields(self):
        ts = TaskStep(
            id=_uuid(),
            task_id=_uuid(),
            step_number=1,
            action_type="screenshot",
        )
        assert ts.description is None
        assert ts.screenshot_s3_key is None
        assert ts.error_message is None


# ===========================================================================
# Session
# ===========================================================================

class TestSession:
    def test_instantiate_with_valid_data(self):
        s = Session(
            id=_uuid(),
            account_id=_uuid(),
            origin_domain="example.com",
            cookies_encrypted=b"encrypted_data",
            auth_state="active",
        )
        assert s.origin_domain == "example.com"
        assert s.auth_state == "active"

    def test_valid_auth_states(self):
        for state in ("active", "stale", "expired"):
            s = Session(
                id=_uuid(),
                account_id=_uuid(),
                origin_domain="example.com",
                cookies_encrypted=b"data",
                auth_state=state,
            )
            assert s.auth_state == state

    def test_table_has_auth_state_check_constraint(self):
        constraints = {c.name for c in Session.__table__.constraints if hasattr(c, "name") and c.name}
        assert "sessions_auth_state_check" in constraints


# ===========================================================================
# AuditLog
# ===========================================================================

class TestAuditLog:
    def test_instantiate_with_valid_data(self):
        al = AuditLog(
            id=_uuid(),
            account_id=_uuid(),
            actor_type="user",
            actor_id="user_123",
            action="task.created",
            resource_type="task",
            resource_id="task_456",
            ip_address="192.168.1.1",
        )
        assert al.action == "task.created"
        assert al.ip_address == "192.168.1.1"

    def test_optional_metadata(self):
        al = AuditLog(
            id=_uuid(),
            account_id=_uuid(),
            actor_type="system",
            actor_id="worker_1",
            action="task.completed",
            resource_type="task",
            resource_id="t1",
        )
        assert al.metadata_ is None
