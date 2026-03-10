"""
api/main.py — FastAPI cloud API for the ComputerUse service.

Start the server:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ComputerUse API",
    description="Cloud execution API for browser automation tasks.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS
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

# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Log every inbound request and its response status + duration."""
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "%s %s → %d  (%dms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a structured JSON error for any unhandled exception."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. Please try again.",
        },
    )

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class TaskRequest(BaseModel):
    """Payload accepted by POST /api/v1/tasks."""

    url: str = Field(..., description="Starting URL for the browser automation task")
    task: str = Field(..., min_length=1, description="Plain-English task description")
    credentials: Optional[Dict[str, str]] = Field(
        default=None,
        description="Login credentials to inject (e.g. username, password)",
    )
    output_schema: Optional[Dict[str, str]] = Field(
        default=None,
        description='Expected output shape, e.g. {"price": "float"}',
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="URL to POST the TaskResponse to when the task reaches a terminal state",
    )
    max_steps: int = Field(default=50, ge=1, le=200)
    timeout_seconds: int = Field(default=300, ge=10, le=3600)


class TaskResponse(BaseModel):
    """Shape of every task-related API response."""

    task_id: str
    status: Literal["pending", "running", "completed", "failed"]
    success: bool = False
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    replay_url: Optional[str] = None
    steps: int = 0
    duration_ms: int = 0
    created_at: datetime
    completed_at: Optional[datetime] = None
    webhook_url: Optional[str] = None


class TaskListResponse(BaseModel):
    """Paginated list of tasks returned by GET /api/v1/tasks."""

    tasks: List[TaskResponse]
    total: int
    limit: int
    offset: int


class DeleteResponse(BaseModel):
    """Confirmation payload for DELETE /api/v1/tasks/{task_id}."""

    task_id: str
    message: str


# ---------------------------------------------------------------------------
# In-memory task store
# (Replace with a real database via shared/db.py in production)
# ---------------------------------------------------------------------------

# Maps task_id → {"task": TaskResponse, "api_key": str}
_task_store: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """Dependency that validates the X-API-Key header.

    Rules:
      - Must be present (enforced by FastAPI via the Header default).
      - Must be at least 16 characters (guards against obviously invalid keys).
      - Must start with ``"cu-"`` (the ComputerUse key prefix convention).

    Returns the validated key on success so downstream handlers can use it
    for ownership checks.

    Raises:
        HTTPException 401: If the key is missing, malformed, or invalid.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_api_key",
                "message": "An X-API-Key header is required.",
            },
        )

    if not x_api_key.startswith("cu-") or len(x_api_key) < 16:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_api_key",
                "message": (
                    "The provided API key is not valid. "
                    "Keys must start with 'cu-' and be at least 16 characters."
                ),
            },
        )

    # TODO: validate against the real key store (shared/db.py).
    # For now any well-formed key is accepted so local development works
    # without a database.

    return x_api_key


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    summary="Health check",
    tags=["Infrastructure"],
    response_model=Dict[str, str],
)
async def health_check() -> Dict[str, str]:
    """Return ``{"status": "ok"}`` to confirm the service is reachable.

    This endpoint is intentionally unauthenticated so load balancers and
    container orchestrators can probe it without credentials.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/v1/tasks
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/tasks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskResponse,
    summary="Create a new task",
    tags=["Tasks"],
)
async def create_task(
    body: TaskRequest,
    api_key: str = Depends(verify_api_key),
) -> TaskResponse:
    """Accept a task definition, queue it for execution, and return immediately.

    The response will have ``status="pending"``.  Poll
    ``GET /api/v1/tasks/{task_id}`` or supply a ``webhook_url`` to receive
    the final result.

    Raises:
        400: If the request body fails validation (handled by FastAPI).
        401: If the API key is missing or invalid.
        500: On unexpected server errors.
    """
    task_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)

    task = TaskResponse(
        task_id=task_id,
        status="pending",
        success=False,
        created_at=created_at,
        webhook_url=body.webhook_url,
    )

    # Persist to the in-memory store (swap for DB write in production).
    _task_store[task_id] = {"task": task, "api_key": api_key}

    # Queue the task for async execution.
    # In production this dispatches to Celery (backend/tasks.py):
    #   from workers.tasks import execute_task
    #   execute_task.delay(task_id, body.model_dump(), api_key)
    logger.info("Task %s queued for API key %s…", task_id, api_key[:8])

    return task


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/{task_id}
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/tasks/{task_id}",
    response_model=TaskResponse,
    summary="Get task status and result",
    tags=["Tasks"],
)
async def get_task(
    task_id: str,
    api_key: str = Depends(verify_api_key),
) -> TaskResponse:
    """Return the current status and result of a task.

    Raises:
        401: If the API key is missing or invalid.
        403: If the task belongs to a different API key.
        404: If no task with the given ID exists.
    """
    record = _task_store.get(task_id)

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "task_not_found",
                "message": f"No task with id '{task_id}' was found.",
            },
        )

    if record["api_key"] != api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "message": "You do not have access to this task.",
            },
        )

    return record["task"]


