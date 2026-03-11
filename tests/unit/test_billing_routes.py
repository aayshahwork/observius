"""
tests/unit/test_billing_routes.py — Tests for billing and account API routes.

Tests:
- Checkout session creation (200, 400 same tier, 400 invalid tier)
- Webhook event handling (checkout.session.completed, subscription deleted, invoice paid)
- Usage endpoint (200)
- Portal session creation (200, 400 no customer)
- API key CRUD (create 201, list 200, revoke 200, revoke 404/409)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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
        stripe_customer_id=None,
        monthly_step_limit=500,
        monthly_steps_used=42,
        encryption_key_id="enc-key-1",
        webhook_secret=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    acct = MagicMock()
    for k, v in defaults.items():
        setattr(acct, k, v)
    return acct


def _make_api_key(account_id: uuid.UUID, **overrides):
    defaults = dict(
        id=uuid.uuid4(),
        account_id=account_id,
        key_hash="fakehash",
        key_prefix="cu_prod_",
        key_suffix="abcd",
        label=None,
        expires_at=None,
        revoked_at=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    key = MagicMock()
    for k, v in defaults.items():
        setattr(key, k, v)
    return key


@pytest.fixture
def test_account():
    return _make_account()


@pytest.fixture
def paid_account():
    return _make_account(
        tier="startup",
        stripe_customer_id="cus_test123",
        monthly_step_limit=5000,
    )


@pytest.fixture
def mock_db():
    return AsyncMock()


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

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture
def paid_client(paid_account, mock_db, mock_redis):
    async def override_auth():
        return paid_account

    async def override_db():
        yield mock_db

    async def override_redis():
        yield mock_redis

    app.dependency_overrides[get_current_account] = override_auth
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = override_redis

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture
def webhook_client(mock_db, mock_redis):
    """Client for webhook tests — no auth override (webhook has no auth)."""
    async def override_db():
        yield mock_db

    async def override_redis():
        yield mock_redis

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = override_redis

    yield TestClient(app)

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/v1/billing/checkout
# ---------------------------------------------------------------------------

class TestCheckout:
    def test_checkout_returns_url(self, client):
        with patch.dict("api.config.settings.STRIPE_PRICE_IDS", {"startup": "price_123", "growth": "price_456", "enterprise": "price_789"}):
            resp = client.post(
                "/api/v1/billing/checkout",
                json={"tier": "startup"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "checkout_url" in body
        assert "startup" in body["checkout_url"]

    def test_checkout_same_tier_returns_400(self, client):
        resp = client.post(
            "/api/v1/billing/checkout",
            json={"tier": "free"},
        )
        assert resp.status_code == 422  # "free" not in Literal["startup", "growth", "enterprise"]

    def test_checkout_already_on_tier(self, paid_client):
        with patch.dict("api.config.settings.STRIPE_PRICE_IDS", {"startup": "price_123"}):
            resp = paid_client.post(
                "/api/v1/billing/checkout",
                json={"tier": "startup"},
            )
        assert resp.status_code == 400

    def test_checkout_invalid_tier_returns_422(self, client):
        resp = client.post(
            "/api/v1/billing/checkout",
            json={"tier": "nonexistent"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/billing/webhook
# ---------------------------------------------------------------------------

class TestWebhook:
    def test_checkout_completed_upgrades_tier(self, webhook_client, mock_db):
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_test123",
                    "metadata": {"tier": "growth"},
                }
            },
        }
        resp = webhook_client.post(
            "/api/v1/billing/webhook",
            content=json.dumps(event),
            headers={
                "Content-Type": "application/json",
                "stripe-signature": "t=123,v1=fakesig",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"received": True}
        # Verify DB update was called
        assert mock_db.execute.called
        assert mock_db.commit.called

    def test_subscription_deleted_downgrades(self, webhook_client, mock_db):
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        event = {
            "type": "customer.subscription.deleted",
            "data": {
                "object": {"customer": "cus_test123"}
            },
        }
        resp = webhook_client.post(
            "/api/v1/billing/webhook",
            content=json.dumps(event),
            headers={
                "Content-Type": "application/json",
                "stripe-signature": "t=123,v1=fakesig",
            },
        )
        assert resp.status_code == 200

    def test_invoice_paid_resets_usage(self, webhook_client, mock_db):
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        event = {
            "type": "invoice.paid",
            "data": {
                "object": {"customer": "cus_test123"}
            },
        }
        resp = webhook_client.post(
            "/api/v1/billing/webhook",
            content=json.dumps(event),
            headers={
                "Content-Type": "application/json",
                "stripe-signature": "t=123,v1=fakesig",
            },
        )
        assert resp.status_code == 200

    def test_unknown_event_returns_200(self, webhook_client):
        event = {
            "type": "some.unknown.event",
            "data": {"object": {}},
        }
        resp = webhook_client.post(
            "/api/v1/billing/webhook",
            content=json.dumps(event),
            headers={
                "Content-Type": "application/json",
                "stripe-signature": "t=123,v1=fakesig",
            },
        )
        assert resp.status_code == 200

    def test_invalid_json_returns_400(self, webhook_client):
        resp = webhook_client.post(
            "/api/v1/billing/webhook",
            content=b"not json",
            headers={
                "Content-Type": "application/json",
                "stripe-signature": "t=123,v1=fakesig",
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/v1/billing/usage
# ---------------------------------------------------------------------------

class TestUsage:
    def test_usage_returns_stats(self, client, mock_db, test_account):
        row = MagicMock()
        row.monthly_steps_used = 42
        row.monthly_step_limit = 500
        row.tier = "free"

        result_mock = MagicMock()
        result_mock.one_or_none.return_value = row
        mock_db.execute = AsyncMock(return_value=result_mock)

        resp = client.get("/api/v1/billing/usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["monthly_steps_used"] == 42
        assert body["monthly_step_limit"] == 500
        assert body["tier"] == "free"


# ---------------------------------------------------------------------------
# POST /api/v1/billing/portal
# ---------------------------------------------------------------------------

class TestPortal:
    def test_portal_returns_url(self, paid_client):
        resp = paid_client.post("/api/v1/billing/portal")
        assert resp.status_code == 200
        body = resp.json()
        assert "portal_url" in body
        assert "cus_test123" in body["portal_url"]

    def test_portal_no_customer_returns_400(self, client):
        resp = client.post("/api/v1/billing/portal")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/v1/account/api-keys
# ---------------------------------------------------------------------------

class TestListApiKeys:
    def test_list_returns_keys(self, client, mock_db, test_account):
        keys = [_make_api_key(test_account.id, label="test key")]

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = keys
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db.execute = AsyncMock(return_value=result_mock)

        resp = client.get("/api/v1/account/api-keys")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["key_prefix"] == "cu_prod_"
        assert body[0]["key_suffix"] == "abcd"

    def test_list_empty(self, client, mock_db):
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db.execute = AsyncMock(return_value=result_mock)

        resp = client.get("/api/v1/account/api-keys")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/v1/account/api-keys
# ---------------------------------------------------------------------------

class TestCreateApiKey:
    def test_create_returns_raw_key(self, client, mock_db):
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        resp = client.post(
            "/api/v1/account/api-keys",
            json={"label": "My test key"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "key" in body
        assert body["key"].startswith("cu_prod_")
        assert body["label"] == "My test key"
        assert "id" in body
        assert body["key_prefix"] == body["key"][:8]
        assert body["key_suffix"] == body["key"][-4:]

    def test_create_without_label(self, client, mock_db):
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        resp = client.post(
            "/api/v1/account/api-keys",
            json={},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["key"].startswith("cu_prod_")
        assert body["label"] is None


# ---------------------------------------------------------------------------
# DELETE /api/v1/account/api-keys/{key_id}
# ---------------------------------------------------------------------------

class TestRevokeApiKey:
    def test_revoke_returns_200(self, client, mock_db, test_account):
        key = _make_api_key(test_account.id)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = key
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.commit = AsyncMock()

        resp = client.delete(f"/api/v1/account/api-keys/{key.id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    def test_revoke_missing_key_returns_404(self, client, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        resp = client.delete(f"/api/v1/account/api-keys/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_revoke_already_revoked_returns_409(self, client, mock_db, test_account):
        key = _make_api_key(
            test_account.id,
            revoked_at=datetime.now(timezone.utc),
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = key
        mock_db.execute = AsyncMock(return_value=result_mock)

        resp = client.delete(f"/api/v1/account/api-keys/{key.id}")
        assert resp.status_code == 409
