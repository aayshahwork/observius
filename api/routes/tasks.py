"""
api/routes/tasks.py — Task CRUD endpoints.

POST   /api/v1/tasks              Create a new task
GET    /api/v1/tasks/{task_id}    Get task by ID
GET    /api/v1/tasks              List tasks (paginated)
DELETE /api/v1/tasks/{task_id}    Cancel a task
POST   /api/v1/tasks/{task_id}/retry   Retry a failed task
GET    /api/v1/tasks/{task_id}/replay  Get signed replay URL
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import structlog
from celery import Celery
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.dependencies import get_db, get_redis
from api.middleware.auth import get_current_account
from api.models.account import Account
from api.models.task import Task
from api.schemas.task import ErrorResponse, TaskCreateRequest, TaskListResponse, TaskResponse
from shared.constants import TIER_LIMITS
from api.services.audit_logger import TASK_CANCELLED, TASK_CREATED, TASK_RETRIED, AuditLogger
from shared.url_validator import SSRFBlockedError, validate_url_async, validate_webhook_url

logger = structlog.get_logger("api.tasks")

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])

IDEMPOTENCY_TTL = 86400  # 24 hours

# Celery client for enqueuing tasks (send-only, no result backend needed).
_celery = Celery(broker=settings.REDIS_URL)
_celery.conf.update(task_serializer="json", accept_content=["json"])


def _task_to_response(task: Task) -> TaskResponse:
    """Convert a Task ORM object to a TaskResponse."""
    return TaskResponse(
        task_id=task.id,
        url=task.url,
        status=task.status or "queued",
        success=task.success or False,
        result=task.result,
        error=task.error_message,
        replay_url=None,
        steps=task.total_steps or 0,
        duration_ms=task.duration_ms or 0,
        created_at=task.created_at or datetime.now(timezone.utc),
        completed_at=task.completed_at,
        retry_count=task.retry_count or 0,
        retry_of_task_id=task.retry_of_task_id,
        error_category=task.error_category,
        cost_cents=round(float(task.cost_cents or 0), 4),
        total_tokens_in=task.total_tokens_in or 0,
        total_tokens_out=task.total_tokens_out or 0,
        executor_mode=task.executor_mode or "browser_use",
    )


# ---------------------------------------------------------------------------
# POST /api/v1/tasks
# ---------------------------------------------------------------------------

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def create_task(
    body: TaskCreateRequest,
    request: Request,
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TaskResponse:
    """Create a new browser automation task."""

    # -- Idempotency check --
    if body.idempotency_key:
        cache_key = f"idempotency:{account.id}:{body.idempotency_key}"
        cached = await redis.get(cache_key)
        if cached:
            return TaskResponse.model_validate_json(cached)

    # -- SSRF validation --
    try:
        await validate_url_async(str(body.url))
    except SSRFBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "INVALID_INPUT", "message": str(exc)},
        )

    if body.webhook_url:
        try:
            await validate_webhook_url(str(body.webhook_url))
        except SSRFBlockedError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error_code": "INVALID_INPUT", "message": str(exc)},
            )

    # -- Quota check --
    if account.monthly_steps_used >= account.monthly_step_limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error_code": "QUOTA_EXCEEDED",
                "message": "Monthly step quota exceeded. Upgrade your plan.",
            },
        )

    # -- Insert task row --
    task = Task(
        id=uuid.uuid4(),
        account_id=account.id,
        status="queued",
        url=str(body.url),
        task_description=body.task,
        output_schema=body.output_schema,
        idempotency_key=body.idempotency_key,
        webhook_url=str(body.webhook_url) if body.webhook_url else None,
        max_cost_cents=body.max_cost_cents,
        session_id=body.session_id,
        executor_mode=body.executor_mode,
        created_at=datetime.now(timezone.utc),
    )
    db.add(task)
    await AuditLogger(db).log(
        account_id=account.id,
        actor_type="user",
        actor_id=str(account.id),
        action=TASK_CREATED,
        resource_type="task",
        resource_id=str(task.id),
        metadata={"url": str(body.url)},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(task)

    # -- Enqueue Celery task --
    tier = account.tier or "free"
    tier_config = TIER_LIMITS.get(tier, TIER_LIMITS["free"])

    config_json = json.dumps({
        "url": str(body.url),
        "task": body.task,
        "credentials": body.credentials,
        "output_schema": body.output_schema,
        "max_steps": tier_config["max_steps"],
        "timeout_seconds": min(body.timeout_seconds, tier_config["timeout"]),
        "max_cost_cents": body.max_cost_cents,
        "session_id": str(body.session_id) if body.session_id else None,
        "webhook_url": str(body.webhook_url) if body.webhook_url else None,
        "retry_attempts": body.max_retries,
        "retry_delay_seconds": 2,
        "executor_mode": body.executor_mode,
    })

    _celery.send_task(
        "computeruse.execute_task",
        args=[str(task.id), config_json],
        queue=f"tasks:{tier}",
        task_id=str(task.id),
        soft_time_limit=tier_config["timeout"] + 60,
        time_limit=tier_config["timeout"] + 120,
    )

    logger.info("task_queued", task_id=str(task.id), account_id=str(account.id), queue=f"tasks:{tier}")

    response = _task_to_response(task)

    # -- Cache idempotency result --
    if body.idempotency_key:
        cache_key = f"idempotency:{account.id}:{body.idempotency_key}"
        await redis.set(cache_key, response.model_dump_json(), ex=IDEMPOTENCY_TTL)

    return response


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/{task_id}
# ---------------------------------------------------------------------------

@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_task(
    task_id: uuid.UUID,
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Return the current status and result of a task."""
    stmt = select(Task).where(Task.id == task_id, Task.account_id == account.id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "NOT_FOUND", "message": "Task not found."},
        )

    return _task_to_response(task)


