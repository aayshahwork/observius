from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


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
# Models
# ---------------------------------------------------------------------------


class TaskConfig(BaseModel):
    """Configuration for a single browser automation task.

    Passed to :meth:`ComputerUse.run_task` (or :class:`TaskExecutor` directly)
    to describe what the agent should do, how it should authenticate, what
    structured data to return, and how hard to try before giving up.

    Basic example::

        config = TaskConfig(
            url="https://news.ycombinator.com",
            task="Return the titles of the top 5 posts",
            output_schema={"titles": "list[str]"},
        )

    With credentials and retries::

        config = TaskConfig(
            url="https://github.com/login",
            task="Star the repo anthropics/anthropic-sdk-python",
            credentials={"username": "alice", "password": "s3cr3t"},
            max_steps=20,
            retry_attempts=2,
        )

    Supported ``output_schema`` type strings
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Scalar:         ``"str"``, ``"int"``, ``"float"``, ``"bool"``
    Collections:    ``"list"``, ``"dict"``
    Parameterised:  ``"list[str]"``, ``"list[int]"``, ``"list[float]"``
                    ``"dict[str, int]"``, ``"dict[str, float]"``
                    ``"dict[str, str]"``
    Nested:         ``"list[dict[str, str]]"``
                    ``"dict[str, list[int]]"``
    """

    url: str = Field(
        ...,
        description="The starting URL the browser should navigate to before executing the task",
    )
    task: str = Field(
        ...,
        min_length=1,
        description="Natural-language description of what the agent should do",
    )
    credentials: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Key/value credentials injected into the task prompt "
            "(e.g. {'username': 'alice', 'password': 's3cr3t'}). "
            "When provided the session is saved after completion and "
            "restored on the next run for the same domain."
        ),
    )
    output_schema: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Declares the fields to extract from the page and their types. "
            "Keys are field names; values are type strings such as "
            "'str', 'int', 'float', 'bool', 'list[str]', 'dict[str, int]'. "
            "Nested parameterised types are supported, e.g. 'list[dict[str, str]]'."
        ),
        examples=[
            {"price": "float", "currency": "str"},
            {"titles": "list[str]", "top_score": "int"},
            {"items": "list[dict[str, str]]"},
        ],
    )
    max_steps: int = Field(
        default=50,
        ge=1,
        description="Maximum number of browser actions the agent may take before the run is aborted",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=1,
        description="Wall-clock timeout in seconds for the entire task",
    )
    retry_attempts: int = Field(
        default=3,
        ge=0,
        description="Number of additional attempts on recoverable failures (0 = no retries)",
    )
    retry_delay_seconds: int = Field(
        default=2,
        ge=0,
        description="Base delay in seconds between retry attempts; grows exponentially",
    )

    @field_validator("output_schema")
    @classmethod
    def validate_schema_types(
        cls, schema: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        """Validate every type string in *output_schema* at construction time.

        Catching invalid type strings here (rather than at extraction time)
        produces a clear ``ValidationError`` with the offending field name
        immediately when the config is built, not mid-task.

        Args:
            schema: The raw ``output_schema`` value.

        Returns:
            The schema unchanged if all type strings are valid.

        Raises:
            ValueError: If any value is not a recognised type expression.

        Examples::

            # Valid
            TaskConfig(url="…", task="…", output_schema={"price": "float"})
            TaskConfig(url="…", task="…", output_schema={"tags": "list[str]"})
            TaskConfig(url="…", task="…",
                       output_schema={"items": "list[dict[str, str]]"})

            # Invalid — raises ValidationError
            TaskConfig(url="…", task="…", output_schema={"x": "uuid"})
            TaskConfig(url="…", task="…", output_schema={"x": "List[str]"})  # wrong syntax
        """
        if schema is None:
            return schema

        invalid: list[str] = []
        for field_name, type_str in schema.items():
            if not isinstance(type_str, str) or not _validate_type_string(type_str):
                invalid.append(f"'{field_name}': '{type_str}'")

        if invalid:
            raise ValueError(
                f"output_schema contains invalid type expression(s): "
                f"{', '.join(invalid)}. "
                f"Supported types: str, int, float, bool, list, dict, "
                f"list[T], dict[str, T], and nested variants thereof."
            )

        return schema


class TaskResult(BaseModel):
    """Result returned after a task completes, fails, or is still in progress.

    Captures the final outcome including extracted data, error details,
    optional replay artifacts, and timing metadata.

    Checking the result::

        result = cu.run_task(url="…", task="…", output_schema={"price": "float"})

        if result.success:
            print(result.result["price"])       # float, already coerced
            print(f"Done in {result.duration_ms}ms over {result.steps} steps")
        else:
            print(f"Failed: {result.error}")
            # Inspect the replay for a step-by-step trace:
            print(result.replay_path)

    Status lifecycle::

        "pending"   → task queued, not yet started
        "running"   → agent is actively executing
        "completed" → agent finished (check success for outcome)
        "failed"    → unrecoverable error (see error field)
    """

    task_id: str = Field(
        ...,
        description="Unique identifier (UUID4) for this task run",
    )
    status: Literal["pending", "running", "completed", "failed"] = Field(
        ...,
        description="Current lifecycle status of the task",
    )
    success: bool = Field(
        ...,
        description="True if the task completed without error and output validation passed",
    )
    result: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Extracted and type-validated output data. "
            "Shaped according to TaskConfig.output_schema when provided; "
            "None when no schema was given or the task failed."
        ),
    )
    error: Optional[str] = Field(
        default=None,
        description="Human-readable error description when success=False",
    )
    replay_url: Optional[str] = Field(
        default=None,
        description="Remote HTTPS URL to the session replay (cloud execution only)",
    )
    replay_path: Optional[str] = Field(
        default=None,
        description="Local filesystem path to the replay JSON file",
    )
    steps: int = Field(
        default=0,
        ge=0,
        description="Total number of browser actions taken during execution",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Total wall-clock duration from task start to terminal state, in milliseconds",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the task was created / queued",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when the task reached a terminal state (completed or failed)",
    )


