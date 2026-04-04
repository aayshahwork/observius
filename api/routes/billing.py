"""
api/routes/billing.py — Stripe billing endpoints.

POST  /api/v1/billing/checkout   Create Stripe Checkout session
POST  /api/v1/billing/webhook    Stripe webhook handler (no auth)
GET   /api/v1/billing/usage      Current usage stats
POST  /api/v1/billing/portal     Create Stripe Customer Portal session
"""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.dependencies import get_db
from api.middleware.auth import get_current_account
from api.models.account import Account
from api.schemas.billing import (
    CheckoutRequest,
    CheckoutResponse,
    PortalResponse,
    UsageResponse,
)
from api.services.audit_logger import TIER_DOWNGRADED, TIER_UPGRADED, AuditLogger
from api.services.usage_tracker import UsageTracker
from shared.constants import TIER_STEP_LIMITS

logger = structlog.get_logger("api.billing")

router = APIRouter(prefix="/api/v1/billing", tags=["Billing"])


# ---------------------------------------------------------------------------
# POST /api/v1/billing/checkout
# ---------------------------------------------------------------------------

@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_200_OK,
)
async def create_checkout(
    body: CheckoutRequest,
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    """Create a Stripe Checkout session for tier upgrade."""
    if account.tier == body.tier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INVALID_INPUT",
                "message": f"Already on the {body.tier} tier.",
            },
        )

    # --- Dev mode: Stripe not configured, apply tier change directly ---
    if not settings.STRIPE_SECRET_KEY:
        new_limit = TIER_STEP_LIMITS.get(body.tier, TIER_STEP_LIMITS["free"])
        stmt = (
            update(Account)
            .where(Account.id == account.id)
            .values(tier=body.tier, monthly_step_limit=new_limit)
        )
        await db.execute(stmt)
        await db.commit()

        logger.info(
            "checkout_dev_mode",
            account_id=str(account.id),
            new_tier=body.tier,
        )
        return CheckoutResponse(
            checkout_url=f"/settings?checkout=success&tier={body.tier}"
        )

    # --- Production: create real Stripe Checkout session ---
    price_id = settings.STRIPE_PRICE_IDS.get(body.tier)
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INVALID_INPUT",
                "message": f"No price configured for tier '{body.tier}'.",
            },
        )

    # TODO: wire up stripe
    # session = stripe.checkout.Session.create(
    #     customer=account.stripe_customer_id,
    #     mode="subscription",
    #     line_items=[{"price": price_id, "quantity": 1}],
    #     success_url="https://app.pokant.dev/settings?checkout=success",
    #     cancel_url="https://app.pokant.dev/settings?checkout=cancel",
    #     metadata={"account_id": str(account.id), "tier": body.tier},
    # )
    checkout_url = f"https://checkout.stripe.com/stub/{body.tier}"

    logger.info(
        "checkout_created",
        account_id=str(account.id),
        target_tier=body.tier,
    )

    return CheckoutResponse(checkout_url=checkout_url)


# ---------------------------------------------------------------------------
# POST /api/v1/billing/webhook
# ---------------------------------------------------------------------------

