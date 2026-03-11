"""
api/routes/account.py — Account management endpoints (API key CRUD).

GET    /api/v1/account/api-keys          List API keys
POST   /api/v1/account/api-keys          Create a new API key
DELETE /api/v1/account/api-keys/{key_id} Revoke an API key
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.middleware.auth import get_current_account
from api.models.account import Account
from api.models.api_key import ApiKey
from api.schemas.billing import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyResponse

logger = structlog.get_logger("api.account")

router = APIRouter(prefix="/api/v1/account", tags=["Account"])

# Base62 alphabet for key encoding
_BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _base62_encode(data: bytes) -> str:
    """Encode bytes to a base62 string."""
    num = int.from_bytes(data, "big")
    if num == 0:
        return _BASE62[0]
    chars = []
    while num > 0:
        num, remainder = divmod(num, 62)
        chars.append(_BASE62[remainder])
    return "".join(reversed(chars))


def _generate_api_key() -> str:
    """Generate a new API key: cu_prod_<base62(32 random bytes)>."""
    raw_bytes = secrets.token_bytes(32)
    encoded = _base62_encode(raw_bytes)
    return f"cu_prod_{encoded}"


# ---------------------------------------------------------------------------
# GET /api/v1/account/api-keys
# ---------------------------------------------------------------------------

@router.get(
    "/api-keys",
    response_model=list[ApiKeyResponse],
    status_code=status.HTTP_200_OK,
)
async def list_api_keys(
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyResponse]:
    """List all API keys for the authenticated account."""
    stmt = (
        select(ApiKey)
        .where(ApiKey.account_id == account.id)
        .order_by(ApiKey.created_at.desc())
    )
    result = await db.execute(stmt)
    keys = result.scalars().all()
    return [ApiKeyResponse.model_validate(k) for k in keys]


# ---------------------------------------------------------------------------
# POST /api/v1/account/api-keys
# ---------------------------------------------------------------------------

@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    body: ApiKeyCreateRequest,
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreateResponse:
    """Create a new API key. The raw key is returned only once."""
    raw_key = _generate_api_key()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = ApiKey(
        id=uuid.uuid4(),
        account_id=account.id,
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        key_suffix=raw_key[-4:],
        label=body.label,
        created_at=datetime.now(timezone.utc),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    logger.info(
        "api_key_created",
        account_id=str(account.id),
        key_id=str(api_key.id),
        key_prefix=api_key.key_prefix,
    )

    return ApiKeyCreateResponse(
        id=api_key.id,
        key=raw_key,
        key_prefix=api_key.key_prefix,
        key_suffix=api_key.key_suffix,
        label=api_key.label,
        created_at=api_key.created_at,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/account/api-keys/{key_id}
# ---------------------------------------------------------------------------

@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_200_OK,
)
async def revoke_api_key(
    key_id: uuid.UUID,
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke an API key by setting revoked_at."""
    stmt = select(ApiKey).where(
        ApiKey.id == key_id, ApiKey.account_id == account.id
    )
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "NOT_FOUND", "message": "API key not found."},
        )

    if api_key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "INVALID_STATE",
                "message": "API key is already revoked.",
            },
        )

    api_key.revoked_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(
        "api_key_revoked",
        account_id=str(account.id),
        key_id=str(key_id),
    )

    return {"key_id": str(key_id), "status": "revoked"}