class StepData(BaseModel):
    """Data captured for a single step during task execution.

    Each browser action (click, type, navigate, scroll, etc.) produces one
    ``StepData`` record.  The ordered list of records on a completed
    :class:`TaskResult` forms a full execution trace that can be replayed
    or inspected for debugging.

    Example::

        step = StepData(
            step_number=3,
            action_type="click",
            description="Clicked the 'Sign in' button",
            screenshot_path="replays/screenshots/step_0003.png",
            success=True,
            timestamp=datetime.now(timezone.utc),
        )
    """

    step_number: int = Field(
        ...,
        ge=1,
        description="1-based index of this step within the task run",
    )
    action_type: str = Field(
        ...,
        description=(
            "Category of action taken. Common values: "
            "'click', 'type', 'navigate', 'scroll', 'select', 'wait', 'extract'"
        ),
    )
    description: str = Field(
        ...,
        description="Human-readable summary of what this step did",
    )
    screenshot_path: str = Field(
        ...,
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
        ...,
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


class SessionData(BaseModel):
    """Persisted browser session state for a given domain.

    Stores cookies and Web Storage entries so that authenticated sessions
    can be restored across task runs without re-logging in.  Managed
    automatically by :class:`SessionManager` when ``credentials`` are
    passed to :meth:`ComputerUse.run_task`.

    Example (manual construction for testing)::

        session = SessionData(
            domain="https://github.com",
            cookies=[{"name": "user_session", "value": "abc123", "domain": ".github.com"}],
            local_storage={"theme": "dark"},
            session_storage={},
            created_at=datetime.now(timezone.utc),
        )

    Expiry::

        if session.expires_at and datetime.now(timezone.utc) > session.expires_at:
            session_manager.delete_session(session.domain)
    """

    domain: str = Field(
        ...,
        description=(
            "Origin or hostname this session belongs to, "
            "e.g. 'https://example.com' or 'example.com'"
        ),
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