@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Handle Stripe webhook events.

    Uses raw request body (not Pydantic) because Stripe signature
    verification is computed over the raw bytes.
    """
    payload = await request.body()

    # TODO: wire up stripe
    # try:
    #     event = stripe.Webhook.construct_event(
    #         payload, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
    #     )
    # except stripe.error.SignatureVerificationError:
    #     raise HTTPException(status_code=400, detail="Invalid signature")
    _ = stripe_signature  # will be used for verification

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "INVALID_INPUT", "message": "Invalid JSON payload."},
        )

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(db, data)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, data)
    elif event_type == "invoice.paid":
        await _handle_invoice_paid(db, data)
    else:
        logger.debug("webhook_ignored", event_type=event_type)

    return {"received": True}


async def _handle_checkout_completed(db: AsyncSession, data: dict) -> None:
    """Upgrade account tier after successful checkout."""
    customer_id = data.get("customer")
    metadata = data.get("metadata", {})
    new_tier = metadata.get("tier")

    if not customer_id or not new_tier:
        logger.warning("checkout_completed_missing_data", data=data)
        return

    new_limit = TIER_STEP_LIMITS.get(new_tier, TIER_STEP_LIMITS["free"])

    stmt = (
        update(Account)
        .where(Account.stripe_customer_id == customer_id)
        .values(tier=new_tier, monthly_step_limit=new_limit)
        .returning(Account.id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()

    if row:
        await AuditLogger(db).log(
            account_id=row[0],
            actor_type="webhook",
            actor_id="stripe",
            action=TIER_UPGRADED,
            resource_type="account",
            resource_id=str(row[0]),
            metadata={"new_tier": new_tier, "customer_id": customer_id},
        )

    await db.commit()

    logger.info(
        "tier_upgraded",
        customer_id=customer_id,
        new_tier=new_tier,
        rows_affected=1 if row else 0,
    )


async def _handle_subscription_deleted(db: AsyncSession, data: dict) -> None:
    """Downgrade account to free tier when subscription is cancelled."""
    customer_id = data.get("customer")
    if not customer_id:
        logger.warning("subscription_deleted_missing_customer", data=data)
        return

    free_limit = TIER_STEP_LIMITS["free"]

    stmt = (
        update(Account)
        .where(Account.stripe_customer_id == customer_id)
        .values(tier="free", monthly_step_limit=free_limit)
        .returning(Account.id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()

    if row:
        await AuditLogger(db).log(
            account_id=row[0],
            actor_type="webhook",
            actor_id="stripe",
            action=TIER_DOWNGRADED,
            resource_type="account",
            resource_id=str(row[0]),
            metadata={"customer_id": customer_id},
        )

    await db.commit()

    logger.info(
        "tier_downgraded",
        customer_id=customer_id,
        rows_affected=1 if row else 0,
    )


async def _handle_invoice_paid(db: AsyncSession, data: dict) -> None:
    """Reset monthly usage when invoice is paid (new billing cycle)."""
    customer_id = data.get("customer")
    if not customer_id:
        logger.warning("invoice_paid_missing_customer", data=data)
        return

    stmt = (
        update(Account)
        .where(Account.stripe_customer_id == customer_id)
        .values(monthly_steps_used=0)
    )
    await db.execute(stmt)
    await db.commit()

    logger.info("usage_reset", customer_id=customer_id)


# ---------------------------------------------------------------------------
# GET /api/v1/billing/usage
# ---------------------------------------------------------------------------

@router.get(
    "/usage",
    response_model=UsageResponse,
    status_code=status.HTTP_200_OK,
)
async def get_usage(
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    """Return current usage stats for the authenticated account."""
    tracker = UsageTracker(db)
    usage = await tracker.get_usage(account.id)
    return UsageResponse(**usage)


# ---------------------------------------------------------------------------
# POST /api/v1/billing/portal
# ---------------------------------------------------------------------------

@router.post(
    "/portal",
    response_model=PortalResponse,
    status_code=status.HTTP_200_OK,
)
async def create_portal(
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> PortalResponse:
    """Create a Stripe Customer Portal session."""
    # --- Dev mode: Stripe not configured, return stub ---
    if not settings.STRIPE_SECRET_KEY:
        logger.info("portal_dev_mode", account_id=str(account.id))
        return PortalResponse(portal_url="/settings?portal=dev")

    # --- Production ---
    if not account.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INVALID_INPUT",
                "message": "No Stripe customer associated with this account.",
            },
        )

    # TODO: wire up stripe
    # session = stripe.billing_portal.Session.create(
    #     customer=account.stripe_customer_id,
    #     return_url="https://app.pokant.dev/settings",
    # )
    portal_url = f"https://billing.stripe.com/stub/{account.stripe_customer_id}"

    logger.info("portal_created", account_id=str(account.id))

    return PortalResponse(portal_url=portal_url)
