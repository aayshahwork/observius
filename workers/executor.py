"""
workers/executor.py — Core task execution engine with Anthropic tool-use API.

The TaskExecutor acquires a browser, navigates to a URL, runs a screenshot-based
LLM agent loop using Anthropic's tool-use API, captures step data, and returns
structured results.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from rich.console import Console

from workers.browser_manager import BrowserManager
from workers.captcha_solver import CaptchaSolver
from workers.config import worker_settings
from workers.credential_injector import CredentialInjector
from workers.models import ActionType, StepData, TaskConfig, TaskResult

logger = logging.getLogger(__name__)
console = Console()


class TaskExecutionError(RuntimeError):
    """Raised when the browser_use agent fails during task execution."""


# Claude Sonnet pricing (per million tokens).
_COST_PER_M_INPUT = 3.00
_COST_PER_M_OUTPUT = 15.00

# Tool definitions for the Anthropic messages API.
_TOOLS = [
    {
        "name": "navigate",
        "description": "Navigate the browser to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The URL to navigate to."}},
            "required": ["url"],
        },
    },
    {
        "name": "click",
        "description": "Click an element on the page identified by CSS selector.",
        "input_schema": {
            "type": "object",
            "properties": {"selector": {"type": "string", "description": "CSS selector of the element to click."}},
            "required": ["selector"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into an input element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the input element."},
                "text": {"type": "string", "description": "The text to type."},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the page up or down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction."},
                "pixels": {"type": "integer", "description": "Pixels to scroll. Default 300.", "default": 300},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "wait",
        "description": "Wait for a short duration.",
        "input_schema": {
            "type": "object",
            "properties": {"seconds": {"type": "number", "description": "Seconds to wait (max 10).", "maximum": 10}},
            "required": ["seconds"],
        },
    },
    {
        "name": "inject_credentials",
        "description": (
            "Fill in login credentials on the current page. Use this when you see a login form. "
            "Provide CSS selectors for the username and password fields. "
            "The system will securely inject the credentials — do NOT type them yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "username_selector": {"type": "string", "description": "CSS selector for the username/email input."},
                "password_selector": {"type": "string", "description": "CSS selector for the password input."},
            },
        },
    },
    {
        "name": "solve_captcha",
        "description": (
            "Detect and solve a CAPTCHA on the current page "
            "(reCAPTCHA v2, hCaptcha, or Cloudflare Turnstile). "
            "Auto-detects type if not specified."
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
    },
    {
        "name": "done",
        "description": "Signal that the task is complete. Include extracted result data if an output schema was specified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "result": {"type": "object", "description": "Extracted data matching the output schema."},
                "message": {"type": "string", "description": "Completion summary."},
            },
        },
    },
]


class TaskExecutor:
    """Screenshot-based LLM agent loop using the Anthropic tool-use API."""

    def __init__(
        self,
        config: TaskConfig,
        browser_manager: BrowserManager,
        llm_client: Any,
        use_cloud: bool = False,
        shutdown_check: Optional[Callable[[], bool]] = None,
        step_data: Optional[List[StepData]] = None,
        model: str = "claude-sonnet-4-5-20250514",
    ) -> None:
        self.config = config
        self.browser_manager = browser_manager
        self.llm_client = llm_client
        self.use_cloud = use_cloud
        self.shutdown_check = shutdown_check
        self._shared_step_data = step_data
        self.model = model
        self.steps: List[StepData] = []

    async def execute(self) -> TaskResult:
        """Execute the task end-to-end.

        1. Generate task_id, record start_time.
        2. Acquire browser, create page (1280x720), apply stealth.
        3. Navigate to config.url, capture step 1.
        4. Run browser_use Agent via _execute_with_agent.
        5. Cleanup browser in finally block.
        6. Return TaskResult.
        """
        task_id = str(uuid.uuid4())
        start_time = time.monotonic()
        # Use shared list if provided (allows shutdown handler to see
        # accumulated steps for partial replay generation).
        self.steps = self._shared_step_data if self._shared_step_data is not None else []
        browser = None

        try:
            # -- Step 2: Browser setup --
            browser = await self.browser_manager.get_browser(self.use_cloud)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            await self.browser_manager.apply_stealth(page, task_id)

            # -- Step 3: Navigate --
            step_start = time.monotonic()
            await page.goto(self.config.url, wait_until="networkidle", timeout=30_000)
            screenshot_bytes = await page.screenshot(type="jpeg", quality=85)
            self.steps.append(StepData(
                step_number=1,
                timestamp=datetime.now(timezone.utc),
                action_type=ActionType.NAVIGATE,
                description=f"Navigated to {self.config.url}",
                screenshot_bytes=screenshot_bytes,
                duration_ms=int((time.monotonic() - step_start) * 1000),
                success=True,
            ))

            # -- Step 4: Run browser_use Agent --
            raw_result = await self._execute_with_agent(browser, self.config)
            result_data = None
            if hasattr(raw_result, "final_result"):
                result_data = raw_result.final_result()

            return TaskResult(
                task_id=task_id,
                status="completed",
                success=True,
                result=result_data,
                steps=len(self.steps),
                duration_ms=int((time.monotonic() - start_time) * 1000),
                cost_cents=0.0,
                step_data=self.steps,
            )

        except Exception as exc:
            logger.exception("Task %s failed: %s", task_id, exc)
            return TaskResult(
                task_id=task_id,
                status="failed",
                success=False,
                error=str(exc),
                steps=len(self.steps),
                duration_ms=int((time.monotonic() - start_time) * 1000),
                cost_cents=0.0,
                step_data=self.steps,
            )

        finally:
            if browser is not None:
                try:
                    await self.browser_manager.release_browser(browser)
                except Exception as exc:
                    logger.warning("Error releasing browser: %s", exc)

    async def _execute_with_agent(
        self, browser: Any, config: TaskConfig
    ) -> Any:
        prompt = self._build_task_prompt(config)

        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(
            model_name=self.model,
            anthropic_api_key=worker_settings.ANTHROPIC_API_KEY,
            timeout=60,
        )

        from browser_use import Agent
        agent = Agent(
            task=prompt,
            llm=llm,
            browser=browser,
            register_new_step_callback=self._on_agent_step,
        )

        try:
            result = await agent.run(max_steps=config.max_steps)
            return result
        except Exception as exc:
            raise TaskExecutionError(
                f"Browser Use agent failed: {exc}"
            ) from exc

    def _on_agent_step(self, *args: Any, **kwargs: Any) -> None:
        step_number = len(self.steps) + 1
        step = StepData(
            step_number=step_number,
            timestamp=datetime.now(timezone.utc),
            action_type=ActionType.UNKNOWN,
            description=str(args[0]) if args else "step",
            screenshot_bytes=None,
            success=True,
        )
        self.steps.append(step)
        console.log(f"[dim]Step {step_number}[/]")

    def _build_task_prompt(self, config: TaskConfig) -> str:
        """Build the task prompt passed to the browser_use Agent."""
        lines = [
            f"Go to {config.url} and complete the following task:",
            config.task,
        ]
        if config.output_schema:
            lines.append(
                f"Extract data matching this schema: {json.dumps(config.output_schema)}"
            )
        if config.credentials:
            lines.append(
                "When you encounter a login form, use the available credential injection tool."
            )
        return "\n\n".join(lines)

    async def _execute_tool(self, page: Any, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Dispatch a tool call to the corresponding Playwright action. Returns description."""
        if tool_name == "navigate":
            url = tool_input["url"]
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            captcha_msg = await self._auto_detect_captcha(page)
            suffix = f" {captcha_msg}" if captcha_msg else ""
            return f"Navigated to {url}{suffix}"

        elif tool_name == "click":
            selector = tool_input["selector"]
            await page.click(selector, timeout=5000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            captcha_msg = await self._auto_detect_captcha(page)
            suffix = f" {captcha_msg}" if captcha_msg else ""
            return f"Clicked {selector}{suffix}"

        elif tool_name == "type_text":
            selector = tool_input["selector"]
            text = tool_input["text"]
            await page.fill(selector, text, timeout=5000)
            return f"Typed into {selector}"

        elif tool_name == "scroll":
            direction = tool_input["direction"]
            pixels = tool_input.get("pixels", 300)
            delta = pixels if direction == "down" else -pixels
            await page.evaluate(f"window.scrollBy(0, {delta})")
            return f"Scrolled {direction} {pixels}px"

        elif tool_name == "wait":
            seconds = min(tool_input.get("seconds", 2), 10)
            await page.wait_for_timeout(int(seconds * 1000))
            return f"Waited {seconds}s"

        elif tool_name == "inject_credentials":
            injector = CredentialInjector()
            selectors: dict[str, str] | None = None
            u_sel = tool_input.get("username_selector")
            p_sel = tool_input.get("password_selector")
            if u_sel or p_sel:
                selectors = {}
                if u_sel:
                    selectors["username_selector"] = str(u_sel)
                if p_sel:
                    selectors["password_selector"] = str(p_sel)
            await injector.inject(page, self.config.credentials or {}, selectors=selectors)
            return "Credentials injected"

        elif tool_name == "solve_captcha":
            solver = CaptchaSolver(worker_settings.TWOCAPTCHA_API_KEY)
            captcha_type = tool_input.get("captcha_type")
            result = await solver.solve(page, captcha_type)
            if result.solved:
                return f"Solved {result.captcha_type} captcha in {result.duration_ms}ms"
            raise RuntimeError(f"Failed to solve captcha: {result.error}")

        return f"Unknown tool: {tool_name}"

    async def _auto_detect_captcha(self, page: Any) -> str:
        """Check for CAPTCHA after navigation/click. Solve if found. Returns status message or empty."""
        try:
            solver = CaptchaSolver(worker_settings.TWOCAPTCHA_API_KEY)
            captcha_type = await solver.detect_captcha(page)
            if captcha_type is None:
                return ""
            logger.info("captcha_auto_detected type=%s", captcha_type)
            result = await solver.solve(page, captcha_type)
            if result.solved:
                return f"(auto-solved {result.captcha_type} captcha in {result.duration_ms}ms)"
            return f"(captcha detected: {result.captcha_type}, solve failed: {result.error})"
        except Exception as exc:
            logger.warning("captcha_auto_detect_error error=%s", exc)
            return ""

    def _build_system_prompt(self, config: TaskConfig) -> str:
        """Build the system prompt. Credentials NEVER appear here."""
        target_domain = urlparse(config.url).netloc

        sections = [
            (
                "You are a browser automation agent. You observe screenshots of a web page "
                "and use the provided tools to complete the user's task. "
                "Choose one tool per turn."
            ),
            (
                f"SAFETY: Do not navigate to domains other than {target_domain} "
                "unless absolutely necessary. Do not download files, make purchases, "
                "or take irreversible actions."
            ),
            (
                "CREDENTIALS: When you encounter a login form, use the inject_credentials tool "
                "with the CSS selectors for the username and password fields. "
                "The system will inject credentials securely. Do NOT type credentials yourself."
            ),
            (
                "CAPTCHA: If you encounter a CAPTCHA challenge (reCAPTCHA, hCaptcha, Turnstile), "
                "use the solve_captcha tool. Auto-detects type if not specified."
            ),
        ]

        if config.output_schema:
            schema_str = json.dumps(config.output_schema, indent=2)
            sections.append(
                f'OUTPUT SCHEMA: When the task is complete, use the "done" tool and include '
                f'a "result" object matching this schema:\n{schema_str}'
            )

        sections.append(
            'When the task is fully complete, use the "done" tool. '
            f"You have at most {config.max_steps} actions."
        )

        return "\n\n".join(sections)


def _tool_to_action_type(tool_name: str) -> ActionType:
    """Map a tool name to its corresponding ActionType."""
    mapping = {
        "navigate": ActionType.NAVIGATE,
        "click": ActionType.CLICK,
        "type_text": ActionType.TYPE,
        "scroll": ActionType.SCROLL,
        "wait": ActionType.WAIT,
        "inject_credentials": ActionType.INJECT_CREDENTIALS,
        "solve_captcha": ActionType.SOLVE_CAPTCHA,
        "done": ActionType.EXTRACT,
    }
    return mapping.get(tool_name, ActionType.UNKNOWN)
