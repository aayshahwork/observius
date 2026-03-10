"""
workers/models.py — Data models for the task execution engine.

Defines the core dataclasses used throughout the worker pipeline:
StepData captures per-step telemetry and TaskConfig holds the full
task specification received from the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional


class ActionType(StrEnum):
    """Categories of browser actions the agent can perform."""

    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    EXTRACT = "extract"
    WAIT = "wait"
    INJECT_CREDENTIALS = "inject_credentials"
    UNKNOWN = "unknown"


@dataclass
class StepData:
    """Data captured for a single step during task execution.

    Each browser action produces one StepData record.  The ordered list
    forms a full execution trace for replay and debugging.
    """

    step_number: int
    timestamp: datetime
    action_type: ActionType
    description: str  # max 500 chars, truncated at capture time
    screenshot_bytes: Optional[bytes] = None
    dom_snapshot: Optional[str] = None
    llm_prompt: Optional[str] = None
    llm_response: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    success: bool = True
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.description) > 500:
            self.description = self.description[:500]


@dataclass
class TaskConfig:
    """Configuration for a single browser automation task.

    Mirrors the SDK TaskConfig but uses a plain dataclass for the worker
    layer (no Pydantic dependency in the hot path).
    """

    url: str
    task: str
    credentials: Optional[Dict[str, str]] = None
    output_schema: Optional[Dict[str, str]] = None
    max_steps: int = 50
    timeout_seconds: int = 300
    retry_attempts: int = 3
    retry_delay_seconds: int = 2
    max_cost_cents: Optional[int] = None
    session_id: Optional[str] = None


@dataclass
class TaskResult:
    """Result returned after a task completes or fails."""

    task_id: str
    status: str  # "completed" | "failed"
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    replay_url: Optional[str] = None
    replay_path: Optional[str] = None
    steps: int = 0
    duration_ms: int = 0
    step_data: List[StepData] = field(default_factory=list)
    cumulative_cost_cents: float = 0.0
