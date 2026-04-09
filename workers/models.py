"""
workers/models.py — Worker-level data models for the task execution engine.

Uses plain dataclasses (not Pydantic) for lighter hot-path performance.
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
    SOLVE_CAPTCHA = "solve_captcha"
    UNKNOWN = "unknown"
    # Native executor actions (computer_20251124)
    MOUSE_MOVE = "mouse_move"
    KEY_PRESS = "key_press"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    MIDDLE_CLICK = "middle_click"
    SCREENSHOT = "screenshot"
    DRAG = "drag"
    TRIPLE_CLICK = "triple_click"
    ZOOM = "zoom"


@dataclass
class StepData:
    """Data captured for a single step during task execution."""

    step_number: int
    timestamp: datetime
    action_type: ActionType = ActionType.UNKNOWN
    description: str = ""  # max 500 chars, truncated in __post_init__
    screenshot_bytes: Optional[bytes] = None
    dom_snapshot: Optional[str] = None
    llm_prompt: Optional[str] = None
    llm_response: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    success: bool = True
    error: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    validator_verdict: Optional[str] = None
    failure_class: Optional[str] = None
    patch_applied: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.description) > 500:
            self.description = self.description[:500]


@dataclass
class TaskConfig:
    """Configuration for a single browser automation task."""

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
    executor_mode: str = "browser_use"  # "browser_use" | "native" | "skyvern"
    # Skyvern-specific fields
    skyvern_engine: Optional[str] = None
    skyvern_api_key: Optional[str] = None
    data_extraction_schema: Optional[Dict[str, Any]] = None
    proxy_location: Optional[str] = None


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
    cost_cents: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    step_data: List[StepData] = field(default_factory=list)
    error_category: Optional[str] = None
