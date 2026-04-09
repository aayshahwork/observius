"""Tests for Supabase Storage artifact upload functions in shared/storage.py."""

from __future__ import annotations

import gzip
import os
from unittest.mock import AsyncMock, patch

import pytest

from shared.storage import StorageError, upload_har, upload_trace, upload_video


@pytest.fixture(autouse=True)
def _supabase_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required Supabase env vars for all tests."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-service-key")


@pytest.fixture(autouse=True)
def _reset_bucket_flag() -> None:
    """Reset the module-level bucket-ensured flag between tests."""
    import shared.storage as mod
    mod._artifacts_bucket_ensured = False


def _mock_supabase_upload() -> AsyncMock:
    """Return a patched _supabase_upload that returns a predictable URL."""
    mock = AsyncMock(
        side_effect=lambda path, body, ct: (
            f"https://test.supabase.co/storage/v1/object/public/artifacts/{path}"
        )
    )
    return mock


# ---------------------------------------------------------------------------
# upload_har
# ---------------------------------------------------------------------------


class TestUploadHar:
    @pytest.mark.asyncio
    async def test_compressed_upload(self) -> None:
        raw = b'{"log": {"entries": []}}'
        mock = _mock_supabase_upload()
        with patch("shared.storage._supabase_upload", mock):
            url = await upload_har("run-1", "step-1", raw, compress=True)

        mock.assert_awaited_once()
        call_args = mock.call_args
        object_path = call_args[0][0]
        body = call_args[0][1]
        content_type = call_args[0][2]

        assert object_path == "run-1/step-1/network.har.gz"
        assert content_type == "application/gzip"
        assert gzip.decompress(body) == raw
        assert "network.har.gz" in url

    @pytest.mark.asyncio
    async def test_uncompressed_upload(self) -> None:
        raw = b'{"log": {}}'
        mock = _mock_supabase_upload()
        with patch("shared.storage._supabase_upload", mock):
            url = await upload_har("run-2", "step-3", raw, compress=False)

        call_args = mock.call_args
        assert call_args[0][0] == "run-2/step-3/network.har"
        assert call_args[0][2] == "application/json"
        assert call_args[0][1] == raw
        assert "network.har" in url

    @pytest.mark.asyncio
    async def test_empty_data_raises(self) -> None:
        with pytest.raises(ValueError, match="har_data must not be empty"):
            await upload_har("run-1", "step-1", b"")

    @pytest.mark.asyncio
    async def test_storage_error_propagates(self) -> None:
        mock = AsyncMock(side_effect=StorageError("boom"))
        with patch("shared.storage._supabase_upload", mock):
            with pytest.raises(StorageError):
                await upload_har("run-1", "step-1", b"data")


# ---------------------------------------------------------------------------
# upload_trace
# ---------------------------------------------------------------------------


class TestUploadTrace:
    @pytest.mark.asyncio
    async def test_upload(self) -> None:
        data = b"PK\x03\x04fake-zip-content"
        mock = _mock_supabase_upload()
        with patch("shared.storage._supabase_upload", mock):
            url = await upload_trace("run-5", "step-2", data)

        call_args = mock.call_args
        assert call_args[0][0] == "run-5/step-2/trace.zip"
        assert call_args[0][2] == "application/zip"
        assert call_args[0][1] == data
        assert "trace.zip" in url

    @pytest.mark.asyncio
    async def test_empty_data_raises(self) -> None:
        with pytest.raises(ValueError, match="trace_zip must not be empty"):
            await upload_trace("run-1", "step-1", b"")

    @pytest.mark.asyncio
    async def test_storage_error_propagates(self) -> None:
        mock = AsyncMock(side_effect=StorageError("boom"))
        with patch("shared.storage._supabase_upload", mock):
            with pytest.raises(StorageError):
                await upload_trace("run-1", "step-1", b"data")


# ---------------------------------------------------------------------------
# upload_video
# ---------------------------------------------------------------------------


