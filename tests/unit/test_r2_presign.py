"""
tests/unit/test_r2_presign.py — Unit tests for R2 pre-signed URL generation.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Reset the module-level singleton between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_client():
    """Clear the cached R2 client before each test."""
    import api.services.r2 as r2_mod

    r2_mod._client = None
    yield
    r2_mod._client = None


@pytest.fixture()
def mock_s3_client():
    """Replace the stubbed boto3.client with a controllable mock factory."""
    boto3_stub = sys.modules["boto3"]
    original = boto3_stub.client

    s3_client = MagicMock()
    s3_client.generate_presigned_url.return_value = (
        "https://r2.example.com/bucket/key?X-Amz-Signature=abc"
    )
    boto3_stub.client = MagicMock(return_value=s3_client)

    yield s3_client

    boto3_stub.client = original


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPresignedURLGeneration:
    """Verify pre-signed URL generation with a mocked boto3 client."""

    def test_presign_screenshot_returns_url(self, mock_s3_client):
        from api.config import settings
        from api.services.r2 import SCREENSHOT_EXPIRY, presign_screenshot

        url = presign_screenshot("replays/abc/step_1.png")

        assert url == "https://r2.example.com/bucket/key?X-Amz-Signature=abc"
        mock_s3_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": settings.R2_BUCKET_NAME, "Key": "replays/abc/step_1.png"},
            ExpiresIn=SCREENSHOT_EXPIRY,
        )

    def test_presign_replay_uses_7_day_expiry(self, mock_s3_client):
        from api.config import settings
        from api.services.r2 import REPLAY_EXPIRY, presign_replay

        url = presign_replay("replays/abc/replay.html")

        assert url == "https://r2.example.com/bucket/key?X-Amz-Signature=abc"
        mock_s3_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": settings.R2_BUCKET_NAME, "Key": "replays/abc/replay.html"},
            ExpiresIn=REPLAY_EXPIRY,
        )


# ---------------------------------------------------------------------------
# Missing credentials
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    """When R2 credentials are empty, all functions return None."""

    def test_returns_none_when_access_key_empty(self):
        from api.services.r2 import presign_screenshot

        with patch("api.config.settings") as mock_settings:
            mock_settings.R2_ACCESS_KEY = ""
            mock_settings.R2_SECRET_KEY = "secret"

            assert presign_screenshot("some/key.png") is None

    def test_returns_none_when_secret_key_empty(self):
        from api.services.r2 import presign_screenshot

        with patch("api.config.settings") as mock_settings:
            mock_settings.R2_ACCESS_KEY = "key"
            mock_settings.R2_SECRET_KEY = ""

            assert presign_screenshot("some/key.png") is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Errors during signing should return None, not raise."""

    def test_returns_none_on_boto_error(self, mock_s3_client):
        from api.services.r2 import presign_screenshot

        mock_s3_client.generate_presigned_url.side_effect = Exception("signing failed")

        result = presign_screenshot("some/key.png")

        assert result is None


# ---------------------------------------------------------------------------
# Singleton behaviour
# ---------------------------------------------------------------------------


class TestClientSingleton:
    """The boto3 client should be created once and reused."""

    def test_client_created_once(self):
        boto3_stub = sys.modules["boto3"]
        original = boto3_stub.client

        from api.services.r2 import _get_client

        mock_client = MagicMock()
        client_factory = MagicMock(return_value=mock_client)
        boto3_stub.client = client_factory

        try:
            client1 = _get_client()
            client2 = _get_client()

            assert client1 is client2
            client_factory.assert_called_once()
        finally:
            boto3_stub.client = original
