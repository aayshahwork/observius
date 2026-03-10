"""
shared/storage.py — Async S3 storage operations for replays and screenshots.

All public functions are async and use aioboto3 so they integrate cleanly
with FastAPI and the asyncio event loop used by the rest of the backend.

Required environment variables (or .env):
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    AWS_BUCKET_NAME       (default: computeruse-replays)
    AWS_REGION            (default: us-east-1)
    AWS_CDN_BASE_URL      (optional — if set, returned URLs use this base
                           instead of the s3.amazonaws.com hostname)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import aioboto3
from botocore.exceptions import BotoCoreError, ClientError

from computeruse.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level session — shared across calls to avoid re-creating credentials
# on every upload.
# ---------------------------------------------------------------------------

_session = aioboto3.Session(
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def upload_replay(file_path: str, task_id: str) -> str:
    """Upload a replay file to S3 and return its public URL.

    The file is stored at ``replays/{task_id}<ext>`` in the configured
    bucket.  The extension is preserved from *file_path* so both ``.json``
    and ``.html`` replays are handled correctly.

    Args:
        file_path: Local filesystem path to the replay file.
        task_id:   Task identifier used to construct the S3 key.

    Returns:
        HTTPS URL at which the replay file can be accessed publicly.

    Raises:
        FileNotFoundError: If *file_path* does not exist on disk.
        StorageError:      On S3 upload failures.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Replay file not found: {file_path}")

    ext = path.suffix or ".json"
    s3_key = f"replays/{task_id}{ext}"
    content_type = "text/html" if ext == ".html" else "application/json"

    file_bytes = path.read_bytes()

    await _put_object(
        key=s3_key,
        body=file_bytes,
        content_type=content_type,
        cache_control="public, max-age=86400",
    )

    url = _public_url(s3_key)
    logger.info("Replay uploaded: task=%s key=%s url=%s", task_id, s3_key, url)
    return url


async def upload_screenshot(
    screenshot_bytes: bytes,
    task_id: str,
    step_num: int,
) -> str:
    """Upload a PNG screenshot to S3 and return its URL.

    Files are stored under ``screenshots/{task_id}/step_{step_num:03d}.png``
    so all screenshots for a given task are co-located in the same S3
    prefix, making bulk retrieval or deletion straightforward.

    Args:
        screenshot_bytes: Raw PNG image bytes.
        task_id:          Task the screenshot belongs to.
        step_num:         1-based step index used to name the file.

    Returns:
        HTTPS URL for the uploaded screenshot.

    Raises:
        ValueError:    If *screenshot_bytes* is empty.
        StorageError:  On S3 upload failures.
    """
    if not screenshot_bytes:
        raise ValueError("screenshot_bytes must not be empty")

    s3_key = f"screenshots/{task_id}/step_{step_num:03d}.png"

    await _put_object(
        key=s3_key,
        body=screenshot_bytes,
        content_type="image/png",
        cache_control="public, max-age=604800",  # 7 days — screenshots don't change
    )

    url = _public_url(s3_key)
    logger.debug("Screenshot uploaded: task=%s step=%d key=%s", task_id, step_num, s3_key)
    return url


async def delete_replay(task_id: str) -> bool:
    """Delete all replay objects for *task_id* from S3.

    Attempts to delete both ``.json`` and ``.html`` variants so callers
    don't need to track which format was uploaded.

    Args:
        task_id: The task whose replay files should be removed.

    Returns:
        ``True`` if at least one object was deleted, ``False`` if no
        matching objects were found.

    Raises:
        StorageError: On S3 API errors other than "key not found".
    """
    keys = [f"replays/{task_id}.json", f"replays/{task_id}.html"]
    deleted_any = False

    for key in keys:
        if await _delete_object(key):
            deleted_any = True
            logger.info("Deleted S3 object: %s", key)

    return deleted_any


async def delete_screenshots(task_id: str) -> int:
    """Delete all screenshot objects under ``screenshots/{task_id}/``.

    Args:
        task_id: The task whose screenshots should be removed.

    Returns:
        Number of objects deleted.

    Raises:
        StorageError: On S3 API errors.
    """
    prefix = f"screenshots/{task_id}/"
    keys = await _list_keys(prefix)

    if not keys:
        logger.debug("No screenshots found for task %s", task_id)
        return 0

    deleted = await _delete_objects_batch(keys)
    logger.info("Deleted %d screenshot(s) for task %s", deleted, task_id)
    return deleted


