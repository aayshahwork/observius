"""Observation dataclass — what the browser environment looks like after an action."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    url: str
    title: str
    ax_tree_summary: str | None = None  # pruned accessibility tree text
    screenshot_ref: str | None = None  # R2 URL or local path
    screenshot_b64: str | None = None  # base64 for LLM consumption
    open_tabs: list[str] = field(default_factory=list)
    error_signals: list[str] = field(default_factory=list)
    network_signals: list[dict[str, Any]] = field(default_factory=list)  # [{status, url, method}]
    dom_hash: str | None = None  # content-addressed hash for stuck detection
    timestamp: float = field(default_factory=time.time)
    raw: dict[str, Any] = field(default_factory=dict)