# ---------------------------------------------------------------------------
# GET /api/v1/tasks
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=TaskListResponse,
)
async def list_tasks(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    task_status: str | None = Query(default=None, alias="status"),
    since: datetime | None = Query(default=None),
    session_id: uuid.UUID | None = Query(default=None),
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    """List tasks with pagination and optional filters."""
    base = select(Task).where(Task.account_id == account.id)

    if task_status:
        base = base.where(Task.status == task_status)
    if since:
        base = base.where(Task.created_at >= since)
    if session_id:
        base = base.where(Task.session_id == session_id)

    # Total count
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Paginated results
    stmt = base.order_by(Task.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return TaskListResponse(
        tasks=[_task_to_response(t) for t in tasks],
        total=total,
        has_more=(offset + limit) < total,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/tasks/{task_id}
# ---------------------------------------------------------------------------

@router.delete(
    "/{task_id}",
    status_code=status.HTTP_200_OK,
    responses={404: {"model": ErrorResponse}},
)
async def cancel_task(
    task_id: uuid.UUID,
    request: Request,
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Cancel a queued or running task."""
    stmt = select(Task).where(Task.id == task_id, Task.account_id == account.id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "NOT_FOUND", "message": "Task not found."},
        )

    if task.status not in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "INVALID_STATE",
                "message": f"Cannot cancel task with status '{task.status}'.",
            },
        )

    previous_status = task.status
    task.status = "cancelled"
    task.completed_at = datetime.now(timezone.utc)
    await AuditLogger(db).log(
        account_id=account.id,
        actor_type="user",
        actor_id=str(account.id),
        action=TASK_CANCELLED,
        resource_type="task",
        resource_id=str(task_id),
        metadata={"previous_status": previous_status},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()

    logger.info("task_cancelled", task_id=str(task_id), account_id=str(account.id))
    return {"task_id": str(task_id), "status": "cancelled"}


# ---------------------------------------------------------------------------
# POST /api/v1/tasks/{task_id}/retry
# ---------------------------------------------------------------------------

@router.post(
    "/{task_id}/retry",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskResponse,
    responses={404: {"model": ErrorResponse}},
)
async def retry_task(
    task_id: uuid.UUID,
    request: Request,
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Retry a failed task by cloning its configuration into a new task."""
    stmt = select(Task).where(Task.id == task_id, Task.account_id == account.id)
    result = await db.execute(stmt)
    original = result.scalar_one_or_none()

    if original is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "NOT_FOUND", "message": "Task not found."},
        )

    if original.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "INVALID_STATE",
                "message": "Only failed tasks can be retried.",
            },
        )

    new_task = Task(
        id=uuid.uuid4(),
        account_id=account.id,
        status="queued",
        url=original.url,
        task_description=original.task_description,
        output_schema=original.output_schema,
        webhook_url=original.webhook_url,
        max_cost_cents=original.max_cost_cents,
        session_id=original.session_id,
        executor_mode=original.executor_mode or "browser_use",
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_task)
    await AuditLogger(db).log(
        account_id=account.id,
        actor_type="user",
        actor_id=str(account.id),
        action=TASK_RETRIED,
        resource_type="task",
        resource_id=str(new_task.id),
        metadata={"original_task_id": str(task_id)},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(new_task)

    # Enqueue to Celery (was missing — ghost task bug fix)
    tier = account.tier or "free"
    tier_config = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    config_json = json.dumps({
        "url": original.url,
        "task": original.task_description,
        "credentials": None,
        "output_schema": original.output_schema,
        "max_steps": tier_config["max_steps"],
        "timeout_seconds": tier_config["timeout"],
        "max_cost_cents": original.max_cost_cents,
        "session_id": str(original.session_id) if original.session_id else None,
        "webhook_url": original.webhook_url,
        "retry_attempts": 0,
        "retry_delay_seconds": 2,
        "executor_mode": original.executor_mode or "browser_use",
    })
    _celery.send_task(
        "computeruse.execute_task",
        args=[str(new_task.id), config_json],
        queue=f"tasks:{tier}",
        task_id=str(new_task.id),
    )

    logger.info("task_retried", original_id=str(task_id), new_id=str(new_task.id))
    return _task_to_response(new_task)


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/{task_id}/replay
# ---------------------------------------------------------------------------

@router.get(
    "/{task_id}/replay",
    responses={404: {"model": ErrorResponse}},
)
async def get_replay(
    task_id: uuid.UUID,
    account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return a signed R2 URL for the task's replay recording."""
    stmt = select(Task).where(Task.id == task_id, Task.account_id == account.id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "NOT_FOUND", "message": "Task not found."},
        )

    if not task.replay_s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "NOT_FOUND", "message": "Replay not available yet."},
        )

    # In production: generate a pre-signed URL from R2/S3 with 7-day expiry
    # signed_url = s3_client.generate_presigned_url(
    #     "get_object",
    #     Params={"Bucket": settings.R2_BUCKET_NAME, "Key": task.replay_s3_key},
    #     ExpiresIn=7 * 86400,
    # )
    signed_url = f"https://r2.computeruse.dev/{task.replay_s3_key}?signed=true"

    return {"task_id": str(task_id), "replay_url": signed_url}
