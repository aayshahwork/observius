"""
workers/backends/protocol.py — Abstract protocol for browser automation backends.

Every backend (browser_use, native CUA, Skyvern, etc.) implements CUABackend.
The orchestrator talks to backends through this interface only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, runtime_checkable

from workers.shared_types import Observation, StepIntent, StepResult


@dataclass
class BackendCapabilities:
    """Declares what a backend can do. Used by the orchestrator to pick strategy."""

    supports_single_step: bool = False
    supports_goal_delegation: bool = True
    supports_screenshots: bool = True
    supports_har: bool = False
    supports_trace: bool = False
    supports_video: bool = False
    supports_ax_tree: bool = False


@runtime_checkable
class CUABackend(Protocol):
    """Protocol that every execution backend must satisfy."""

    @property
    def name(self) -> str:
        """Backend identifier (e.g. 'browser_use', 'native', 'skyvern')."""
        ...

    async def initialize(self, config: dict) -> None:
        """Set up the browser session / external connection."""
        ...

    async def execute_step(self, intent: StepIntent) -> StepResult:
        """Execute a single atomic browser action."""
        ...

    async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
        """Delegate a full goal to the backend's own agentic loop."""
        ...

    async def get_observation(self) -> Observation:
        """Return current browser state without acting."""
        ...

    async def teardown(self) -> None:
        """Clean up browser session and resources."""
        ...
