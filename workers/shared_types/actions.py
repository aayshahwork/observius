"""
workers/shared_types/actions.py — Intent, grounding, and results for each step.

GroundingRung: how confidently the agent identified the target element.
StepIntent:    what the agent planned to do (before execution).
StepResult:    what actually happened (after execution).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional

from .observations import Observation


class GroundingRung(StrEnum):
    """Confidence level in how the agent identified its target element.

    Ordered from most reliable to least reliable. The retry system
    can suggest moving UP the ladder on re-attempts (e.g., from
    COORDINATE to CSS_SELECTOR).
    """

    CSS_SELECTOR = "css_selector"
    ARIA_LABEL = "aria_label"
    TEXT_MATCH = "text_match"
    XPATH = "xpath"
    COORDINATE = "coordinate"
    HEURISTIC = "heuristic"


@dataclass
class StepIntent:
    """What the agent planned to do before executing an action.

    Built from the LLM's response. The executor creates a StepIntent
    before running the action, then pairs it with a StepResult after.
    """

    action_type: str = ""
    target_selector: str = ""
    target_text: str = ""
    input_value: str = ""
    grounding: GroundingRung = GroundingRung.HEURISTIC
    description: str = ""
    expected_outcome: str = ""
    url_before: str = ""

    def __post_init__(self) -> None:
        if len(self.description) > 500:
            self.description = self.description[:500]


@dataclass
class StepResult:
    """What actually happened when an action was executed.

    Created by the executor after running the action described by StepIntent.
    Pairs 1:1 with a StepIntent to form a complete step record.
    """

    success: bool = True
    error: Optional[str] = None
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    observation: Optional[Observation] = None
    verification_passed: Optional[bool] = None
    side_effects: list[str] = field(default_factory=list)

    @property
    def cost_cents(self) -> float:
        """Approximate cost of this step based on Claude Sonnet pricing."""
        return (self.tokens_in * 3.0 + self.tokens_out * 15.0) / 1_000_000 * 100

    @property
    def has_observation(self) -> bool:
        """Whether a post-action observation was captured."""
        return self.observation is not None
