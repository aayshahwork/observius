"""
Reset monthly_steps_used for all accounts.

Run as cron on the 1st of each month via Railway:
    python -m api.management.reset_usage
"""

from __future__ import annotations

import asyncio

from sqlalchemy import update

from api.db.engine import engine
from api.models.account import Account


async def _reset() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(update(Account).values(monthly_steps_used=0))
        print(f"Monthly usage reset complete: {result.rowcount} account(s) updated.")


if __name__ == "__main__":
    asyncio.run(_reset())
