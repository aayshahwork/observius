"""
workers/db.py — SQLAlchemy engines for Celery workers.

Provides both a synchronous engine (psycopg2, used by Celery task functions)
and an async engine (asyncpg, used by async code running inside asyncio.run).
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import Session, sessionmaker

from workers.config import worker_settings

_sync_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def _get_engine() -> Engine:
    """Lazily create the sync engine (avoids import-time DB connection)."""
    global _sync_engine
    if _sync_engine is None:
        sync_url = worker_settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
        _sync_engine = create_engine(
            sync_url,
            pool_size=5,
            max_overflow=3,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _sync_engine


def _get_session_factory() -> sessionmaker:
    """Lazily create the session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=_get_engine(),
            class_=Session,
            expire_on_commit=False,
        )
    return _session_factory


def get_sync_session() -> Session:
    """Return a new synchronous DB session. Caller must close it."""
    return _get_session_factory()()


# ---------------------------------------------------------------------------
# Async engine — used by SessionManager and other async worker code
# (called from within asyncio.run() in execute_task).
# ---------------------------------------------------------------------------

_async_engine: Optional[Any] = None
_async_sf: Optional[Any] = None


def _get_async_engine() -> Any:
    """Lazily create the async engine."""
    global _async_engine
    if _async_engine is None:
        import ssl
        from sqlalchemy.ext.asyncio import create_async_engine as _cae

        import re as _re
        _m = _re.search(r"@([^:/]+)", worker_settings.DATABASE_URL)
        _host = _m.group(1) if _m else ""
        _is_remote = bool(_host) and _host not in ("localhost", "127.0.0.1") and "." in _host
        if _is_remote:
            _ssl_ctx = ssl.create_default_context()
            _ssl_ctx.check_hostname = False
            _ssl_ctx.verify_mode = ssl.CERT_NONE
            _connect_args: dict = {"ssl": _ssl_ctx}
        else:
            _connect_args = {}

        _async_engine = _cae(
            worker_settings.DATABASE_URL,
            pool_size=5,
            max_overflow=3,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=_connect_args,
        )
    return _async_engine


def get_async_session_factory() -> Any:
    """Return an async session factory. Lazily initialised."""
    global _async_sf
    if _async_sf is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        _async_sf = async_sessionmaker(
            bind=_get_async_engine(),
            expire_on_commit=False,
        )
    return _async_sf