def get_replay_url(task_id: str, extension: str = ".json") -> str:
    """Construct the public S3 URL for a replay without making a network call.

    Args:
        task_id:   Task identifier.
        extension: File extension, either ``".json"`` or ``".html"``.

    Returns:
        Public HTTPS URL for the replay file.
    """
    ext = extension if extension.startswith(".") else f".{extension}"
    return _public_url(f"replays/{task_id}{ext}")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _put_object(
    key: str,
    body: bytes,
    content_type: str,
    cache_control: str = "public, max-age=86400",
) -> None:
    """Upload *body* to S3 at *key* with public-read access.

    Args:
        key:           S3 object key (path within the bucket).
        body:          Raw bytes to upload.
        content_type:  MIME type for the ``Content-Type`` header.
        cache_control: Value of the ``Cache-Control`` header.

    Raises:
        StorageError: Wraps any ``BotoCoreError`` or ``ClientError``.
    """
    try:
        async with _session.client("s3") as s3:
            await s3.put_object(
                Bucket=settings.AWS_BUCKET_NAME,
                Key=key,
                Body=body,
                ContentType=content_type,
                CacheControl=cache_control,
                ACL="public-read",
            )
    except (BotoCoreError, ClientError) as exc:
        raise StorageError(
            f"Failed to upload '{key}' to bucket '{settings.AWS_BUCKET_NAME}': {exc}"
        ) from exc


async def _delete_object(key: str) -> bool:
    """Delete a single S3 object, returning ``False`` if it does not exist.

    Args:
        key: S3 object key to delete.

    Returns:
        ``True`` if the object existed and was deleted, ``False`` if it was
        not found (S3 returns 204 for both, so existence is checked first
        with a ``head_object`` call).

    Raises:
        StorageError: On errors other than 404.
    """
    try:
        async with _session.client("s3") as s3:
            # head_object is the cheapest way to confirm existence.
            try:
                await s3.head_object(Bucket=settings.AWS_BUCKET_NAME, Key=key)
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                    return False
                raise

            await s3.delete_object(Bucket=settings.AWS_BUCKET_NAME, Key=key)
            return True

    except (BotoCoreError, ClientError) as exc:
        raise StorageError(
            f"Failed to delete '{key}' from bucket '{settings.AWS_BUCKET_NAME}': {exc}"
        ) from exc


async def _delete_objects_batch(keys: list[str]) -> int:
    """Delete up to 1 000 S3 objects in a single API call.

    Uses the ``delete_objects`` multi-delete endpoint which is significantly
    faster than individual ``delete_object`` calls for large sets.

    Args:
        keys: List of S3 object keys to delete (max 1 000 per call).

    Returns:
        Number of objects successfully deleted.

    Raises:
        StorageError: If the S3 API call fails entirely.
    """
    if not keys:
        return 0

    objects = [{"Key": k} for k in keys[:1000]]

    try:
        async with _session.client("s3") as s3:
            response = await s3.delete_objects(
                Bucket=settings.AWS_BUCKET_NAME,
                Delete={"Objects": objects, "Quiet": False},
            )
    except (BotoCoreError, ClientError) as exc:
        raise StorageError(
            f"Batch delete of {len(objects)} objects failed: {exc}"
        ) from exc

    deleted = response.get("Deleted", [])
    errors = response.get("Errors", [])

    if errors:
        for err in errors:
            logger.warning(
                "S3 delete error for key '%s': %s %s",
                err.get("Key"),
                err.get("Code"),
                err.get("Message"),
            )

    return len(deleted)


async def _list_keys(prefix: str) -> list[str]:
    """List all S3 object keys under *prefix*.

    Handles pagination automatically via ``list_objects_v2`` continuation
    tokens so prefixes with more than 1 000 objects are fully enumerated.

    Args:
        prefix: S3 key prefix to list under (should end with ``/``).

    Returns:
        List of full S3 object keys.

    Raises:
        StorageError: On S3 API errors.
    """
    keys: list[str] = []
    continuation_token: Optional[str] = None

    try:
        async with _session.client("s3") as s3:
            while True:
                kwargs: dict = {
                    "Bucket": settings.AWS_BUCKET_NAME,
                    "Prefix": prefix,
                }
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token

                response = await s3.list_objects_v2(**kwargs)
                keys.extend(obj["Key"] for obj in response.get("Contents", []))

                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")

    except (BotoCoreError, ClientError) as exc:
        raise StorageError(f"Failed to list objects under '{prefix}': {exc}") from exc

    return keys


def _public_url(key: str) -> str:
    """Build a public HTTPS URL for *key*.

    Uses ``AWS_CDN_BASE_URL`` if set (e.g. a CloudFront distribution),
    otherwise falls back to the canonical S3 HTTPS hostname.

    Args:
        key: S3 object key.

    Returns:
        Full HTTPS URL string.
    """
    import os
    cdn_base = os.environ.get("AWS_CDN_BASE_URL", "").rstrip("/")
    if cdn_base:
        return f"{cdn_base}/{key}"

    return (
        f"https://{settings.AWS_BUCKET_NAME}"
        f".s3.{settings.AWS_REGION}.amazonaws.com/{key}"
    )


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class StorageError(Exception):
    """Raised when an S3 storage operation fails.

    Wraps ``botocore`` exceptions so callers only need to handle one type.
    The original exception is always chained via ``__cause__``.
    """
