"""
api/main.py — FastAPI application entrypoint for ComputerUse API.

Start the server:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from api.config import settings
from api.middleware.credential_scrubber import CredentialScrubber
from api.middleware.logging import StructuredLoggingMiddleware
from api.middleware.metrics import PrometheusMiddleware
from api.routes.account import router as account_router
from api.routes.audit import router as audit_router
from api.routes.auth import router as auth_router
from api.routes.billing import router as billing_router
from api.routes.sessions import router as sessions_router
from api.routes.alerts import router as alerts_router
from api.routes.analytics import router as analytics_router
from api.routes.tasks import router as tasks_router

# ---------------------------------------------------------------------------
# Structured logging configuration
# ---------------------------------------------------------------------------

_renderer = (
    structlog.dev.ConsoleRenderer()
    if settings.ENVIRONMENT == "development"
    else structlog.processors.JSONRenderer()
)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        CredentialScrubber(),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _renderer,
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("api")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ComputerUse API",
    description="Cloud execution API for browser automation tasks.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Middleware (order matters: last added = first executed)
# ---------------------------------------------------------------------------

_cors_origins = [
    "http://localhost:3000",
    "http://localhost:8080",
    # Production domains
    "https://pokant.live",
    "https://app.pokant.live",
    "https://api.pokant.live",
]
# Allow custom DASHBOARD_URL override (e.g. staging)
import os as _os
_vercel_url = _os.environ.get("DASHBOARD_URL", "")
if _vercel_url:
    _cors_origins.append(_vercel_url.rstrip("/"))
# Allow Vercel preview deployments
_cors_origins.append("https://pokant.vercel.app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://pokant(-[a-z0-9]+)?\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(StructuredLoggingMiddleware)
app.add_middleware(PrometheusMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth_router)
app.include_router(tasks_router)
app.include_router(analytics_router)
app.include_router(alerts_router)
app.include_router(sessions_router)
app.include_router(billing_router)
app.include_router(account_router)
app.include_router(audit_router)

# Local file serving for dev (replays/screenshots when R2 is not configured)
if settings.ENVIRONMENT == "development":
    from api.routes.local_files import router as local_files_router
    app.include_router(local_files_router)

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error_code": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
    )

# ---------------------------------------------------------------------------
# Health check (no auth) — checks DB + Redis connectivity
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Infrastructure"])
async def health_check() -> Response:
    from redis.asyncio import Redis
    from sqlalchemy import text

    from api.db.engine import async_session_factory

    db_ok = False
    redis_ok = False

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    try:
        client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await client.ping()  # type: ignore[misc]
            redis_ok = True
        finally:
            await client.aclose()
    except Exception:
        pass

    status = "ok" if (db_ok and redis_ok) else "degraded"
    status_code = 200 if (db_ok or redis_ok) else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "db": "ok" if db_ok else "unavailable",
            "redis": "ok" if redis_ok else "unavailable",
            "version": app.version,
        },
    )

# ---------------------------------------------------------------------------
# Prometheus metrics endpoint (no auth)
# ---------------------------------------------------------------------------

@app.get("/metrics", tags=["Infrastructure"], include_in_schema=False)
async def prometheus_metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
