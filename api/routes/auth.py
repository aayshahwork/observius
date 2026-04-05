"""
api/routes/auth.py — Self-serve registration endpoint.

POST /api/v1/auth/register   Create account + API key (no auth required)
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.models.account import Account
from api.models.api_key import ApiKey
from shared.constants import TIER_STEP_LIMITS

logger = structlog.get_logger("api.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = ""


class RegisterResponse(BaseModel):
    account_id: str
    email: str
    api_key: str
    tier: str
    monthly_step_limit: int


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    """Create a new account on the free tier and return an API key.

    The raw API key is returned exactly once — it is never stored.
    """
    # Check for duplicate email
    existing = await db.execute(
        select(Account.id).where(Account.email == body.email)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "DUPLICATE_EMAIL",
                "message": "An account with this email already exists.",
            },
        )

    # Create account
    account_id = uuid.uuid4()
    account = Account(
        id=account_id,
        email=body.email,
        name=body.name or body.email.split("@")[0],
        tier="free",
        monthly_step_limit=TIER_STEP_LIMITS["free"],
        monthly_steps_used=0,
        encryption_key_id=f"enc_{uuid.uuid4().hex[:16]}",
    )
    db.add(account)

    # Generate API key: cu_live_ + 32 hex chars
    raw_key = f"cu_live_{secrets.token_hex(16)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = ApiKey(
        id=uuid.uuid4(),
        account_id=account_id,
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        key_suffix=raw_key[-4:],
        label="Default key",
        created_at=datetime.utcnow(),
    )
    db.add(api_key)

    await db.commit()

    logger.info(
        "account_registered",
        account_id=str(account_id),
        email=body.email,
        ip=request.client.host if request.client else None,
    )

    return RegisterResponse(
        account_id=str(account_id),
        email=body.email,
        api_key=raw_key,
        tier="free",
        monthly_step_limit=TIER_STEP_LIMITS["free"],
    )