class TestUploadVideo:
    @pytest.mark.asyncio
    async def test_upload(self) -> None:
        data = b"\x1a\x45\xdf\xa3fake-webm"
        mock = _mock_supabase_upload()
        with patch("shared.storage._supabase_upload", mock):
            url = await upload_video("run-9", "step-4", data)

        call_args = mock.call_args
        assert call_args[0][0] == "run-9/step-4/recording.webm"
        assert call_args[0][2] == "video/webm"
        assert call_args[0][1] == data
        assert "recording.webm" in url

    @pytest.mark.asyncio
    async def test_empty_data_raises(self) -> None:
        with pytest.raises(ValueError, match="video_data must not be empty"):
            await upload_video("run-1", "step-1", b"")

    @pytest.mark.asyncio
    async def test_storage_error_propagates(self) -> None:
        mock = AsyncMock(side_effect=StorageError("boom"))
        with patch("shared.storage._supabase_upload", mock):
            with pytest.raises(StorageError):
                await upload_video("run-1", "step-1", b"data")


# ---------------------------------------------------------------------------
# _supabase_upload integration
# ---------------------------------------------------------------------------


class TestEnsureArtifactsBucket:
    """Test _ensure_artifacts_bucket flag behaviour."""

    @pytest.mark.asyncio
    async def test_flag_not_set_on_http_error(self) -> None:
        """A failed bucket creation must leave the flag False so the next call retries."""
        import httpx
        from unittest.mock import MagicMock
        import shared.storage as mod

        assert not mod._artifacts_bucket_ensured

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=httpx.Request("POST", "http://x"),
            response=MagicMock(status_code=500),
        )
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("shared.storage.httpx.AsyncClient", return_value=mock_client):
            await mod._ensure_artifacts_bucket("https://test.supabase.co", "key")

        assert not mod._artifacts_bucket_ensured, "flag must remain False after failure"

    @pytest.mark.asyncio
    async def test_flag_set_on_success(self) -> None:
        from unittest.mock import MagicMock
        import shared.storage as mod

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("shared.storage.httpx.AsyncClient", return_value=mock_client):
            await mod._ensure_artifacts_bucket("https://test.supabase.co", "key")

        assert mod._artifacts_bucket_ensured

    @pytest.mark.asyncio
    async def test_flag_set_on_409(self) -> None:
        """409 = bucket already exists, should count as success."""
        from unittest.mock import MagicMock
        import shared.storage as mod

        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("shared.storage.httpx.AsyncClient", return_value=mock_client):
            await mod._ensure_artifacts_bucket("https://test.supabase.co", "key")

        assert mod._artifacts_bucket_ensured

    @pytest.mark.asyncio
    async def test_skips_when_already_ensured(self) -> None:
        import shared.storage as mod
        mod._artifacts_bucket_ensured = True

        mock_client = AsyncMock()
        with patch("shared.storage.httpx.AsyncClient", mock_client):
            await mod._ensure_artifacts_bucket("https://test.supabase.co", "key")

        mock_client.assert_not_called()


class TestSupabaseUploadInternal:
    """Test the _supabase_upload helper itself (mocking httpx)."""

    @pytest.mark.asyncio
    async def test_missing_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)

        from shared.storage import _supabase_upload

        with pytest.raises(StorageError, match="SUPABASE_URL and SUPABASE_KEY"):
            await _supabase_upload("path/file.bin", b"data", "application/octet-stream")

    @pytest.mark.asyncio
    async def test_http_error_wrapped_as_storage_error(self) -> None:
        import httpx
        from unittest.mock import MagicMock

        from shared.storage import _supabase_upload

        # raise_for_status is sync, so use MagicMock for the response
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=httpx.Request("POST", "http://x"),
            response=MagicMock(status_code=500),
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("shared.storage._ensure_artifacts_bucket", new_callable=AsyncMock):
            with patch("shared.storage.httpx.AsyncClient", return_value=mock_client):
                with pytest.raises(StorageError, match="upload failed"):
                    await _supabase_upload("p/f.bin", b"x", "application/octet-stream")
