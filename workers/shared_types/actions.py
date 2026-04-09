"""Action-related shared types: StepIntent, StepResult, GroundingRung."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from workers.shared_types.observations import Observation


class GroundingRung(StrEnum):
    """Selector strategy used, ordered from most to least preferred."""

    ROLE = "role"
    LABEL = "label"
    TEXT = "text"
    TESTID = "testid"
    CSS_XPATH = "css_xpath"
    VISION = "vision"
    COORDINATES = "coordinates"  # raw x,y — used by Anthropic CUA and Skyvern


@dataclass
class StepIntent:
    """What the planner wants the backend to do."""

    action: str  # from existing ActionType enum in workers/models.py
    target: dict[str, Any]  # locator info: {strategy: "role", role: "button", name: "Submit"} or {x: 100, y: 200}
    value: str | None = None  # text to type, URL to navigate to, etc.
    guardrails: list[str] = field(default_factory=list)  # e.g. ["no_purchase", "require_confirmation"]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    """What the backend returns after executing."""

    success: bool
    observation: Observation
    artifacts: dict[str, Any] = field(default_factory=dict)  # {screenshot_ref, har_ref, trace_ref, video_ref}
    error: str | None = None
    error_code: str | None = None  # machine-readable, e.g. "element_not_found"
    grounding_rung_used: GroundingRung | None = None
    duration_ms: int = 0
    raw_backend_output: Any = None  # backend-specific data for debugging
