from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields as dc_fields
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
    # Stagehand AI actions
    ACT = "act"
    OBSERVE = "observe"
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
    # Non-visual agent actions
    LLM_CALL = "llm_call"
    API_CALL = "api_call"
    STATE_SNAPSHOT = "state_snapshot"
    # Desktop automation actions
    DESKTOP_CLICK = "desktop_click"
    DESKTOP_TYPE = "desktop_type"
    DESKTOP_HOTKEY = "desktop_hotkey"
    DESKTOP_SCROLL = "desktop_scroll"
    DESKTOP_DRAG = "desktop_drag"
    DESKTOP_LAUNCH = "desktop_launch"
    DESKTOP_FOCUS = "desktop_focus"
    WINDOW_SWITCH = "window_switch"
    MENU_SELECT = "menu_select"
    FILE_OPEN = "file_open"
    FILE_SAVE = "file_save"


# ---------------------------------------------------------------------------
# Schema type validation helpers
# ---------------------------------------------------------------------------

# All recognised leaf type tokens.
_SCALAR_TYPES = frozenset({"str", "int", "float", "bool", "list", "dict"})

# Matches a parameterised outer type, e.g. "list[str]" or "dict[str, int]".
_PARAMETERISED_RE = re.compile(r"^(list|dict)\[(.+)\]$", re.IGNORECASE)


def _validate_type_string(type_str: str) -> bool:
    """Return ``True`` if *type_str* is a valid output-schema type expression.

    Supported grammar (case-insensitive)::

        type       := scalar | parameterised
        scalar     := "str" | "int" | "float" | "bool" | "list" | "dict"
        parameterised := "list[" type "]"
                       | "dict[" type "," type "]"

    Examples of valid strings::

        "str", "int", "float", "bool"
        "list", "dict"
        "list[str]", "list[int]", "list[float]", "list[bool]"
        "dict[str, int]", "dict[str, float]", "dict[str, str]"
        "list[dict[str, str]]"        # nested
        "dict[str, list[int]]"        # nested value type

    Args:
        type_str: A type expression from an ``output_schema`` value.

    Returns:
        ``True`` if the expression is valid, ``False`` otherwise.
    """
    type_str = type_str.strip().lower()

    if type_str in _SCALAR_TYPES:
        return True

    match = _PARAMETERISED_RE.match(type_str)
    if not match:
        return False

    outer, inner = match.group(1), match.group(2).strip()

    if outer == "list":
        return _validate_type_string(inner)

    if outer == "dict":
        # Split on the first top-level comma (ignoring commas inside brackets).
        parts = _split_top_level(inner)
        if len(parts) == 1:
            # Shorthand: dict[int] means dict[str, int]
            return _validate_type_string(parts[0])
        if len(parts) == 2:
            return _validate_type_string(parts[0]) and _validate_type_string(parts[1])
        return False

    return False  # unreachable, but keeps type checkers happy


