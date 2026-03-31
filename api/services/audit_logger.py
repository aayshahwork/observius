"""
api/services/audit_logger.py — Append-only audit log for security-relevant actions.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Action constants
TASK_CREATED = "task.created"
TASK_CANCELLED = "task.cancelled"
TASK_RETRIED = "task.retried"
TASK_INGESTED = "task.ingested"
API_KEY_CREATED = "api_key.created"
API_KEY_REVOKED = "api_key.revoked"
SESSION_DELETED = "session.deleted"
TIER_UPGRADED = "tier.upgraded"
TIER_DOWNGRADED = "tier.downgraded"


class AuditLogger:
    """Append-only audit log writer. Participates in the caller's transaction."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def log(
        self,
        account_id: uuid.UUID,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Insert a single audit log entry.

        Does NOT commit — the caller's transaction covers this insert
        so the audit entry is atomic with the action it describes.
        """
        await self._db.execute(
            text(
                "INSERT INTO audit_log "
                "(account_id, actor_type, actor_id, action, resource_type, resource_id, metadata, ip_address) "
                "VALUES (:account_id, :actor_type, :actor_id, :action, :resource_type, :resource_id, "
                "CAST(:metadata AS jsonb), :ip_address)"
            ),
            {
                "account_id": str(account_id),
                "actor_type": actor_type,
                "actor_id": actor_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "metadata": json.dumps(metadata) if metadata else None,
                "ip_address": ip_address,
            },
        )
