"""
api/services/usage_tracker.py — Atomic usage metering for step-based billing.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.account import Account


class UsageTracker:
    """Tracks per-account step usage with atomic DB updates."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def try_increment_steps(
        self, account_id: uuid.UUID, step_count: int
    ) -> tuple[bool, int]:
        """Atomically increment steps only if quota allows.

        Returns (allowed, new_total).  If the increment would exceed the
        monthly limit the UPDATE is skipped and (False, current_total) is
        returned — no partial write occurs.
        """
        result = await self._db.execute(
            text(
                "UPDATE accounts "
                "SET monthly_steps_used = monthly_steps_used + :step_count "
                "WHERE id = :account_id "
                "  AND monthly_steps_used + :step_count <= monthly_step_limit "
                "RETURNING monthly_steps_used"
            ),
            {"step_count": step_count, "account_id": str(account_id)},
        )
        row = result.one_or_none()

        if row is not None:
            await self._db.commit()
            return True, row[0]

        # Quota would be exceeded — fetch current total for the caller.
        current = await self._db.execute(
            text(
                "SELECT monthly_steps_used FROM accounts WHERE id = :account_id"
            ),
            {"account_id": str(account_id)},
        )
        current_row = current.one_or_none()
        await self._db.rollback()
        return False, current_row[0] if current_row else 0

    async def check_quota(self, account_id: uuid.UUID) -> bool:
        """Return True if the account has remaining quota."""
        stmt = select(
            Account.monthly_steps_used, Account.monthly_step_limit
        ).where(Account.id == account_id)
        result = await self._db.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return False
        return row.monthly_steps_used < row.monthly_step_limit

    async def get_usage(self, account_id: uuid.UUID) -> dict:
        """Return current usage stats for the account."""
        stmt = select(
            Account.monthly_steps_used,
            Account.monthly_step_limit,
            Account.tier,
        ).where(Account.id == account_id)
        result = await self._db.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return {
                "monthly_steps_used": 0,
                "monthly_step_limit": 0,
                "tier": "free",
                "billing_period_end": None,
            }
        return {
            "monthly_steps_used": row.monthly_steps_used,
            "monthly_step_limit": row.monthly_step_limit,
            "tier": row.tier or "free",
            "billing_period_end": None,  # TODO: wire up Stripe billing period
        }
