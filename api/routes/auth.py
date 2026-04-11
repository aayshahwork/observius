"""
api/routes/auth.py — Registration and login endpoints.

POST /auth/register   Create account with email + password
POST /auth/login      Authenticate and return a fresh API key
"""

from __future__ import annotations

import hashlib
import hmac
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

router = APIRouter(prefix="/auth", tags=["Auth"])


# ---------------------------------------------------------------------------
# Password helpers (PBKDF2-SHA256, 600k iterations)
# ---------------------------------------------------------------------------

_ITERATIONS = 600_000


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${key.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, iter_str, salt, stored_hex = stored.split("$", 3)
        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iter_str)
        )
        return hmac.compare_digest(key.hex(), stored_hex)
    except Exception:
        return False


def _make_api_key(account_id: uuid.UUID, label: str) -> tuple[str, ApiKey]:
    """Generate a new API key. Returns (raw_key, ApiKey model)."""
    raw_key = f"cu_live_{secrets.token_hex(16)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = ApiKey(
        id=uuid.uuid4(),
        account_id=account_id,
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        key_suffix=raw_key[-4:],
        label=label,
        created_at=datetime.utcnow(),
    )
    return raw_key, api_key


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(..., min_length=8)
    name: str = ""


class RegisterResponse(BaseModel):
    account_id: str
    email: str
    api_key: str
    tier: str
    monthly_step_limit: int


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    account_id: str
    email: str
    api_key: str
    tier: str
    monthly_step_limit: int


# ---------------------------------------------------------------------------
# POST /auth/register
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
    """Create a new account on the free tier and return an API key (shown once)."""
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

    account_id = uuid.uuid4()
    account = Account(
        id=account_id,
        email=body.email,
        name=body.name or body.email.split("@")[0],
        tier="free",
        monthly_step_limit=TIER_STEP_LIMITS["free"],
        monthly_steps_used=0,
        encryption_key_id=f"enc_{uuid.uuid4().hex[:16]}",
        password_hash=_hash_password(body.password),
    )
    db.add(account)

    raw_key, api_key = _make_api_key(account_id, "Default key")
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


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Authenticate with email + password and receive a fresh API key."""
    result = await db.execute(
        select(Account).where(Account.email == body.email)
    )
    account = result.scalar_one_or_none()

    # Always run verify to avoid timing-based email enumeration
    dummy_hash = f"pbkdf2_sha256${_ITERATIONS}${'0' * 32}${'0' * 64}"
    stored_hash = account.password_hash if (account and account.password_hash) else dummy_hash
    valid = _verify_password(body.password, stored_hash)

    if not account or not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "INVALID_CREDENTIALS",
                "message": "Invalid email or password.",
            },
        )

    raw_key, api_key = _make_api_key(account.id, "Dashboard login")
    db.add(api_key)
    await db.commit()

    logger.info(
        "account_login",
        account_id=str(account.id),
        email=account.email,
        ip=request.client.host if request.client else None,
    )

    return LoginResponse(
        account_id=str(account.id),
        email=account.email,
        api_key=raw_key,
        tier=account.tier or "free",
        monthly_step_limit=account.monthly_step_limit,
    )
