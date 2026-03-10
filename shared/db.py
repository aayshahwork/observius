"""
shared/db.py — Async PostgreSQL database layer using asyncpg.

All public functions assume the connection pool has been initialised by
calling :func:`init_db` at application startup.  Tear it down with
:func:`close_db` on shutdown.

Typical FastAPI integration::

    @app.on_event("startup")
    async def startup():
        await init_db()

    @app.on_event("shutdown")
    async def shutdown():
        await close_db()
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import asyncpg
from asyncpg import Pool, Connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level pool — initialised by init_db(), torn down by close_db().
# ---------------------------------------------------------------------------

_pool: Optional[Pool] = None

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id         VARCHAR(255) PRIMARY KEY,
    user_api_key    VARCHAR(255) NOT NULL,
    status          VARCHAR(50)  NOT NULL DEFAULT 'pending',
    success         BOOLEAN,
    request         JSONB        NOT NULL,
    result          JSONB,
    error           TEXT,
    replay_url      TEXT,
    replay_path     TEXT,
    steps           INTEGER      NOT NULL DEFAULT 0,
    duration_ms     INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at    TIMESTAMP WITH TIME ZONE,
    celery_task_id  VARCHAR(255)
);
"""

_CREATE_TASKS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_tasks_user_created
    ON tasks (user_api_key, created_at DESC);
"""

_CREATE_API_KEYS = """
CREATE TABLE IF NOT EXISTS api_keys (
    api_key      VARCHAR(255) PRIMARY KEY,
    user_id      VARCHAR(255) NOT NULL,
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    last_used_at TIMESTAMP WITH TIME ZONE,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE
);
"""

_CREATE_API_KEYS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_api_keys_user
    ON api_keys (user_id);
"""

# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

async def init_db(dsn: Optional[str] = None) -> None:
    """Initialise the connection pool and create all tables.

    Should be called once at application startup before any other database
    function is used.

    Args:
        dsn: PostgreSQL DSN string.  Falls back to the ``DATABASE_URL``
             environment variable, then to a local default.

    Raises:
        :exc:`asyncpg.PostgresError`: If the database is unreachable or the
            schema creation fails.
    """
    global _pool

    if _pool is not None:
        logger.debug("init_db called but pool already exists — skipping")
        return

    resolved_dsn = dsn or os.environ.get(
        "DATABASE_URL", "postgresql://localhost/computeruse"
    )

    logger.info("Connecting to database…")
    _pool = await asyncpg.create_pool(
        dsn=resolved_dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
        # Return dicts instead of asyncpg Record objects for easier JSON
        # serialisation downstream.
        init=_set_json_codec,
    )

    async with _acquire() as conn:
        await conn.execute(_CREATE_TASKS)
        await conn.execute(_CREATE_TASKS_INDEX)
        await conn.execute(_CREATE_API_KEYS)
        await conn.execute(_CREATE_API_KEYS_INDEX)

    logger.info("Database initialised successfully")


async def close_db() -> None:
    """Gracefully close the connection pool.

    Safe to call even if :func:`init_db` was never called.
    """
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None
    logger.info("Database pool closed")


# ---------------------------------------------------------------------------
# Task operations
# ---------------------------------------------------------------------------

async def save_task(task_data: Dict[str, Any]) -> None:
    """Insert a new task row.

    Args:
        task_data: Dict containing at minimum ``task_id``, ``user_api_key``,
                   ``status``, ``request``, and ``created_at``.

    Raises:
        :exc:`asyncpg.UniqueViolationError`: If ``task_id`` already exists.
        :exc:`RuntimeError`: If the pool has not been initialised.
    """
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tasks (
                task_id, user_api_key, status, success,
                request, result, error,
                replay_url, replay_path,
                steps, duration_ms,
                created_at, completed_at, celery_task_id
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, $9,
                $10, $11,
                $12, $13, $14
            )
            """,
            task_data["task_id"],
            task_data["user_api_key"],
            task_data.get("status", "pending"),
            task_data.get("success"),
            json.dumps(task_data.get("request", {})),
            json.dumps(task_data["result"]) if task_data.get("result") else None,
            task_data.get("error"),
            task_data.get("replay_url"),
            task_data.get("replay_path"),
            task_data.get("steps", 0),
            task_data.get("duration_ms", 0),
            task_data.get("created_at", datetime.now(timezone.utc)),
            task_data.get("completed_at"),
            task_data.get("celery_task_id"),
        )
    logger.debug("Saved task %s", task_data["task_id"])


async def update_task(task_id: str, updates: Dict[str, Any]) -> None:
    """Update arbitrary fields on an existing task row.

    Builds a dynamic ``SET`` clause from the *updates* dict keys so callers
    don't need to construct SQL themselves.  JSONB fields (``result``,
    ``request``) are serialised automatically.

    Args:
        task_id: Primary key of the task to update.
        updates: Mapping of column names to new values.  Only columns that
                 exist in the schema should be included.

    Raises:
        :exc:`RuntimeError`: If the pool has not been initialised.
    """
    if not updates:
        return

    # Columns that must be JSON-serialised before binding.
    _json_cols = {"result", "request"}

    params: list[Any] = []
    clauses: list[str] = []

    for col, value in updates.items():
        params.append(
            json.dumps(value) if col in _json_cols and value is not None else value
        )
        clauses.append(f"{col} = ${len(params)}")

    params.append(task_id)
    sql = f"UPDATE tasks SET {', '.join(clauses)} WHERE task_id = ${len(params)}"

    async with _acquire() as conn:
        result = await conn.execute(sql, *params)

    updated = int(result.split()[-1])  # "UPDATE <n>"
    if updated == 0:
        logger.warning("update_task: no row found for task_id=%s", task_id)
    else:
        logger.debug("Updated task %s: %s", task_id, list(updates.keys()))


async def get_task(task_id: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Fetch a single task, verifying ownership.

    Args:
        task_id: The task's primary key.
        api_key: The caller's API key.  The row is only returned if
                 ``user_api_key`` matches this value.

    Returns:
        A dict representation of the task row, or ``None`` if not found or
        the API key doesn't match.

    Raises:
        :exc:`RuntimeError`: If the pool has not been initialised.
    """
    async with _acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                task_id, user_api_key, status, success,
                request, result, error,
                replay_url, replay_path,
                steps, duration_ms,
                created_at, completed_at, celery_task_id
            FROM tasks
            WHERE task_id = $1
              AND user_api_key = $2
            """,
            task_id,
            api_key,
        )

    if row is None:
        return None

    return _deserialise_row(dict(row))


async def list_tasks(
    api_key: str,
    limit: int = 10,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return a paginated list of tasks belonging to *api_key*, newest first.

    Args:
        api_key: Filter to tasks owned by this key.
        limit:   Maximum number of rows to return.
        offset:  Number of rows to skip (for pagination).

    Returns:
        List of task dicts ordered by ``created_at DESC``.

    Raises:
        :exc:`RuntimeError`: If the pool has not been initialised.
    """
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                task_id, user_api_key, status, success,
                request, result, error,
                replay_url, replay_path,
                steps, duration_ms,
                created_at, completed_at, celery_task_id
            FROM tasks
            WHERE user_api_key = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            api_key,
            limit,
            offset,
        )

    return [_deserialise_row(dict(row)) for row in rows]


