"""workers/memory/store.py — Async PostgreSQL-backed memory store.

Follows the same asyncpg pool pattern as shared/db.py:
- __init__ stores the DSN; pool is created by calling await init()
- _acquire() context manager raises clearly if init() was not called
- JSONB columns decoded automatically via _set_json_codec
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Optional

import asyncpg
from asyncpg import Connection, Pool

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    scope: str          # "agent" | "tenant" | "user"
    scope_id: str       # tenant_id or user_id
    key: str            # e.g. "site:amazon.com:login_flow:v2"
    content: dict
    provenance: dict = field(default_factory=dict)
    safety_label: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class MemoryStore:
    """Async CRUD interface over the ``memory_entries`` table."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._pool: Optional[Pool] = None

    async def init(self) -> None:
        """Create the connection pool. Must be called before any other method."""
        if self._pool is not None:
            return
        # asyncpg requires postgresql:// not SQLAlchemy's postgresql+asyncpg://
        dsn = self._db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgres+asyncpg://", "postgres://"
        )
        self._pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=5,
            command_timeout=30,
            init=_set_json_codec,
        )
        logger.info("MemoryStore pool initialised")

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def get(self, scope: str, scope_id: str, key: str) -> MemoryEntry | None:
        """Return the entry for (scope, scope_id, key), or None if absent."""
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT scope, scope_id, key, content, provenance,
                       safety_label, created_at, last_used_at
                FROM memory_entries
                WHERE scope = $1 AND scope_id = $2 AND key = $3
                """,
                scope, scope_id, key,
            )
        if row is None:
            return None
        return _row_to_entry(dict(row))

    async def put(self, entry: MemoryEntry) -> None:
        """Upsert an entry. On conflict, update content, provenance, and last_used_at."""
        async with self._acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_entries
                    (scope, scope_id, key, content, provenance, safety_label)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (scope, scope_id, key) DO UPDATE SET
                    content      = EXCLUDED.content,
                    provenance   = EXCLUDED.provenance,
                    safety_label = EXCLUDED.safety_label,
                    last_used_at = now()
                """,
                entry.scope,
                entry.scope_id,
                entry.key,
                json.dumps(entry.content),
                json.dumps(entry.provenance),
                entry.safety_label,
            )

    async def query(
        self, scope: str, scope_id: str, key_prefix: str
    ) -> list[MemoryEntry]:
        """Return all entries whose key starts with key_prefix."""
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT scope, scope_id, key, content, provenance,
                       safety_label, created_at, last_used_at
                FROM memory_entries
                WHERE scope = $1 AND scope_id = $2 AND key LIKE $3
                ORDER BY last_used_at DESC
                """,
                scope, scope_id, key_prefix + "%",
            )
        return [_row_to_entry(dict(r)) for r in rows]

    async def touch(self, scope: str, scope_id: str, key: str) -> None:
        """Update last_used_at to now() for the given entry."""
        async with self._acquire() as conn:
            await conn.execute(
                """
                UPDATE memory_entries
                SET last_used_at = now()
                WHERE scope = $1 AND scope_id = $2 AND key = $3
                """,
                scope, scope_id, key,
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[Connection]:
        if self._pool is None:
            raise RuntimeError(
                "MemoryStore pool is not initialised. Call await store.init() first."
            )
        async with self._pool.acquire() as conn:
            yield conn


# ---------------------------------------------------------------------------
# Module-level helpers (mirrors shared/db.py)
# ---------------------------------------------------------------------------

async def _set_json_codec(conn: Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


def _row_to_entry(row: dict) -> MemoryEntry:
    return MemoryEntry(
        scope=row["scope"],
        scope_id=row["scope_id"],
        key=row["key"],
        content=row["content"],
        provenance=row.get("provenance") or {},
        safety_label=row.get("safety_label"),
        created_at=row.get("created_at"),
        last_used_at=row.get("last_used_at"),
    )