# ---------------------------------------------------------------------------
# GET /api/v1/tasks
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/tasks",
    response_model=TaskListResponse,
    summary="List tasks",
    tags=["Tasks"],
)
async def list_tasks(
    limit: int = Query(default=10, ge=1, le=100, description="Maximum results to return"),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),
    api_key: str = Depends(verify_api_key),
) -> TaskListResponse:
    """Return a paginated list of tasks belonging to the authenticated API key.

    Results are ordered newest-first by ``created_at``.

    Raises:
        401: If the API key is missing or invalid.
    """
    # Filter to tasks owned by this key, newest first.
    owned: List[TaskResponse] = [
        record["task"]
        for record in _task_store.values()
        if record["api_key"] == api_key
    ]
    owned.sort(key=lambda t: t.created_at, reverse=True)

    total = len(owned)
    page = owned[offset : offset + limit]

    return TaskListResponse(
        tasks=page,
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/tasks/{task_id}
# ---------------------------------------------------------------------------

@app.delete(
    "/api/v1/tasks/{task_id}",
    response_model=DeleteResponse,
    summary="Cancel or delete a task",
    tags=["Tasks"],
)
async def delete_task(
    task_id: str,
    api_key: str = Depends(verify_api_key),
) -> DeleteResponse:
    """Cancel a running task or remove a completed one.

    * If the task is ``pending`` or ``running`` it will be revoked in the
      Celery queue before being removed.
    * Completed and failed tasks are removed from the store immediately.

    Raises:
        401: If the API key is missing or invalid.
        403: If the task belongs to a different API key.
        404: If no task with the given ID exists.
    """
    record = _task_store.get(task_id)

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "task_not_found",
                "message": f"No task with id '{task_id}' was found.",
            },
        )

    if record["api_key"] != api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "message": "You do not have access to this task.",
            },
        )

    task: TaskResponse = record["task"]
    if task.status in ("pending", "running"):
        # Revoke the Celery task if it hasn't started yet.
        # In production:
        #   from workers.tasks import celery_app
        #   celery_app.control.revoke(task_id, terminate=True)
        logger.info("Revoking queued task %s", task_id)
        task.status = "failed"
        task.error = "Cancelled by user"
        task.completed_at = datetime.now(timezone.utc)

    del _task_store[task_id]
    logger.info("Task %s deleted by API key %s…", task_id, api_key[:8])

    return DeleteResponse(
        task_id=task_id,
        message=f"Task '{task_id}' has been cancelled and removed.",
    )


# ---------------------------------------------------------------------------
# Startup / shutdown hooks
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    """Initialise resources when the server starts."""
    logger.info("ComputerUse API starting up…")
    # In production: await db.connect(), await redis.ping(), etc.


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Release resources when the server shuts down."""
    logger.info("ComputerUse API shutting down…")
    # In production: await db.disconnect(), etc.