def _split_top_level(s: str) -> list[str]:
    """Split *s* on commas that are not inside square brackets.

    Used to separate key and value types inside ``dict[…, …]`` without
    being confused by nested parameterised types like ``dict[str, list[int]]``.

    Args:
        s: The content inside the outer ``dict[…]`` brackets.

    Returns:
        List of type-expression strings after splitting.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


# ---------------------------------------------------------------------------
# Dataclass models (public API)
# ---------------------------------------------------------------------------


def _pydantic_aware_dict_factory(items: list[tuple[str, Any]]) -> Dict[str, Any]:
    """Dict factory for :func:`dataclasses.asdict` that handles Pydantic models.

    If any value is a Pydantic ``BaseModel``, it is serialised via
    ``.model_dump()`` so the result is always plain dicts/lists/primitives.
    """
    result: Dict[str, Any] = {}
    for key, value in items:
        if isinstance(value, BaseModel):
            result[key] = value.model_dump()
        elif isinstance(value, list):
            result[key] = [v.model_dump() if isinstance(v, BaseModel) else v for v in value]
        else:
            result[key] = value
    return result


@dataclass
class TaskConfig:
    """Configuration for a single browser automation task.

    Passed to :meth:`ComputerUse.run_task` (or :class:`TaskExecutor` directly)
    to describe what the agent should do, how it should authenticate, what
    structured data to return, and how hard to try before giving up.
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
    idempotency_key: Optional[str] = None
    webhook_url: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.url or not self.url.strip():
            raise ValueError("url must not be empty")
        if not self.task or len(self.task) < 1:
            raise ValueError("task must not be empty")
        if len(self.task) > 2000:
            raise ValueError("task must be 2000 characters or fewer")
        if self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if self.retry_attempts < 0:
            raise ValueError("retry_attempts must be >= 0")
        if self.max_cost_cents is not None and self.max_cost_cents <= 0:
            raise ValueError("max_cost_cents must be > 0 when provided")
        if self.output_schema:
            invalid: list[str] = []
            for field_name, type_str in self.output_schema.items():
                if not isinstance(type_str, str) or not _validate_type_string(type_str):
                    invalid.append(f"'{field_name}': '{type_str}'")
            if invalid:
                raise ValueError(
                    f"output_schema contains invalid type expression(s): "
                    f"{', '.join(invalid)}. "
                    f"Supported types: str, int, float, bool, list, dict, "
                    f"list[T], dict[str, T], and nested variants thereof."
                )


