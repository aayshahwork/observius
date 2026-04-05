"""
api/middleware/auth.py — API key authentication dependency.

Extracts X-API-Key header, SHA-256 hashes it, validates against the
api_keys table, and returns the associated Account.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_db
from api.models.account import Account
from api.models.api_key import ApiKey


def _hash_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _extract_api_key(request: Request) -> str:
    """Extract API key from X-API-Key header or Authorization: Bearer header."""
    key = request.headers.get("X-API-Key") or ""
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:]
    return key.strip()


async def get_current_account(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Account:
    """FastAPI dependency that authenticates via API key.

    Accepts the key from either header:
      - X-API-Key: <key>              (curl / direct clients)
      - Authorization: Bearer <key>   (SDK cloud mode)

    1. Hash the raw key with SHA-256.
    2. Look up api_keys by key_hash.
    3. Check revoked_at IS NULL and expires_at is valid.
    4. Load the associated Account.
    5. Set current_setting('app.account_id') on the DB connection for RLS.

    Returns the Account on success.
    Raises HTTPException(401) on any failure.
    """
    raw_key = _extract_api_key(request)
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "UNAUTHORIZED", "message": "Missing API key. Provide X-API-Key or Authorization: Bearer header."},
        )

    key_hash = _hash_key(raw_key)

    stmt = (
        select(ApiKey)
        .options(selectinload(ApiKey.account))
        .where(ApiKey.key_hash == key_hash)
    )
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "UNAUTHORIZED", "message": "Invalid API key."},
        )

    # Check revoked
    if api_key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "UNAUTHORIZED", "message": "API key has been revoked."},
        )

    # Check expired
    now = datetime.now(timezone.utc)
    if api_key.expires_at is not None and api_key.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "UNAUTHORIZED", "message": "API key has expired."},
        )

    account = api_key.account
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "UNAUTHORIZED", "message": "Account not found."},
        )

    # Set RLS context on the connection
    await db.execute(text(f"SET LOCAL app.account_id = '{account.id}'"))

    return account
