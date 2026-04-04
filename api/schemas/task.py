"""
api/schemas/task.py — Pydantic v2 request/response models for the Tasks API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class StepIngestData(BaseModel):
    """Single step in an SDK-ingested task."""

    step_number: int
    action_type: str = "unknown"
    description: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    success: bool = True
    error: str | None = None
    screenshot_base64: str | None = None
    context: dict[str, Any] | None = None


class TaskIngestRequest(BaseModel):
    """POST /api/v1/tasks/ingest request body — accepts pre-completed task results from the SDK."""

    task_id: str | None = None
    url: str = ""
    task_description: str = ""
    status: str = "completed"
    cost_cents: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    error_category: str | None = None
    error_message: str | None = None
    executor_mode: str = "sdk"
    duration_ms: int = 0
    steps: list[StepIngestData] = []
    created_at: str | None = None
    completed_at: str | None = None
    analysis: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    compiled_workflow: dict[str, Any] | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"queued", "running", "completed", "failed", "timeout", "cancelled"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


class TaskCreateRequest(BaseModel):
    """POST /api/v1/tasks request body."""

    url: HttpUrl = Field(..., description="Starting URL for browser automation")
    task: str = Field(..., min_length=1, max_length=2000, description="Task description")
    output_schema: dict[str, Any] | None = Field(default=None, description="Expected output shape")
    credentials: dict[str, str] | None = Field(
        default=None,
        exclude=True,
        description="Login credentials (never serialized in responses/logs)",
    )
    timeout_seconds: int = Field(default=300, ge=30, le=600)
    max_retries: int = Field(default=3, ge=0, le=5)
    session_id: uuid.UUID | None = Field(default=None)
    idempotency_key: str | None = Field(default=None, max_length=255)
    webhook_url: HttpUrl | None = Field(default=None)
    max_cost_cents: int | None = Field(default=None, gt=0)
    executor_mode: str = Field(
        default="browser_use",
        pattern=r"^(browser_use|native)$",
        description="Executor mode: 'browser_use' (DOM-based) or 'native' (screenshot pixel-based)",
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class TaskResponse(BaseModel):
    """Representation of a single task."""

    task_id: uuid.UUID
    url: str | None = None
    task_description: str | None = None
    status: str
    success: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    replay_url: str | None = None
    steps: int = 0
    duration_ms: int = 0
    created_at: datetime
    completed_at: datetime | None = None
    retry_count: int = 0
    retry_of_task_id: uuid.UUID | None = None
    error_category: str | None = None
    cost_cents: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    executor_mode: str = "browser_use"
    analysis: dict[str, Any] | None = None
    compiled_workflow: dict[str, Any] | None = None
    playwright_script: str | None = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """Paginated list of tasks."""

    tasks: list[TaskResponse]
    total: int
    has_more: bool


class StepResponse(BaseModel):
    """Single step within a task execution."""

    step_number: int
    action_type: str
    description: str | None = None
    screenshot_url: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    success: bool = True
    error: str | None = None
    created_at: datetime | None = None
    context: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class ErrorResponse(BaseModel):
    """Standard error response body."""

    error_code: str
    message: str
    details: list[str] | None = None
