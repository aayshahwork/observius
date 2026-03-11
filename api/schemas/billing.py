"""
api/schemas/billing.py — Pydantic v2 request/response models for the Billing API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CheckoutRequest(BaseModel):
    """POST /api/v1/billing/checkout request body."""

    tier: Literal["startup", "growth", "enterprise"] = Field(
        ..., description="Target subscription tier"
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class CheckoutResponse(BaseModel):
    """Stripe Checkout session URL."""

    checkout_url: str


class PortalResponse(BaseModel):
    """Stripe Customer Portal session URL."""

    portal_url: str


class UsageResponse(BaseModel):
    """Current billing usage stats."""

    monthly_steps_used: int
    monthly_step_limit: int
    tier: str
    billing_period_end: datetime | None = None


# ---------------------------------------------------------------------------
# API Key models (used by account routes)
# ---------------------------------------------------------------------------

class ApiKeyCreateRequest(BaseModel):
    """POST /api/v1/account/api-keys request body."""

    label: str | None = Field(default=None, max_length=255, description="Human-readable label")


class ApiKeyCreateResponse(BaseModel):
    """Response with raw key — shown only once."""

    id: uuid.UUID
    key: str = Field(..., description="Raw API key (only visible at creation time)")
    key_prefix: str
    key_suffix: str
    label: str | None = None
    created_at: datetime


class ApiKeyResponse(BaseModel):
    """API key metadata (raw key is never shown again)."""

    id: uuid.UUID
    key_prefix: str
    key_suffix: str
    label: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}
