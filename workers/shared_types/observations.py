"""
workers/shared_types/observations.py — What the agent sees at each step.

Observation captures the browser state snapshot taken after each action.
Used by both the executor (Person A) and reliability/retry logic (Person B).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Observation:
    """Browser state snapshot captured after an action executes.

    The executor creates one Observation per step. Downstream consumers
    (stuck detector, failure analyzer, action verifier, replay generator)
    read observations without mutating them.
    """

    url: str = ""
    page_title: str = ""
    screenshot_b64: Optional[str] = None
    dom_snippet: str = ""
    timestamp_ms: int = 0
    viewport_width: int = 1280
    viewport_height: int = 720
    tab_count: int = 1
    error_text: str = ""
    console_errors: list[str] = field(default_factory=list)

    @property
    def has_screenshot(self) -> bool:
        """Whether a screenshot was captured for this observation."""
        return self.screenshot_b64 is not None and len(self.screenshot_b64) > 0

    @property
    def has_error(self) -> bool:
        """Whether the page showed an error state."""
        return bool(self.error_text) or bool(self.console_errors)

    def truncated_dom(self, max_chars: int = 2000) -> str:
        """Return DOM snippet truncated to max_chars for LLM context."""
        if len(self.dom_snippet) <= max_chars:
            return self.dom_snippet
        return self.dom_snippet[:max_chars] + "..."
