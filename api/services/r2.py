"""
api/services/r2.py — Pre-signed URL generation for Cloudflare R2.

Provides helpers to generate real pre-signed URLs for step screenshots
and replay recordings stored in R2 (S3-compatible).

The boto3 client is lazily initialised as a module-level singleton.
generate_presigned_url is a local computation (no network call), so
using sync boto3 inside async routes is safe.
"""

from __future__ import annotations

import threading

import structlog

logger = structlog.get_logger("api.services.r2")

# ---------------------------------------------------------------------------
# Expiry constants
# ---------------------------------------------------------------------------

SCREENSHOT_EXPIRY = 3600        # 1 hour
REPLAY_EXPIRY = 7 * 86400      # 7 days

# ---------------------------------------------------------------------------
# Lazy singleton client
# ---------------------------------------------------------------------------

_client = None
_client_lock = threading.Lock()


def _get_client():
    """Return a cached boto3 S3 client configured for R2, or None if unconfigured."""
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        # Double-check after acquiring lock
        if _client is not None:
            return _client

        from api.config import settings

        if not settings.R2_ACCESS_KEY or not settings.R2_SECRET_KEY:
            return None

        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT or None,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
        return _client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_presigned_url(s3_key: str, expires_in: int = 3600) -> str | None:
    """Generate a pre-signed GET URL for an R2 object.

    Returns None when R2 is not configured or on any signing error.
    """
    client = _get_client()
    if client is None:
        return None

    from api.config import settings

    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.R2_BUCKET_NAME, "Key": s3_key},
            ExpiresIn=expires_in,
        )
    except Exception:
        logger.warning("presigned_url_failed", s3_key=s3_key, exc_info=True)
        return None


def presign_screenshot(s3_key: str) -> str | None:
    """Pre-sign a step screenshot URL (1-hour expiry).

    Handles local:// keys by returning a local file-serving URL.
    """
    if s3_key.startswith("local://"):
        return _local_file_url(s3_key)
    return generate_presigned_url(s3_key, expires_in=SCREENSHOT_EXPIRY)


def presign_replay(s3_key: str) -> str | None:
    """Pre-sign a replay recording URL (7-day expiry).

    Handles local:// keys by returning a local file-serving URL.
    """
    if s3_key.startswith("local://"):
        return _local_file_url(s3_key)
    return generate_presigned_url(s3_key, expires_in=REPLAY_EXPIRY)


def _local_file_url(local_key: str) -> str:
    """Convert a local:// key to an API file-serving URL."""
    import urllib.parse

    # local://replays/{task_id}/replay.html -> replays/{task_id}/replay.html
    path = local_key.removeprefix("local://")
    return f"/api/v1/local-files/{urllib.parse.quote(path, safe='/')}"


def is_r2_configured() -> bool:
    """Return True if R2 credentials are present and non-placeholder."""
    from api.config import settings

    key = settings.R2_ACCESS_KEY
    secret = settings.R2_SECRET_KEY
    return bool(key and secret and key != "your_r2_access_key" and secret != "your_r2_secret_key")
