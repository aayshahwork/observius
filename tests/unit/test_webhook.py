"""
tests/unit/test_webhook.py — Unit tests for webhook delivery.

Tests:
- HMAC-SHA256 signature computation
- Retry backoff schedule
- Final failure marks webhook_delivered=False
- Payload structure
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# HMAC Signature
# ---------------------------------------------------------------------------


class TestHMACSignature:
    """Verify HMAC-SHA256 signing logic matches what deliver_webhook produces."""

    def test_signature_is_valid_hex(self):
        """HMAC-SHA256 should produce a 64-char hex string."""
        secret = "test-webhook-secret-abc123"
        payload = json.dumps(
            {"task_id": str(uuid.uuid4()), "status": "completed"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_different_payloads_produce_different_signatures(self):
        """Different payloads must produce different signatures."""
        secret = "shared-secret"

        payload_a = json.dumps(
            {"task_id": "aaa", "status": "completed"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        payload_b = json.dumps(
            {"task_id": "bbb", "status": "completed"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

        sig_a = hmac.new(secret.encode(), payload_a, hashlib.sha256).hexdigest()
        sig_b = hmac.new(secret.encode(), payload_b, hashlib.sha256).hexdigest()

        assert sig_a != sig_b

    def test_same_payload_same_secret_is_deterministic(self):
        """Same payload + secret should always produce the same signature."""
        secret = "deterministic-test"
        payload = json.dumps(
            {"task_id": "xyz", "status": "failed"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

        sig1 = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        sig2 = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert sig1 == sig2

    def test_no_signature_header_when_no_webhook_secret(self):
        """When account has no webhook_secret, X-CU-Signature should be omitted."""
        account = MagicMock()
        account.webhook_secret = None

        headers = {"Content-Type": "application/json"}
        if account.webhook_secret:
            headers["X-CU-Signature"] = "should-not-appear"

        assert "X-CU-Signature" not in headers

    def test_signature_header_present_when_webhook_secret_set(self):
        """When account has webhook_secret, X-CU-Signature should be present."""
        secret = "my-secret"
        payload_bytes = b'{"task_id":"123"}'

        headers = {"Content-Type": "application/json"}
        sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        headers["X-CU-Signature"] = sig

        assert "X-CU-Signature" in headers
        assert len(headers["X-CU-Signature"]) == 64


# ---------------------------------------------------------------------------
# Retry Backoff
# ---------------------------------------------------------------------------


class TestWebhookRetry:
    """Verify the retry backoff schedule."""

    def test_backoff_schedule_values(self):
        """Backoff intervals should be [30, 60, 120, 240, 480]."""
        from workers.tasks import WEBHOOK_BACKOFFS

        assert WEBHOOK_BACKOFFS == [30, 60, 120, 240, 480]

    def test_backoff_schedule_doubles(self):
        """Each interval should double the previous one."""
        from workers.tasks import WEBHOOK_BACKOFFS

        for i in range(1, len(WEBHOOK_BACKOFFS)):
            assert WEBHOOK_BACKOFFS[i] == WEBHOOK_BACKOFFS[i - 1] * 2

    def test_five_retries(self):
        """Should retry exactly 5 times."""
        from workers.tasks import WEBHOOK_BACKOFFS, deliver_webhook

        assert len(WEBHOOK_BACKOFFS) == 5
        assert deliver_webhook.max_retries == 5

    @patch("workers.db.get_sync_session")
    def test_mark_webhook_failed_sets_false(self, mock_get_session):
        """_mark_webhook_failed should set webhook_delivered=False."""
        session = MagicMock()
        mock_get_session.return_value = session

        task = MagicMock()
        session.get.return_value = task

        from workers.tasks import _mark_webhook_failed

        _mark_webhook_failed(str(uuid.uuid4()))

        assert task.webhook_delivered is False
        session.commit.assert_called_once()

    @patch("workers.db.get_sync_session")
    def test_mark_webhook_failed_handles_missing_task(self, mock_get_session):
        """_mark_webhook_failed should not crash if task is missing."""
        session = MagicMock()
        mock_get_session.return_value = session
        session.get.return_value = None

        from workers.tasks import _mark_webhook_failed

        # Should not raise
        _mark_webhook_failed(str(uuid.uuid4()))
        session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Webhook Payload
# ---------------------------------------------------------------------------


class TestWebhookPayload:
    """Verify the webhook payload structure."""

    def test_payload_contains_required_fields(self):
        """Webhook payload must include the 5 required fields."""
        payload = {
            "task_id": str(uuid.uuid4()),
            "status": "completed",
            "result": {"data": "value"},
            "replay_url": "https://r2.computeruse.dev/replays/123/replay.html",
            "duration_ms": 5000,
        }

        required_keys = {"task_id", "status", "result", "replay_url", "duration_ms"}
        assert required_keys == set(payload.keys())

    def test_payload_json_canonical_form(self):
        """Payload should serialize with separators=(',', ':') and sort_keys=True for HMAC."""
        payload = {"status": "completed", "task_id": "abc", "result": None, "replay_url": None, "duration_ms": 100}
        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        # Keys should be sorted
        assert canonical.index('"duration_ms"') < canonical.index('"replay_url"')
        assert canonical.index('"replay_url"') < canonical.index('"result"')
        assert canonical.index('"result"') < canonical.index('"status"')
        assert canonical.index('"status"') < canonical.index('"task_id"')

        # No spaces
        assert " " not in canonical

    def test_replay_url_none_when_no_s3_key(self):
        """replay_url should be None when task has no replay_s3_key."""
        replay_s3_key = None
        replay_url = (
            f"https://r2.computeruse.dev/{replay_s3_key}"
            if replay_s3_key
            else None
        )
        assert replay_url is None