@dataclass
class TaskResult:
    """Result returned after a task completes, fails, or is still in progress.

    Captures the final outcome including extracted data, error details,
    optional replay artifacts, and timing metadata.
    """

    task_id: str
    status: str
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    replay_url: Optional[str] = None
    replay_path: Optional[str] = None
    steps: int = 0
    duration_ms: int = 0
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cost_cents: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    error_category: Optional[str] = None
    step_data: List[Any] = field(default_factory=list)
    analysis: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict.

        Uses a Pydantic-aware dict factory so nested ``BaseModel`` instances
        (if any) are serialised via ``.model_dump()``.
        """
        d = asdict(self, dict_factory=_pydantic_aware_dict_factory)
        for key in ("created_at", "completed_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        return d

    def to_json(self, indent: Optional[int] = None) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), default=str, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskResult:
        """Deserialize from a dict (with ISO datetime strings)."""
        data = dict(data)  # shallow copy to avoid mutating caller's dict
        for key in ("created_at", "completed_at"):
            val = data.get(key)
            if isinstance(val, str):
                try:
                    data[key] = datetime.fromisoformat(val)
                except ValueError:
                    data[key] = None
        known = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_json(cls, text: str) -> TaskResult:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(text))


# ---------------------------------------------------------------------------
# Pydantic models (internal SDK use only)
# ---------------------------------------------------------------------------


class StepData(BaseModel):
    """Data captured for a single step during task execution.

    Each browser action (click, type, navigate, scroll, etc.) produces one
    ``StepData`` record.  The ordered list of records on a completed
    :class:`TaskResult` forms a full execution trace that can be replayed
    or inspected for debugging.
    """

    step_number: int = Field(
        ...,
        ge=1,
        description="1-based index of this step within the task run",
    )
    action_type: str = Field(
        default="unknown",
        description=(
            "Category of action taken. Common values: "
            "'click', 'type', 'navigate', 'scroll', 'select', 'wait', 'extract'"
        ),
    )
    description: str = Field(
        default="",
        description="Human-readable summary of what this step did",
    )
    screenshot_path: str = Field(
        default="",
        description=(
            "Filesystem path to the PNG screenshot captured immediately "
            "after this step executed. Empty string if no screenshot was taken."
        ),
    )
    dom_snapshot: Optional[str] = Field(
        default=None,
        description="Serialised DOM snapshot at this step, if captured by the agent",
    )
    success: bool = Field(
        default=True,
        description="Whether this individual step completed without error",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if this step failed; None when success=True",
    )
    timestamp: datetime = Field(
        ...,
        description="UTC timestamp when this step was executed",
    )
    screenshot_bytes: Optional[bytes] = Field(
        default=None,
        exclude=True,
        description="Raw screenshot bytes, used by replay generator",
    )
    tokens_in: int = Field(
        default=0,
        description="Input tokens consumed by LLM for this step",
    )
    tokens_out: int = Field(
        default=0,
        description="Output tokens produced by LLM for this step",
    )
    duration_ms: int = Field(
        default=0,
        description="Wall-clock duration of this step in milliseconds",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="LLM reasoning text for this step",
    )
    context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Arbitrary debug context (LLM traces, API responses, state snapshots)",
    )

    # -- Explore-to-replay enrichment fields ----------------------------------
    selectors: Optional[List[Dict]] = Field(
        default=None,
        description="Multiple selector strategies for the acted-on element",
    )
    intent: str = Field(default="", description="Inferred intent of this step")
    intent_detail: str = Field(default="", description="Detailed intent description")
    pre_url: str = Field(default="", description="Page URL before the action")
    post_url: str = Field(default="", description="Page URL after the action")
    pre_dom_hash: str = Field(default="", description="DOM fingerprint hash before action")
    post_dom_hash: str = Field(default="", description="DOM fingerprint hash after action")
    expected_url_pattern: str = Field(
        default="", description="Regex pattern the post-action URL should match",
    )
    expected_element: str = Field(
        default="", description="CSS selector expected to appear after action",
    )
    expected_text: str = Field(
        default="", description="Text expected on page after action",
    )
    fill_value_template: str = Field(
        default="", description="Parameterized template for fill values (e.g. {{email}})",
    )
    element_text: str = Field(default="", description="innerText of the acted-on element")
    element_tag: str = Field(default="", description="HTML tag of the acted-on element")
    element_role: str = Field(default="", description="ARIA role of the acted-on element")
    verification_result: Optional[Dict] = Field(
        default=None, description="Post-action verification outcome",
    )
    window_title: str = Field(default="", description="Desktop window title (desktop automation)")
    control_type: str = Field(default="", description="Desktop control type (desktop automation)")
    control_name: str = Field(default="", description="Desktop control name (desktop automation)")

    # -- Retry intelligence fields (appended by AR3) ---------------------------
    attempt_number: int = Field(
        default=1,
        description="Which retry attempt produced this step (1-based)",
    )


# ---------------------------------------------------------------------------
# Compiled workflow models (explore-to-replay)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompiledStep:
    """A single step in a compiled, replayable workflow."""

    action_type: str
    selectors: List[Dict] = field(default_factory=list)
    fill_value_template: str = ""
    expected_url_pattern: str = ""
    expected_element: str = ""
    expected_text: str = ""
    intent: str = ""
    timeout_ms: int = 30000
    pre_url: str = ""
    window_title: str = ""
    control_type: str = ""
    control_name: str = ""


@dataclass(frozen=True)
class CompiledWorkflow:
    """A compiled workflow ready for deterministic replay."""

    name: str
    steps: List[CompiledStep] = field(default_factory=list)
    start_url: str = ""
    parameters: Dict[str, str] = field(default_factory=dict)
    source_task_id: str = ""
    compiled_at: str = ""


class SessionData(BaseModel):
    """Persisted browser session state for a given domain.

    Stores cookies and Web Storage entries so that authenticated sessions
    can be restored across task runs without re-logging in.  Managed
    automatically by :class:`SessionManager` when ``credentials`` are
    passed to :meth:`ComputerUse.run_task`.
    """

    domain: str = Field(
        ...,
        description=("Origin or hostname this session belongs to, " "e.g. 'https://example.com' or 'example.com'"),
    )
    cookies: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "List of cookie objects in Playwright/CDP format. "
            "Each dict contains at minimum 'name', 'value', and 'domain'."
        ),
    )
    local_storage: Dict[str, str] = Field(
        default_factory=dict,
        description="Key/value pairs from window.localStorage for this origin",
    )
    session_storage: Dict[str, str] = Field(
        default_factory=dict,
        description="Key/value pairs from window.sessionStorage for this origin",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when this session was first saved to disk",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description=(
            "UTC timestamp after which this session should be considered stale "
            "and discarded. None means no explicit expiry — the session is "
            "kept until manually deleted."
        ),
    )
