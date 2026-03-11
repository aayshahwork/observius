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

from api.middleware.credential_scrubber import CredentialScrubber
from api.middleware.logging import StructuredLoggingMiddleware
from api.routes.account import router as account_router
from api.routes.billing import router as billing_router
from api.routes.sessions import router as sessions_router
from api.routes.tasks import router as tasks_router

# ---------------------------------------------------------------------------
# Structured logging configuration
# ---------------------------------------------------------------------------

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
        structlog.dev.ConsoleRenderer(),
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8080",
        "https://app.computeruse.dev",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(StructuredLoggingMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(tasks_router)
app.include_router(sessions_router)
app.include_router(billing_router)
app.include_router(account_router)

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
# Health check (no auth)
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Infrastructure"])
async def health_check() -> dict:
    return {"status": "ok"}
