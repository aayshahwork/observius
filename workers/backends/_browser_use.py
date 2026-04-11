"""
workers/backends/_browser_use.py — Browser Use backend implementation.

Wraps the browser-use Agent library into the CUABackend protocol.
Ported from workers/executor.py::_execute_with_agent with EXACT same
LLM configuration, browser setup, and callback patterns.

Goal-delegation mode only: the browser-use Agent handles its own agentic
loop.  execute_step() is NOT supported.
"""

from __future__ import annotations

import base64
import inspect
import logging
import time
from typing import Any, List, Optional

from workers.backends.protocol import BackendCapabilities
from workers.config import worker_settings
from workers.shared_types import Observation, StepIntent, StepResult
from workers.stuck_detector import StuckDetector

logger = logging.getLogger(__name__)


class BrowserUseBackend:
    """CUABackend implementation using the browser-use library.

    Browser Use API reference (used in execute_goal + _convert_history):
      agent = Agent(task=goal, llm=ChatAnthropic(...), browser_session=session,
                    max_actions_per_step=3, max_failures=3, use_vision=True)
      history = await agent.run(max_steps=20)
      history.urls(), history.screenshots(), history.action_names(),
      history.extracted_content(), history.errors(), history.model_actions(),
      history.model_thoughts(), history.final_result(),
      history.is_done(), history.is_successful()
    """

    capabilities = BackendCapabilities(
        supports_single_step=False,
        supports_goal_delegation=True,
        supports_screenshots=True,
        supports_har=False,
        supports_trace=False,
        supports_video=False,
        supports_ax_tree=False,
    )

    def __init__(self) -> None:
        self._config: dict = {}
        self._llm: Any = None
        self._browser_session: Any = None
        self._stuck_detector = StuckDetector()
        self._step_timestamps: List[float] = []
        self._model: str = "claude-sonnet-4-6"
        self._last_history: Any = None  # raw AgentHistoryList for get_observation
        # Live-streaming: if set, emitted after each completed browser-use
        # step so PAV can surface partial progress during delegated goals.
        self._live_step_callback: Optional[Any] = None
        self._emitted_step_count: int = 0
        self._current_agent: Any = None

    @property
    def name(self) -> str:
        return "browser_use"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, config: dict) -> None:
        """Set up LLM and BrowserSession.

        Ports EXACTLY the setup from executor.py::_execute_with_agent.

        Config keys:
            model: str — Claude model name (default: ``claude-sonnet-4-6``)
            anthropic_api_key: str — override (default: worker_settings)
            headless: bool — headless browser (default: True)
        """
        self._config = config
        self._model = config.get("model", "claude-sonnet-4-6")
        api_key = config.get("anthropic_api_key", worker_settings.ANTHROPIC_API_KEY)

        # -- LLM setup (exact match to executor.py lines 429-435) --
        from anthropic import AsyncAnthropic
        from browser_use.llm.anthropic.chat import ChatAnthropic

        self._llm = ChatAnthropic(
            model=self._model,
            api_key=api_key,
            timeout=60,
        )

        # CRITICAL FIX: browser-use 0.11.x creates a new AsyncAnthropic
        # client on every LLM call (get_client() returns a fresh instance).
        # This prevents TCP connection reuse / pooling, causing Anthropic's
        # load balancer to treat every request as a new client and return
        # 529 (overloaded) under even moderate load.
        # Fix: cache a single client and monkey-patch get_client().
        _cached_client = AsyncAnthropic(
            api_key=api_key, timeout=60, max_retries=3,
        )
        self._llm.get_client = lambda: _cached_client
        self._cached_anthropic_client = _cached_client  # prevent GC

        # -- BrowserSession setup (exact match to executor.py lines 441-449) --
        from browser_use.browser.session import BrowserSession

        self._browser_session = BrowserSession(
            headless=config.get("headless", True),
            enable_default_extensions=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

    async def teardown(self) -> None:
        """Close browser session and release resources."""
        if self._browser_session is not None:
            try:
                close_fn = getattr(self._browser_session, "close", None)
                if close_fn and callable(close_fn):
                    result = close_fn()
                    if inspect.isawaitable(result):
                        await result
            except Exception as exc:
                logger.debug("BrowserSession close failed: %s", exc)
            self._browser_session = None
        self._llm = None
        self._cached_anthropic_client = None
        self._last_history = None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute_step(self, intent: StepIntent) -> StepResult:
        """Not supported — BrowserUseBackend uses goal delegation only."""
        raise NotImplementedError(
            "BrowserUseBackend uses execute_goal — delegation mode only"
        )

    async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
        """Delegate a full goal to browser-use Agent.

        1. Create Agent with goal as task (exact params from executor.py)
        2. ``history = await agent.run(max_steps=max_steps)``
        3. Convert AgentHistoryList into ``list[StepResult]``

        The step callback wiring (register_new_step_callback, on_step_end)
        is preserved exactly as in executor.py.
        """
        if self._llm is None or self._browser_session is None:
            raise RuntimeError(
                "BrowserUseBackend not initialized — call initialize() first"
            )

        self._step_timestamps = []
        self._last_history = None

        # -- Re-create BrowserSession if the previous agent's BrowserStopEvent
        # destroyed the CDP connection (reset(force=True) on agent completion).
        # Without this, subsequent execute_goal() calls fail with
        # "CDP client not initialized - browser may not be connected yet".
        from browser_use.browser.session import BrowserSession

        self._browser_session = BrowserSession(
            headless=self._config.get("headless", True),
            enable_default_extensions=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        # -- Inject start URL into the goal so the agent navigates there --
        # BrowserSession doesn't create a page until Agent.run(), so we
        # can't page.goto() before the agent starts.  Instead, prepend
        # the URL to the task string — the agent will navigate on step 1.
        start_url = (self._config or {}).get("url")
        if start_url and start_url not in goal:
            goal = f"First navigate to {start_url}, then: {goal}"

        # -- Agent construction (exact match to executor.py lines 451-458) --
        from browser_use import Agent

        agent_kwargs: dict[str, Any] = {
            "task": goal,
            "llm": self._llm,
            "browser": self._browser_session,
            "register_new_step_callback": self._on_agent_step,
            "calculate_cost": True,
            "use_vision": self._config.get("use_vision", True),
        }

        # -- Structured output: convert user's output_schema dict to a
        # dynamic pydantic model and hand it to browser-use. When set,
        # browser-use forces the Agent's final `done` action to return
        # a JSON payload conforming to the schema.
        output_schema = (self._config or {}).get("output_schema")
        if output_schema:
            try:
                model_cls = _schema_dict_to_pydantic_model(output_schema)
                agent_kwargs["output_model_schema"] = model_cls
            except Exception as exc:
                logger.warning(
                    "Failed to build output_model_schema from %r: %s",
                    output_schema, exc,
                )

        agent = Agent(**agent_kwargs)
        self._current_agent = agent
        self._emitted_step_count = 0

        # -- Run agent (exact match to executor.py lines 460-468) --
        try:
            run_kwargs: dict[str, Any] = {"max_steps": max_steps}
            # browser-use 0.11+: on_step_end callback for stuck detection
            if "on_step_end" in inspect.signature(agent.run).parameters:
                run_kwargs["on_step_end"] = self._on_step_end
            history = await agent.run(**run_kwargs)
        except Exception as exc:
            # Preserve whatever partial history the agent accumulated
            # before the failure so PAV / tasks.py can still persist it.
            try:
                partial = getattr(agent, "history", None)
                if partial is not None:
                    self._last_history = partial
            except Exception:
                pass
            raise RuntimeError(f"Browser Use agent failed: {exc}") from exc
        finally:
            self._current_agent = None

        self._last_history = history
        return self._convert_history(history)

    async def get_observation(self) -> Observation:
        """Return current browser state from the agent's browser session.

        Uses the last screenshot from the most recent history if available,
        otherwise captures a fresh screenshot from the BrowserSession page.
        """
        # Try to get the latest state from history
        if self._last_history is not None:
            try:
                screenshots = self._last_history.screenshots()
                urls = self._last_history.urls()
                if screenshots:
                    shot = screenshots[-1]
                    screenshot_b64 = (
                        shot if isinstance(shot, str)
                        else base64.b64encode(shot).decode() if isinstance(shot, bytes)
                        else None
                    )
                    return Observation(
                        url=urls[-1] if urls else "",
                        screenshot_b64=screenshot_b64,
                        timestamp_ms=int(time.time() * 1000),
                    )
            except Exception:
                pass

        # Fallback: capture directly from the browser session page
        if self._browser_session is not None:
            try:
                page = self._get_current_page()
                if page is not None:
                    url = getattr(page, "url", "") or ""
                    title = ""
                    try:
                        title = await page.title()
                    except Exception:
                        pass
                    screenshot_b64 = None
                    try:
                        raw = await page.screenshot(type="png")
                        screenshot_b64 = base64.b64encode(raw).decode()
                    except Exception:
                        pass
                    viewport = getattr(page, "viewport_size", None) or {}
                    return Observation(
                        url=url,
                        page_title=title,
                        screenshot_b64=screenshot_b64,
                        timestamp_ms=int(time.time() * 1000),
                        viewport_width=viewport.get("width", 1280),
                        viewport_height=viewport.get("height", 720),
                    )
            except Exception as exc:
                logger.debug("get_observation fallback failed: %s", exc)

        return Observation()

    # ------------------------------------------------------------------
    # Callbacks (ported from executor.py lines 471-519)
    # ------------------------------------------------------------------

    def _on_agent_step(self, *args: Any, **kwargs: Any) -> None:
        """register_new_step_callback — records step start timestamp.

        Exact port from executor.py::_on_agent_step (line 471).
        """
        self._step_timestamps.append(time.monotonic())
        logger.debug(
            "BrowserUseBackend step %d started", len(self._step_timestamps),
        )

    async def _on_step_end(self, agent: Any) -> None:
        """Async hook fired after each browser-use step.

        Does two things:
        1. Real-time stuck detection (exact port from executor.py::_on_step_end).
        2. Live streaming: converts any newly-completed steps into
           ``StepResult`` objects and forwards them to
           ``self._live_step_callback`` so PAV / tasks.py sees partial
           progress BEFORE ``agent.run()`` returns. This matters for
           long-running delegations that get killed by Celery's
           ``soft_time_limit`` — without streaming, ``shared_steps``
           would be empty at the outer exception handler.
        """
        # --- 1. Stuck detection ---
        try:
            history_list = getattr(agent, "history", None)
            latest = None
            if isinstance(history_list, list) and history_list:
                latest = history_list[-1]
            if latest is not None:
                signal = self._stuck_detector.check_agent_step(latest)
                if signal.detected:
                    logger.warning(
                        "Stuck agent detected: reason=%s step=%d details=%s",
                        signal.reason,
                        signal.step_number,
                        signal.details,
                    )
                    stop_fn = getattr(agent, "stop", None)
                    if callable(stop_fn):
                        stop_fn()
        except Exception as exc:
            logger.debug("on_step_end stuck check failed: %s", exc)

        # --- 2. Live streaming of step data ---
        try:
            if self._live_step_callback is None:
                return

            # browser-use exposes agent.history as AgentHistoryList which
            # wraps .history (a plain list). Convert everything emitted so
            # far, then forward only the new entries.
            full_history = getattr(agent, "history", None)
            if full_history is None:
                return

            converted = self._convert_history(full_history)
            new_results = converted[self._emitted_step_count:]
            for sr in new_results:
                try:
                    self._live_step_callback(sr)
                except Exception as cb_exc:
                    logger.debug("live step callback failed: %s", cb_exc)
            self._emitted_step_count = len(converted)
        except Exception as exc:
            logger.debug("live streaming failed: %s", exc)

    # ------------------------------------------------------------------
    # History conversion (ported from executor.py::_enrich_steps_from_history)
    # ------------------------------------------------------------------

    def _convert_history(self, result: Any) -> List[StepResult]:
        """Convert browser-use AgentHistoryList into ``list[StepResult]``.

        Uses:
          - ``result.screenshots()``     → Observation.screenshot_b64
          - ``result.action_names()``     → side_effects (action label)
          - ``result.errors()``           → StepResult.error
          - ``result.model_actions()``    → side_effects (raw actions)
          - ``result.model_thoughts()``   → side_effects (reasoning)
          - ``result.urls()``             → Observation.url
          - ``result.final_result()``     → side_effect on last step
          - ``result.is_done()``          → side_effect on last step

        Ported from executor.py::_enrich_steps_from_history (line 588).
        """
        steps: List[StepResult] = []

        try:
            history = getattr(result, "history", None) or []

            screenshots = _safe_call(result, "screenshots")
            action_names = _safe_call(result, "action_names")
            errors = _safe_call(result, "errors")
            model_actions = _safe_call(result, "model_actions")
            model_thoughts = _safe_call(result, "model_thoughts")
            urls = _safe_call(result, "urls")
            final_result = _safe_call_scalar(result, "final_result")
            is_done = _safe_call_scalar(result, "is_done")

            for i, agent_step in enumerate(history):
                # -- Screenshot → Observation --
                screenshot_b64: Optional[str] = None
                if i < len(screenshots) and screenshots[i]:
                    shot = screenshots[i]
                    if isinstance(shot, bytes):
                        screenshot_b64 = base64.b64encode(shot).decode()
                    elif isinstance(shot, str):
                        screenshot_b64 = shot

                step_url = urls[i] if i < len(urls) and urls[i] else ""

                observation = Observation(
                    url=step_url,
                    screenshot_b64=screenshot_b64,
                    timestamp_ms=int(time.time() * 1000),
                )

                # -- Success/failure from action results --
                # (exact logic from executor.py lines 639-648)
                success = True
                error_msg: Optional[str] = None

                step_result_list = getattr(agent_step, "result", None)
                if step_result_list and isinstance(step_result_list, list):
                    err_parts: list[str] = []
                    for r in step_result_list:
                        err = getattr(r, "error", None)
                        if err:
                            err_parts.append(str(err))
                    if err_parts:
                        success = False
                        error_msg = "; ".join(err_parts[:3])

                # Fallback: errors() list
                if not error_msg and i < len(errors) and errors[i]:
                    success = False
                    error_msg = str(errors[i])

                # -- Per-step duration from StepMetadata (browser-use 0.11+) --
                # Token counts are tracked aggregate on history.usage, not per-step
                # (see https://github.com/browser-use/browser-use). We attribute
                # tokens / cost to the last step below.
                tokens_in = 0
                tokens_out = 0
                duration_ms = 0

                meta = getattr(agent_step, "metadata", None)
                if meta:
                    start_t = getattr(meta, "step_start_time", None)
                    end_t = getattr(meta, "step_end_time", None)
                    if start_t is not None and end_t is not None:
                        duration_ms = int((float(end_t) - float(start_t)) * 1000)
                    # Legacy field (pre-0.11) — keep for forward/backward compat
                    legacy_in = getattr(meta, "input_tokens", 0) or 0
                    legacy_out = getattr(meta, "output_tokens", 0) or 0
                    if legacy_in or legacy_out:
                        tokens_in = legacy_in
                        tokens_out = legacy_out

                # Fallback duration from callback timestamps
                if not duration_ms and i < len(self._step_timestamps):
                    start = self._step_timestamps[i]
                    end = (
                        self._step_timestamps[i + 1]
                        if i + 1 < len(self._step_timestamps)
                        else time.monotonic()
                    )
                    duration_ms = int((end - start) * 1000)

                # -- Side effects: action name, model thoughts, model actions --
                side_effects: list[str] = []

                if i < len(action_names) and action_names[i]:
                    side_effects.append(f"action:{action_names[i]}")

                if i < len(model_thoughts) and model_thoughts[i]:
                    side_effects.append(
                        f"thought:{str(model_thoughts[i])[:200]}"
                    )

                if i < len(model_actions) and model_actions[i]:
                    side_effects.append(
                        f"raw_action:{str(model_actions[i])[:200]}"
                    )

                # -- Description from model_output --
                # (exact logic from executor.py lines 651-661)
                mo = getattr(agent_step, "model_output", None)
                if mo:
                    next_goal = getattr(mo, "next_goal", None)
                    if next_goal:
                        side_effects.append(f"goal:{str(next_goal)[:200]}")
                    eval_prev = getattr(mo, "evaluation_previous_goal", None)
                    if eval_prev:
                        side_effects.append(f"eval:{str(eval_prev)[:200]}")

                # Tag last step with final_result and completion status.
                # NOTE: do not truncate — downstream JSON extraction needs
                # the full payload. browser-use final_result length is
                # naturally bounded by the LLM's output budget.
                if i == len(history) - 1:
                    if final_result is not None:
                        side_effects.append(
                            f"final_result:{str(final_result)}"
                        )
                    if is_done is not None:
                        side_effects.append(f"is_done:{is_done}")

                steps.append(StepResult(
                    success=success,
                    error=error_msg,
                    duration_ms=duration_ms,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    observation=observation,
                    side_effects=side_effects,
                ))

            # -- Aggregate usage (browser-use 0.11+): attribute totals to last step
            # if per-step attribution wasn't available. history.usage is a
            # UsageSummary with total_prompt_tokens / total_completion_tokens.
            # StepResult.cost_cents is a @property computed from tokens, so
            # setting tokens is sufficient for cost aggregation.
            usage = getattr(result, "usage", None)
            if usage and steps:
                per_step_populated = any(s.tokens_in or s.tokens_out for s in steps)
                if not per_step_populated:
                    total_in = int(getattr(usage, "total_prompt_tokens", 0) or 0)
                    total_out = int(getattr(usage, "total_completion_tokens", 0) or 0)
                    if total_in or total_out:
                        from dataclasses import replace
                        steps[-1] = replace(
                            steps[-1],
                            tokens_in=total_in,
                            tokens_out=total_out,
                        )

        except Exception as exc:
            logger.warning("Failed to convert browser-use history: %s", exc)

        return steps

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_current_page(self) -> Any:
        """Extract the current Playwright page from BrowserSession."""
        session = self._browser_session
        if session is None:
            return None
        # browser-use exposes current_page or browser.contexts[0].pages[-1]
        page = getattr(session, "current_page", None)
        if page is not None:
            return page
        browser = getattr(session, "browser", None)
        if browser is not None:
            contexts = getattr(browser, "contexts", [])
            if contexts:
                pages = getattr(contexts[0], "pages", [])
                if pages:
                    return pages[-1]
        return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _safe_call(obj: Any, method: str) -> list:
    """Call obj.method() returning a list, or [] on any failure."""
    fn = getattr(obj, method, None)
    if fn is None:
        return []
    try:
        result = fn()
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _safe_call_scalar(obj: Any, method: str) -> Any:
    """Call obj.method() returning a scalar, or None on any failure."""
    fn = getattr(obj, method, None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dynamic pydantic model for output_schema
# ---------------------------------------------------------------------------


def _schema_dict_to_pydantic_model(schema: dict) -> type:
    """Convert a user ``output_schema`` dict to a dynamic pydantic BaseModel.

    Input dict shape::

        {"titles": "list[str]", "count": "int", "author": "str"}

    browser-use ``Agent(output_model_schema=...)`` expects a pydantic
    ``BaseModel`` subclass. We synthesise one at runtime so the Agent's
    final ``done`` action is forced to return a JSON payload matching the
    user's requested shape. That payload is what ends up as
    ``final_result()`` and becomes ``task.result`` downstream.
    """
    from typing import Any as _Any
    from typing import Dict, List

    from pydantic import create_model

    TYPE_MAP: dict[str, _Any] = {
        "str": str,
        "string": str,
        "int": int,
        "integer": int,
        "float": float,
        "number": float,
        "bool": bool,
        "boolean": bool,
        "list": List[_Any],
        "dict": Dict[str, _Any],
        "list[str]": List[str],
        "list[int]": List[int],
        "list[float]": List[float],
        "list[bool]": List[bool],
        "list[dict]": List[Dict[str, _Any]],
        "dict[str, str]": Dict[str, str],
        "dict[str, int]": Dict[str, int],
        "dict[str, any]": Dict[str, _Any],
    }

    fields: dict[str, tuple[_Any, _Any]] = {}
    for key, type_str in schema.items():
        norm = str(type_str).lower().strip().replace(" ", "")
        python_type = TYPE_MAP.get(norm, _Any)
        # Default to None so the Agent can still finish if a field is missing
        fields[key] = (python_type, None)

    return create_model("DynamicTaskOutput", **fields)
