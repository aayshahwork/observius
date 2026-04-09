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
        from browser_use.llm.anthropic.chat import ChatAnthropic

        self._llm = ChatAnthropic(
            model=self._model,
            api_key=api_key,
            timeout=60,
        )

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

        # -- Inject start URL into the goal so the agent navigates there --
        # BrowserSession doesn't create a page until Agent.run(), so we
        # can't page.goto() before the agent starts.  Instead, prepend
        # the URL to the task string — the agent will navigate on step 1.
        start_url = (self._config or {}).get("url")
        if start_url and start_url not in goal:
            goal = f"First navigate to {start_url}, then: {goal}"

        # -- Agent construction (exact match to executor.py lines 451-458) --
        from browser_use import Agent

        agent = Agent(
            task=goal,
            llm=self._llm,
            browser=self._browser_session,
            register_new_step_callback=self._on_agent_step,
            calculate_cost=True,
            use_vision=self._config.get("use_vision", False),
        )

        # -- Run agent (exact match to executor.py lines 460-468) --
        try:
            run_kwargs: dict[str, Any] = {"max_steps": max_steps}
            # browser-use 0.11+: on_step_end callback for stuck detection
            if "on_step_end" in inspect.signature(agent.run).parameters:
                run_kwargs["on_step_end"] = self._on_step_end
            history = await agent.run(**run_kwargs)
        except Exception as exc:
            raise RuntimeError(f"Browser Use agent failed: {exc}") from exc

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
        """Async hook for real-time stuck detection during agent.run().

        Exact port from executor.py::_on_step_end (line 484).
        """
        try:
            history = getattr(agent, "history", None)
            if not history:
                return
            latest = history[-1] if isinstance(history, list) else None
            if latest is None:
                return
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

                # -- Token counts from step metadata --
                # (exact logic from executor.py lines 664-670)
                tokens_in = 0
                tokens_out = 0
                duration_ms = 0

                meta = getattr(agent_step, "metadata", None)
                if meta:
                    tokens_in = getattr(meta, "input_tokens", 0) or 0
                    tokens_out = getattr(meta, "output_tokens", 0) or 0
                    step_dur = getattr(meta, "step_duration", None)
                    if step_dur is not None:
                        duration_ms = int(step_dur * 1000)

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

                # Tag last step with final_result and completion status
                if i == len(history) - 1:
                    if final_result is not None:
                        side_effects.append(
                            f"final_result:{str(final_result)[:500]}"
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
