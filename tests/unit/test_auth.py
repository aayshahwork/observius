"""
tests/unit/test_auth.py — Tests for API key authentication middleware.

Tests:
- Valid key returns 200
- Missing key returns 401
- Invalid (unknown) key returns 401
- Expired key returns 401
- Revoked key returns 401
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from api.middleware.auth import get_current_account, _hash_key


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_RAW_KEY = "cu_test_testkey1234567890abcdef12"
TEST_KEY_HASH = _hash_key(TEST_RAW_KEY)


def _make_account(**overrides):
    """Create a mock Account object."""
    defaults = dict(
        id=uuid.uuid4(),
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


def _make_api_key(account, **overrides):
    """Create a mock ApiKey object."""
    defaults = dict(
        id=uuid.uuid4(),
        account_id=account.id,
        key_hash=TEST_KEY_HASH,
        key_prefix="cu_test_",
        key_suffix="f12",
        label=None,
        expires_at=None,
        revoked_at=None,
        created_at=datetime.now(timezone.utc),
        account=account,
    )
    defaults.update(overrides)
    key = MagicMock()
    for k, v in defaults.items():
        setattr(key, k, v)
    return key


def _build_app(db_mock: AsyncMock) -> FastAPI:
    """Build a minimal FastAPI app with the auth dependency."""
    test_app = FastAPI()

    @test_app.get("/protected")
    async def protected(account=Depends(get_current_account)):
        return {"account_id": str(account.id), "email": account.email}

    from api.dependencies import get_db

    async def mock_get_db():
        yield db_mock

    test_app.dependency_overrides[get_db] = mock_get_db
    return test_app


def _mock_db_with_result(api_key_obj):
    """Create a mock AsyncSession that returns the given api_key from a query."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = api_key_obj
    db.execute.return_value = result_mock
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuthValidKey:
    def test_valid_key_returns_200(self):
        account = _make_account()
        api_key = _make_api_key(account)
        db = _mock_db_with_result(api_key)
        test_app = _build_app(db)
        client = TestClient(test_app)

        resp = client.get("/protected", headers={"X-API-Key": TEST_RAW_KEY})
        assert resp.status_code == 200
        body = resp.json()
        assert body["account_id"] == str(account.id)
        assert body["email"] == "test@pokant.dev"


class TestAuthMissingKey:
    def test_missing_key_returns_401(self):
        """Returns 401 when no API key header is provided."""
        db = _mock_db_with_result(None)
        test_app = _build_app(db)
        client = TestClient(test_app)

        resp = client.get("/protected")
        assert resp.status_code == 401
        assert resp.json()["detail"]["error_code"] == "UNAUTHORIZED"

    def test_bearer_token_works(self):
        """Authorization: Bearer header is accepted as an alternative to X-API-Key."""
        account = _make_account()
        api_key = _make_api_key(account)
        db = _mock_db_with_result(api_key)
        test_app = _build_app(db)
        client = TestClient(test_app)

        resp = client.get("/protected", headers={"Authorization": f"Bearer {TEST_RAW_KEY}"})
        assert resp.status_code == 200
        assert resp.json()["account_id"] == str(account.id)


class TestAuthInvalidKey:
    def test_unknown_key_returns_401(self):
        db = _mock_db_with_result(None)
        test_app = _build_app(db)
        client = TestClient(test_app)

        resp = client.get("/protected", headers={"X-API-Key": "cu_bad_key_does_not_exist"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error_code"] == "UNAUTHORIZED"


class TestAuthExpiredKey:
    def test_expired_key_returns_401(self):
        account = _make_account()
        expired_key = _make_api_key(
            account,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db = _mock_db_with_result(expired_key)
        test_app = _build_app(db)
        client = TestClient(test_app)

        resp = client.get("/protected", headers={"X-API-Key": TEST_RAW_KEY})
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"]["message"].lower()


class TestAuthRevokedKey:
    def test_revoked_key_returns_401(self):
        account = _make_account()
        revoked_key = _make_api_key(
            account,
            revoked_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db = _mock_db_with_result(revoked_key)
        test_app = _build_app(db)
        client = TestClient(test_app)

        resp = client.get("/protected", headers={"X-API-Key": TEST_RAW_KEY})
        assert resp.status_code == 401
        assert "revoked" in resp.json()["detail"]["message"].lower()


class TestHashKey:
    def test_hash_is_sha256(self):
        raw = "test-key"
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert _hash_key(raw) == expected
