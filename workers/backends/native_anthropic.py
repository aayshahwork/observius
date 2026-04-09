"""
workers/backends/native_anthropic.py — Native Anthropic computer_use backend.

Full CUABackend implementation using Anthropic's computer_20251124 tool
with Playwright for browser action execution.

Ported from workers/executor.py::_execute_native() with the EXACT same
agentic loop, action handlers, stuck detection, and context trimming.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from workers.backends.protocol import BackendCapabilities
from workers.config import worker_settings
from workers.models import ActionType
from workers.shared_types import Observation, StepIntent, StepResult
from workers.stuck_detector import StuckDetector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — exact copies from executor.py
# ---------------------------------------------------------------------------

_COST_PER_M_INPUT = 3.00
_COST_PER_M_OUTPUT = 15.00

_KEY_MAP: Dict[str, str] = {
    "Return": "Enter",
    "BackSpace": "Backspace",
    "Tab": "Tab",
    "Escape": "Escape",
    "ctrl": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "super": "Meta",
    "space": " ",
    "Space": " ",
}

_MAX_SCREENSHOT_WIDTH = 1280
_MAX_CONTEXT_MESSAGES = 20
_STUCK_CHECK_INTERVAL = 5


class NativeAnthropicBackend:
    """CUABackend implementation using the Anthropic computer_use API.

    Drives a Playwright browser via pixel-coordinate actions returned by
    Claude's ``computer_20251124`` tool.  Supports both single-step
    execution AND full goal delegation.

    Ported from executor.py::_execute_native with identical:
    - Tool definitions (computer, inject_credentials, solve_captcha, done)
    - Action handlers (all 16 action types)
    - Context window trimming (first + last 20 messages)
    - Stuck detection (every 5 steps with recovery hint injection)
    - Screenshot scaling (max 1280px width)
    """

    capabilities = BackendCapabilities(
        supports_single_step=True,
        supports_goal_delegation=True,
        supports_screenshots=True,
        supports_har=False,
        supports_trace=False,
        supports_video=False,
        supports_ax_tree=False,
    )

    def __init__(self) -> None:
        self._config: dict = {}
        self._model: str = "claude-sonnet-4-6"
        self._llm_client: Any = None
        self._page: Any = None
        self._browser_context: Any = None
        self._playwright: Any = None
        self._browser: Any = None
        self._stuck_detector = StuckDetector()
        self._last_cursor_pos: Tuple[int, int] = (0, 0)
        self._scale_factor: float = 1.0
        self._scaled_width: int = 1280
        self._scaled_height: int = 720
        # Agentic loop state (persists across execute_step calls)
        self._messages: List[Dict[str, Any]] = []
        self._step_count: int = 0
        self._total_tokens_in: int = 0
        self._total_tokens_out: int = 0
        # Internal step records for stuck detection
        self._steps: list = []

    @property
    def name(self) -> str:
        return "native"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, config: dict) -> None:
        """Set up Anthropic client and Playwright browser.

        Config keys:
            model: str — Claude model (default: ``claude-sonnet-4-6``)
            anthropic_api_key: str — override (default: worker_settings)
            headless: bool — headless browser (default: True)
            url: str — initial URL to navigate to
            task: str — task description
            credentials: dict — login credentials
            output_schema: dict — JSON schema for extracted data
            max_steps: int — maximum steps (default: 50)
        """
        self._config = config
        self._model = config.get("model", "claude-sonnet-4-6")
        api_key = config.get("anthropic_api_key", worker_settings.ANTHROPIC_API_KEY)

        # -- Anthropic client setup --
        import anthropic

        self._llm_client = anthropic.AsyncAnthropic(api_key=api_key)

        # -- Playwright browser setup --
        if config.get("page"):
            # External page injection (for testing or shared browser)
            self._page = config["page"]
        else:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=config.get("headless", True),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            self._browser_context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
            )
            self._page = await self._browser_context.new_page()

            url = config.get("url")
            if url:
                await self._page.goto(url, wait_until="domcontentloaded")

        # -- Screenshot scaling setup (exact match to executor.py lines 873-882) --
        viewport = self._page.viewport_size or {"width": 1280, "height": 720}
        vp_width, vp_height = viewport["width"], viewport["height"]
        if vp_width > _MAX_SCREENSHOT_WIDTH:
            self._scale_factor = _MAX_SCREENSHOT_WIDTH / vp_width
            self._scaled_width = _MAX_SCREENSHOT_WIDTH
            self._scaled_height = int(vp_height * self._scale_factor)
        else:
            self._scale_factor = 1.0
            self._scaled_width = vp_width
            self._scaled_height = vp_height

    async def teardown(self) -> None:
        """Close browser and release resources."""
        if self._browser_context is not None:
            try:
                await self._browser_context.close()
            except Exception as exc:
                logger.debug("Browser context close failed: %s", exc)
            self._browser_context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.debug("Browser close failed: %s", exc)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.debug("Playwright stop failed: %s", exc)
            self._playwright = None
        self._page = None
        self._llm_client = None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute_step(self, intent: StepIntent) -> StepResult:
        """Execute a single step in the native CUA loop.

        Takes a screenshot, calls Claude, executes the returned action,
        returns a StepResult with post-action observation.
        """
        if self._llm_client is None or self._page is None:
            raise RuntimeError(
                "NativeAnthropicBackend not initialized — call initialize() first"
            )

        step_start = time.monotonic()
        self._step_count += 1

        # -- Take screenshot --
        raw_screenshot = await self._page.screenshot(type="png")
        scaled_screenshot, _ = _scale_screenshot(raw_screenshot, _MAX_SCREENSHOT_WIDTH)
        screenshot_b64 = base64.b64encode(scaled_screenshot).decode()

        # -- Build user message --
        if not self._messages:
            task = self._config.get("task", intent.description or "Complete the task")
            user_content: List[Dict[str, Any]] = [
                {"type": "text", "text": f"Complete this task: {task}"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_b64,
                    },
                },
            ]
            self._messages.append({"role": "user", "content": user_content})

        # -- Context window trimming (exact match to executor.py lines 991-1002) --
        if len(self._messages) > _MAX_CONTEXT_MESSAGES:
            trimmed = len(self._messages) - _MAX_CONTEXT_MESSAGES
            self._messages = [self._messages[0]] + self._messages[-(_MAX_CONTEXT_MESSAGES - 1):]
            logger.debug("Trimmed %d messages from context", trimmed)

        # -- Call Claude (exact match to executor.py lines 1004-1020) --
        from workers.retry import retry_with_backoff

        try:
            from anthropic import APIStatusError
            _retriable = (APIStatusError, ConnectionError, TimeoutError)
        except ImportError:
            _retriable = (ConnectionError, TimeoutError)

        tools = self._build_tools()
        system_prompt = self._build_system_prompt()

        response = await retry_with_backoff(
            self._llm_client.beta.messages.create,
            model=self._model,
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=self._messages,
            betas=["computer-use-2025-11-24"],
            retriable_exceptions=_retriable,
        )

        # -- Track tokens --
        step_tokens_in = getattr(response.usage, "input_tokens", 0)
        step_tokens_out = getattr(response.usage, "output_tokens", 0)
        self._total_tokens_in += step_tokens_in
        self._total_tokens_out += step_tokens_out

        # -- Append assistant response --
        self._messages.append({"role": "assistant", "content": response.content})

        # -- Process tool calls (exact match to executor.py lines 1032-1168) --
        tool_results: List[Dict[str, Any]] = []
        step_success = True
        step_error: Optional[str] = None
        action_description = ""
        action_type = ActionType.UNKNOWN
        post_screenshot_b64: Optional[str] = None

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input

            try:
                if tool_name == "computer":
                    action_description, action_type = await self._execute_computer_action(
                        tool_input,
                    )
                    # Post-action screenshot
                    post_raw = await self._page.screenshot(type="png")
                    scaled_post, _ = _scale_screenshot(post_raw, _MAX_SCREENSHOT_WIDTH)
                    post_screenshot_b64 = base64.b64encode(scaled_post).decode()

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": post_screenshot_b64,
                            },
                        }],
                    })

                elif tool_name == "inject_credentials":
                    from workers.credential_injector import CredentialInjector

                    injector = CredentialInjector()
                    await injector.inject(self._page, self._config.get("credentials") or {})
                    action_description = "Credentials injected"
                    action_type = ActionType.INJECT_CREDENTIALS
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{"type": "text", "text": "Credentials injected successfully."}],
                    })

                elif tool_name == "solve_captcha":
                    from workers.captcha_solver import CaptchaSolver

                    solver = CaptchaSolver(worker_settings.TWOCAPTCHA_API_KEY)
                    captcha_type = tool_input.get("captcha_type")
                    captcha_result = await solver.solve(self._page, captcha_type)
                    action_type = ActionType.SOLVE_CAPTCHA
                    if captcha_result.solved:
                        action_description = f"Solved {captcha_result.captcha_type} captcha"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{
                                "type": "text",
                                "text": f"CAPTCHA solved ({captcha_result.captcha_type}).",
                            }],
                        })
                    else:
                        step_success = False
                        step_error = f"CAPTCHA solve failed: {captcha_result.error}"
                        action_description = step_error
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": step_error}],
                            "is_error": True,
                        })

                elif tool_name == "done":
                    raw_result = tool_input.get("result")
                    action_description = tool_input.get("message", "Task completed")
                    action_type = ActionType.EXTRACT

                    if raw_result and self._config.get("output_schema"):
                        try:
                            from workers.output_validator import OutputValidator, ValidationError

                            validator = OutputValidator()
                            raw_result = validator.validate(
                                raw_result, self._config["output_schema"],
                            )
                        except ValidationError as val_err:
                            schema_str = json.dumps(self._config["output_schema"], indent=2)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": [{
                                    "type": "text",
                                    "text": (
                                        f"Validation failed: {val_err.message}\n"
                                        f"Please call done again with a result "
                                        f"matching: {schema_str}"
                                    ),
                                }],
                                "is_error": True,
                            })
                            continue

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{"type": "text", "text": "Task marked as done."}],
                    })

                else:
                    action_description = f"Unknown tool: {tool_name}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                        "is_error": True,
                    })

            except Exception as exc:
                step_success = False
                step_error = str(exc)
                action_description = f"Error: {exc}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "is_error": True,
                })

        # -- Append tool results as next user message --
        if tool_results:
            self._messages.append({"role": "user", "content": tool_results})

        # -- Stuck detection (periodic, exact match to executor.py lines 1202-1233) --
        if self._step_count % _STUCK_CHECK_INTERVAL == 0 and self._steps:
            stuck_signal = self._stuck_detector.analyze_full_history(self._steps)
            if stuck_signal.detected:
                logger.warning(
                    "Native backend stuck: reason=%s step=%d details=%s",
                    stuck_signal.reason,
                    stuck_signal.step_number,
                    stuck_signal.details,
                )
                recovery_hint = (
                    f"You appear stuck ({stuck_signal.reason}: {stuck_signal.details}). "
                    "Try a completely different approach to accomplish the task."
                )
                if self._messages and self._messages[-1]["role"] == "user":
                    content = self._messages[-1]["content"]
                    if isinstance(content, list):
                        content.append({"type": "text", "text": recovery_hint})
                    else:
                        self._messages[-1]["content"] = [
                            {"type": "text", "text": str(content)},
                            {"type": "text", "text": recovery_hint},
                        ]

        duration_ms = int((time.monotonic() - step_start) * 1000)

        # Build observation from post-action state
        observation = Observation(
            url=self._page.url or "",
            screenshot_b64=post_screenshot_b64 or screenshot_b64,
            timestamp_ms=int(time.time() * 1000),
            viewport_width=self._scaled_width,
            viewport_height=self._scaled_height,
        )
        try:
            observation.page_title = await self._page.title()
        except Exception:
            pass

        side_effects = []
        if action_description:
            side_effects.append(f"action:{action_description}")

        result = StepResult(
            success=step_success,
            error=step_error,
            duration_ms=duration_ms,
            tokens_in=step_tokens_in,
            tokens_out=step_tokens_out,
            observation=observation,
            side_effects=side_effects,
        )

        # Record step internally for stuck detection
        from workers.models import StepData
        from datetime import datetime, timezone

        self._steps.append(StepData(
            step_number=self._step_count,
            timestamp=datetime.now(timezone.utc),
            action_type=action_type,
            description=action_description,
            screenshot_bytes=raw_screenshot,
            tokens_in=step_tokens_in,
            tokens_out=step_tokens_out,
            duration_ms=duration_ms,
            success=step_success,
            error=step_error,
        ))

        return result

    async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
        """Run the full native Claude computer_use agentic loop.

        Ported from executor.py::_execute_native (lines 863-1250).
        """
        if self._llm_client is None or self._page is None:
            raise RuntimeError(
                "NativeAnthropicBackend not initialized — call initialize() first"
            )

        from workers.retry import retry_with_backoff
        from workers.models import StepData
        from datetime import datetime, timezone

        self._messages = []
        self._step_count = 0
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._steps = []
        self._last_cursor_pos = (0, 0)
        self._stuck_detector = StuckDetector()

        tools = self._build_tools()
        system_prompt = self._build_system_prompt()

        try:
            from anthropic import APIStatusError
            _retriable = (APIStatusError, ConnectionError, TimeoutError)
        except ImportError:
            _retriable = (ConnectionError, TimeoutError)

        results: List[StepResult] = []
        config_max_steps = self._config.get("max_steps", max_steps)
        effective_max_steps = min(max_steps, config_max_steps)

        for step_num in range(1, effective_max_steps + 1):
            step_start = time.monotonic()
            self._step_count = step_num

            # -- Take screenshot --
            raw_screenshot = await self._page.screenshot(type="png")
            scaled_screenshot, _ = _scale_screenshot(raw_screenshot, _MAX_SCREENSHOT_WIDTH)
            screenshot_b64 = base64.b64encode(scaled_screenshot).decode()

            # -- Build user message (exact match to executor.py lines 975-988) --
            if not self._messages:
                task = self._config.get("task", goal)
                user_content: List[Dict[str, Any]] = [
                    {"type": "text", "text": f"Complete this task: {task}"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                ]
                self._messages.append({"role": "user", "content": user_content})

            # -- Context window trimming (exact match to executor.py lines 991-1002) --
            if len(self._messages) > _MAX_CONTEXT_MESSAGES:
                trimmed = len(self._messages) - _MAX_CONTEXT_MESSAGES
                self._messages = [self._messages[0]] + self._messages[-(_MAX_CONTEXT_MESSAGES - 1):]
                logger.debug("Trimmed %d messages from context", trimmed)

            # -- Call Claude (exact match to executor.py lines 1011-1020) --
            response = await retry_with_backoff(
                self._llm_client.beta.messages.create,
                model=self._model,
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=self._messages,
                betas=["computer-use-2025-11-24"],
                retriable_exceptions=_retriable,
            )

            # -- Track tokens --
            step_tokens_in = getattr(response.usage, "input_tokens", 0)
            step_tokens_out = getattr(response.usage, "output_tokens", 0)
            self._total_tokens_in += step_tokens_in
            self._total_tokens_out += step_tokens_out

            # -- Append assistant response --
            self._messages.append({"role": "assistant", "content": response.content})

            # -- Process tool calls (exact match to executor.py lines 1032-1168) --
            tool_results: List[Dict[str, Any]] = []
            done = False

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                action_description = ""
                action_type = ActionType.UNKNOWN
                step_success = True
                step_error: Optional[str] = None
                step_screenshot: Optional[bytes] = None
                post_screenshot_b64: Optional[str] = None

                try:
                    if tool_name == "computer":
                        action_description, action_type = await self._execute_computer_action(
                            tool_input,
                        )
                        post_screenshot = await self._page.screenshot(type="png")
                        scaled_post, _ = _scale_screenshot(post_screenshot, _MAX_SCREENSHOT_WIDTH)
                        post_screenshot_b64 = base64.b64encode(scaled_post).decode()
                        step_screenshot = post_screenshot

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": post_screenshot_b64,
                                },
                            }],
                        })

                    elif tool_name == "inject_credentials":
                        from workers.credential_injector import CredentialInjector

                        injector = CredentialInjector()
                        await injector.inject(self._page, self._config.get("credentials") or {})
                        action_description = "Credentials injected"
                        action_type = ActionType.INJECT_CREDENTIALS
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": "Credentials injected successfully."}],
                        })

                    elif tool_name == "solve_captcha":
                        from workers.captcha_solver import CaptchaSolver

                        solver = CaptchaSolver(worker_settings.TWOCAPTCHA_API_KEY)
                        captcha_type = tool_input.get("captcha_type")
                        captcha_result = await solver.solve(self._page, captcha_type)
                        action_type = ActionType.SOLVE_CAPTCHA
                        if captcha_result.solved:
                            action_description = f"Solved {captcha_result.captcha_type} captcha"
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": [{
                                    "type": "text",
                                    "text": f"CAPTCHA solved ({captcha_result.captcha_type}).",
                                }],
                            })
                        else:
                            step_success = False
                            step_error = f"CAPTCHA solve failed: {captcha_result.error}"
                            action_description = step_error
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": [{"type": "text", "text": step_error}],
                                "is_error": True,
                            })

                    elif tool_name == "done":
                        raw_result = tool_input.get("result")
                        action_description = tool_input.get("message", "Task completed")
                        action_type = ActionType.EXTRACT

                        if raw_result and self._config.get("output_schema"):
                            try:
                                from workers.output_validator import OutputValidator, ValidationError

                                validator = OutputValidator()
                                raw_result = validator.validate(
                                    raw_result, self._config["output_schema"],
                                )
                            except ValidationError as val_err:
                                schema_str = json.dumps(self._config["output_schema"], indent=2)
                                logger.warning(
                                    "Output validation failed (will retry): %s",
                                    val_err.message,
                                )
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": [{
                                        "type": "text",
                                        "text": (
                                            f"Validation failed: {val_err.message}\n"
                                            f"Please call done again with a result "
                                            f"matching: {schema_str}"
                                        ),
                                    }],
                                    "is_error": True,
                                })
                                continue

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": "Task marked as done."}],
                        })
                        done = True

                    else:
                        action_description = f"Unknown tool: {tool_name}"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                            "is_error": True,
                        })

                except Exception as exc:
                    step_success = False
                    step_error = str(exc)
                    action_description = f"Error: {exc}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "is_error": True,
                    })

                # -- Build StepResult --
                duration_ms = int((time.monotonic() - step_start) * 1000)

                observation = Observation(
                    url=self._page.url or "",
                    screenshot_b64=post_screenshot_b64 or screenshot_b64,
                    timestamp_ms=int(time.time() * 1000),
                    viewport_width=self._scaled_width,
                    viewport_height=self._scaled_height,
                )
                try:
                    observation.page_title = await self._page.title()
                except Exception:
                    pass

                side_effects = []
                if action_description:
                    side_effects.append(f"action:{action_description}")
                if done:
                    side_effects.append("is_done:True")
                    if raw_result is not None:
                        side_effects.append(f"result:{json.dumps(raw_result)[:500]}")

                results.append(StepResult(
                    success=step_success,
                    error=step_error,
                    duration_ms=duration_ms,
                    tokens_in=step_tokens_in,
                    tokens_out=step_tokens_out,
                    observation=observation,
                    side_effects=side_effects,
                ))

                # Internal StepData for stuck detector
                self._steps.append(StepData(
                    step_number=len(self._steps) + 1,
                    timestamp=datetime.now(timezone.utc),
                    action_type=action_type,
                    description=action_description,
                    screenshot_bytes=step_screenshot or raw_screenshot,
                    tokens_in=step_tokens_in,
                    tokens_out=step_tokens_out,
                    duration_ms=duration_ms,
                    success=step_success,
                    error=step_error,
                ))

            if done:
                break

            # -- Append tool results as next user message --
            if tool_results:
                self._messages.append({"role": "user", "content": tool_results})

            # -- Stuck detection (periodic, exact match to executor.py lines 1202-1233) --
            if step_num % _STUCK_CHECK_INTERVAL == 0 and self._steps:
                stuck_signal = self._stuck_detector.analyze_full_history(self._steps)
                if stuck_signal.detected:
                    logger.warning(
                        "Native backend stuck: reason=%s step=%d details=%s",
                        stuck_signal.reason,
                        stuck_signal.step_number,
                        stuck_signal.details,
                    )
                    recovery_hint = (
                        f"You appear stuck ({stuck_signal.reason}: {stuck_signal.details}). "
                        "Try a completely different approach to accomplish the task."
                    )
                    if self._messages and self._messages[-1]["role"] == "user":
                        content = self._messages[-1]["content"]
                        if isinstance(content, list):
                            content.append({"type": "text", "text": recovery_hint})
                        else:
                            self._messages[-1]["content"] = [
                                {"type": "text", "text": str(content)},
                                {"type": "text", "text": recovery_hint},
                            ]

            # If response has stop_reason == "end_turn" with no tool calls, we're done
            if response.stop_reason == "end_turn" and not tool_results:
                break

        return results

    async def get_observation(self) -> Observation:
        """Return current browser state without acting."""
        if self._page is None:
            return Observation()

        url = self._page.url or ""
        title = ""
        screenshot_b64 = None
        viewport = self._page.viewport_size or {}

        try:
            title = await self._page.title()
        except Exception:
            pass

        try:
            raw = await self._page.screenshot(type="png")
            screenshot_b64 = base64.b64encode(raw).decode()
        except Exception:
            pass

        return Observation(
            url=url,
            page_title=title,
            screenshot_b64=screenshot_b64,
            timestamp_ms=int(time.time() * 1000),
            viewport_width=viewport.get("width", 1280),
            viewport_height=viewport.get("height", 720),
        )

    # ------------------------------------------------------------------
    # Tool definitions (exact match to executor.py lines 884-949)
    # ------------------------------------------------------------------

    def _build_tools(self) -> List[Dict[str, Any]]:
        """Build tool definitions for the Anthropic messages API."""
        tools: List[Dict[str, Any]] = [
            {
                "type": "computer_20251124",
                "name": "computer",
                "display_width_px": self._scaled_width,
                "display_height_px": self._scaled_height,
                "display_number": 1,
            },
        ]
        if self._config.get("credentials"):
            tools.append({
                "name": "inject_credentials",
                "description": (
                    "Inject stored login credentials into the current page. "
                    "Call this when you see a login form. The system will "
                    "securely fill in the credentials — do NOT type them yourself."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "The domain of the login page.",
                        },
                    },
                    "required": ["domain"],
                },
            })
        tools.append({
            "name": "solve_captcha",
            "description": (
                "Solve a CAPTCHA on the current page "
                "(reCAPTCHA v2, hCaptcha, or Cloudflare Turnstile)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "captcha_type": {
                        "type": "string",
                        "enum": ["recaptcha_v2", "hcaptcha", "turnstile"],
                        "description": "CAPTCHA type. Omit to auto-detect.",
                    },
                },
            },
        })
        tools.append({
            "name": "done",
            "description": (
                "Signal that the task is complete. Include extracted data "
                "if an output schema was specified."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "description": "Extracted data matching the output schema.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Completion summary.",
                    },
                },
            },
        })
        return tools

    # ------------------------------------------------------------------
    # System prompt (exact match to executor.py lines 1374-1415)
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Build the system prompt for native computer_use mode."""
        config = self._config
        url = config.get("url", "")
        task = config.get("task", "")
        target_domain = urlparse(url).netloc if url else ""
        max_steps = config.get("max_steps", 50)

        sections = [
            (
                "You are a browser automation agent. You see screenshots of a web page "
                "and use the computer tool to interact with it via mouse clicks, keyboard "
                "input, and scrolling. You work with pixel coordinates from the screenshots."
            ),
            f"TARGET URL: {url}",
            f"TASK: {task}",
            (
                f"SAFETY: Stay on {target_domain} unless absolutely necessary. "
                "Do not download files, make purchases, or take irreversible actions."
            ),
        ]

        if config.get("credentials"):
            sections.append(
                "CREDENTIALS: When you see a login form, use the inject_credentials tool. "
                "The system will fill in the credentials securely. Do NOT type passwords yourself."
            )

        sections.append(
            "CAPTCHA: If you encounter a CAPTCHA challenge, use the solve_captcha tool."
        )

        output_schema = config.get("output_schema")
        if output_schema:
            schema_str = json.dumps(output_schema, indent=2)
            sections.append(
                'OUTPUT SCHEMA: When the task is complete, call the "done" tool with a '
                f'"result" object matching this schema:\n{schema_str}'
            )

        sections.append(
            f'When the task is complete, call the "done" tool. '
            f"You have at most {max_steps} actions."
        )

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Action execution (exact match to executor.py lines 1252-1372)
    # ------------------------------------------------------------------

    async def _execute_computer_action(
        self,
        action_input: Dict[str, Any],
    ) -> Tuple[str, ActionType]:
        """Map a computer_20251124 tool action to a Playwright call.

        Returns (description, action_type).
        """
        page = self._page
        scale_factor = self._scale_factor
        action = action_input.get("action", "")

        def _remap(coord: List[int]) -> Tuple[int, int]:
            rx = int(coord[0] / scale_factor) if scale_factor != 1.0 else coord[0]
            ry = int(coord[1] / scale_factor) if scale_factor != 1.0 else coord[1]
            self._last_cursor_pos = (rx, ry)
            return rx, ry

        if action == "key":
            key = action_input.get("text", "")
            mapped = _KEY_MAP.get(key, key)
            await page.keyboard.press(mapped)
            return f"Pressed key: {key}", ActionType.KEY_PRESS

        elif action == "type":
            text = action_input.get("text", "")
            await page.keyboard.type(text)
            return f"Typed: {text[:50]}", ActionType.TYPE

        elif action == "cursor_position":
            x, y = self._last_cursor_pos
            return f"Cursor at ({x}, {y})", ActionType.MOUSE_MOVE

        elif action == "mouse_move":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.move(rx, ry)
            return f"Moved mouse to ({rx}, {ry})", ActionType.MOUSE_MOVE

        elif action == "left_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.click(rx, ry)
            return f"Clicked at ({rx}, {ry})", ActionType.CLICK

        elif action == "left_click_drag":
            start_coord = action_input.get("start_coordinate", [0, 0])
            end_coord = action_input.get("coordinate", [0, 0])
            sx, sy = _remap(start_coord)
            ex, ey = _remap(end_coord)
            await page.mouse.move(sx, sy)
            await page.mouse.down()
            await page.mouse.move(ex, ey)
            await page.mouse.up()
            return f"Dragged from ({sx}, {sy}) to ({ex}, {ey})", ActionType.DRAG

        elif action == "right_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.click(rx, ry, button="right")
            return f"Right-clicked at ({rx}, {ry})", ActionType.RIGHT_CLICK

        elif action == "double_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.dblclick(rx, ry)
            return f"Double-clicked at ({rx}, {ry})", ActionType.DOUBLE_CLICK

        elif action == "triple_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.click(rx, ry, click_count=3)
            return f"Triple-clicked at ({rx}, {ry})", ActionType.TRIPLE_CLICK

        elif action == "middle_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.click(rx, ry, button="middle")
            return f"Middle-clicked at ({rx}, {ry})", ActionType.MIDDLE_CLICK

        elif action == "screenshot":
            return "Took screenshot", ActionType.SCREENSHOT

        elif action == "scroll":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            direction = action_input.get("direction", "down")
            amount = action_input.get("amount", 3)
            delta_y = amount * 100 * (1 if direction == "down" else -1)
            await page.mouse.move(rx, ry)
            await page.mouse.wheel(0, delta_y)
            return f"Scrolled {direction} {amount} clicks at ({rx}, {ry})", ActionType.SCROLL

        elif action == "wait":
            seconds = min(action_input.get("duration", 2), 10)
            await asyncio.sleep(seconds)
            return f"Waited {seconds}s", ActionType.WAIT

        elif action == "hold_key":
            keys = action_input.get("key", "")
            mapped_key = _KEY_MAP.get(keys, keys)
            await page.keyboard.down(mapped_key)
            try:
                nested = action_input.get("action")
                if nested and isinstance(nested, dict):
                    await self._execute_computer_action(nested)
            finally:
                await page.keyboard.up(mapped_key)
            return f"Held key: {keys}", ActionType.KEY_PRESS

        elif action == "zoom":
            amount = action_input.get("amount", 1)
            zoom_key = "+" if amount > 0 else "-"
            for _ in range(abs(amount)):
                await page.keyboard.press(f"Control+{zoom_key}")
            return f"Zoomed {'in' if amount > 0 else 'out'} {abs(amount)}x", ActionType.ZOOM

        return f"Unknown action: {action}", ActionType.UNKNOWN


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _scale_screenshot(
    png_bytes: bytes, max_width: int = _MAX_SCREENSHOT_WIDTH,
) -> Tuple[bytes, float]:
    """Scale a PNG screenshot to fit within *max_width*.

    Returns (scaled_png_bytes, scale_factor).
    Exact copy from executor.py lines 1418-1439.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    width, height = img.size

    if width <= max_width:
        return png_bytes, 1.0

    scale = max_width / width
    new_width = max_width
    new_height = int(height * scale)
    img_resized = img.resize((new_width, new_height), Image.LANCZOS)

    buf = io.BytesIO()
    img_resized.save(buf, format="PNG")
    return buf.getvalue(), scale