async def count_tasks(api_key: str) -> int:
    """Return the total number of tasks owned by *api_key*.

    Useful for building paginated list responses without a second round-trip
    if the count is fetched before calling :func:`list_tasks`.

    Args:
        api_key: The owner's API key.

    Returns:
        Integer row count.
    """
    async with _acquire() as conn:
        result = await conn.fetchval(
            "SELECT COUNT(*) FROM tasks WHERE user_api_key = $1",
            api_key,
        )
    return int(result or 0)


async def delete_task(task_id: str, api_key: str) -> bool:
    """Hard-delete a task row after verifying ownership.

    Args:
        task_id: The task to delete.
        api_key: Must match ``user_api_key`` on the row.

    Returns:
        ``True`` if the row was deleted, ``False`` if not found or not owned.
    """
    async with _acquire() as conn:
        result = await conn.execute(
            "DELETE FROM tasks WHERE task_id = $1 AND user_api_key = $2",
            task_id,
            api_key,
        )
    deleted = int(result.split()[-1])
    return deleted > 0


# ---------------------------------------------------------------------------
# API key operations
# ---------------------------------------------------------------------------

async def verify_api_key(api_key: str) -> bool:
    """Check whether *api_key* exists and is active.

    Also updates ``last_used_at`` in the same round-trip so usage can be
    tracked without a separate UPDATE.

    Args:
        api_key: The key to verify.

    Returns:
        ``True`` if the key exists and ``is_active`` is ``TRUE``.

    Raises:
        :exc:`RuntimeError`: If the pool has not been initialised.
    """
    async with _acquire() as conn:
        is_active = await conn.fetchval(
            """
            UPDATE api_keys
            SET last_used_at = NOW()
            WHERE api_key = $1 AND is_active = TRUE
            RETURNING is_active
            """,
            api_key,
        )
    return is_active is True


async def create_api_key(api_key: str, user_id: str) -> None:
    """Insert a new API key record.

    Args:
        api_key: The key string to store (should already be hashed in
                 production — store a HMAC, not the raw key).
        user_id: The owning user's identifier.

    Raises:
        :exc:`asyncpg.UniqueViolationError`: If *api_key* already exists.
    """
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO api_keys (api_key, user_id, created_at, is_active)
            VALUES ($1, $2, NOW(), TRUE)
            """,
            api_key,
            user_id,
        )
    logger.info("API key created for user %s", user_id)


async def deactivate_api_key(api_key: str) -> bool:
    """Mark an API key as inactive (soft-delete).

    Args:
        api_key: The key to deactivate.

    Returns:
        ``True`` if the key was found and deactivated, ``False`` otherwise.
    """
    async with _acquire() as conn:
        result = await conn.execute(
            "UPDATE api_keys SET is_active = FALSE WHERE api_key = $1",
            api_key,
        )
    return int(result.split()[-1]) > 0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _acquire() -> AsyncIterator[Connection]:
    """Yield a connection from the pool, raising clearly if not initialised."""
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Call await init_db() at application startup."
        )
    async with _pool.acquire() as conn:
        yield conn


async def _set_json_codec(conn: Connection) -> None:
    """Register custom JSON codecs on a new connection.

    asyncpg doesn't decode JSONB columns automatically; this init hook
    teaches it to use Python's ``json`` module for both encoding and
    decoding so JSONB columns are returned as dicts/lists rather than
    raw strings.
    """
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


def _deserialise_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a raw DB row for consumption by the API layer.

    * JSONB columns (``request``, ``result``) arrive as dicts thanks to the
      codec registered in :func:`_set_json_codec`.
    * ``datetime`` objects are converted to ISO-8601 strings so the dict is
      directly JSON-serialisable without a custom encoder.

    Args:
        row: Raw dict produced by ``dict(asyncpg.Record)``.

    Returns:
        Normalised dict safe to pass directly into a Pydantic model or
        ``json.dumps``.
    """
    for key, value in row.items():
        if isinstance(value, datetime):
            row[key] = value.isoformat()
    return row
