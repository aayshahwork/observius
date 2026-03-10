from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskConfig:
    """Configuration for a browser automation task."""

    max_steps: int = 50
    timeout: float = 300.0
    record: bool = True
    proxy: str | None = None
    cookies: list[dict[str, str]] = field(default_factory=list)


@dataclass
class TaskResult:
    """Result of a completed browser automation task."""

    task_id: str
    status: str  # "completed" | "failed" | "cancelled"
    output: str | None = None
    recording_url: str | None = None
    steps: int = 0
    duration_seconds: float = 0.0
    error: str | None = None
