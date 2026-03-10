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
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from workers.browser_manager import BrowserManager
from workers.models import ActionType, StepData, TaskConfig, TaskResult

logger = logging.getLogger(__name__)

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


class AuthFormUnrecognizedError(Exception):
    """Raised when the credential injection heuristic cannot find the login form."""


class TaskExecutor:
    """Screenshot-based LLM agent loop using the Anthropic tool-use API."""

    def __init__(
        self,
        config: TaskConfig,
        browser_manager: BrowserManager,
        llm_client: Any,
        use_cloud: bool = False,
    ) -> None:
        self.config = config
        self.browser_manager = browser_manager
        self.llm_client = llm_client
        self.use_cloud = use_cloud

    async def execute(self) -> TaskResult:
        """Execute the task end-to-end.

        1. Generate task_id, record start_time.
        2. Acquire browser, create page (1280x720), apply stealth.
        3. Navigate to config.url, capture step 1.
        4. Build system prompt (NO credentials).
        5. Tool-use agent loop until done/max_steps/cost_limit.
        6. Cleanup browser in finally block.
        7. Return TaskResult.
        """
        task_id = str(uuid.uuid4())
        start_time = time.monotonic()
        steps: List[StepData] = []
        cumulative_cost_cents = 0.0
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
            steps.append(StepData(
                step_number=1,
                timestamp=datetime.now(timezone.utc),
                action_type=ActionType.NAVIGATE,
                description=f"Navigated to {self.config.url}",
                screenshot_bytes=screenshot_bytes,
                duration_ms=int((time.monotonic() - step_start) * 1000),
                success=True,
            ))

            # -- Step 4: System prompt --
            system_prompt = self._build_system_prompt(self.config)

            # -- Step 5: Tool-use agent loop --
            messages: List[Dict[str, Any]] = []

            for step_num in range(2, self.config.max_steps + 1):
                step_start = time.monotonic()

                # a) Capture screenshot
                screenshot_bytes = await page.screenshot(type="jpeg", quality=85)
                screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode("ascii")

                # Build context from last 3 steps
                recent = "\n".join(
                    f"Step {s.step_number}: [{s.action_type}] {s.description}"
                    for s in steps[-3:]
                )

                # b) Build user message with screenshot
                user_content: List[Dict[str, Any]] = [
                    {"type": "text", "text": f"Task: {self.config.task}\n\nRecent actions:\n{recent}\n\nWhat should I do next?"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": screenshot_b64}},
                ]

                # Keep conversation manageable: only last 5 exchanges
                if len(messages) > 10:
                    messages = messages[-10:]

                messages.append({"role": "user", "content": user_content})

                # c) Call Anthropic API with tools
                try:
                    response = self.llm_client.messages.create(
                        model="claude-sonnet-4-5-20250514",
                        max_tokens=1024,
                        system=system_prompt,
                        tools=_TOOLS,
                        messages=messages,
                    )
                except Exception as exc:
                    logger.error("LLM API call failed at step %d: %s", step_num, exc)
                    steps.append(StepData(
                        step_number=step_num,
                        timestamp=datetime.now(timezone.utc),
                        action_type=ActionType.UNKNOWN,
                        description=f"LLM API call failed: {exc}",
                        screenshot_bytes=screenshot_bytes,
                        duration_ms=int((time.monotonic() - step_start) * 1000),
                        success=False,
                        error=str(exc),
                    ))
                    break

                # Track tokens
                tokens_in = getattr(response.usage, "input_tokens", 0)
                tokens_out = getattr(response.usage, "output_tokens", 0)
                step_cost = (tokens_in / 1_000_000 * _COST_PER_M_INPUT + tokens_out / 1_000_000 * _COST_PER_M_OUTPUT) * 100
                cumulative_cost_cents += step_cost

                # d) Parse response — find tool_use block
                tool_use_block = None
                text_response = ""
                assistant_content = response.content
                for block in assistant_content:
                    if getattr(block, "type", None) == "tool_use":
                        tool_use_block = block
                    elif getattr(block, "type", None) == "text":
                        text_response = getattr(block, "text", "")

                # Append assistant message to conversation
                messages.append({"role": "assistant", "content": assistant_content})

                if tool_use_block is None:
                    # No tool call — model is thinking. Record and continue.
                    steps.append(StepData(
                        step_number=step_num,
                        timestamp=datetime.now(timezone.utc),
                        action_type=ActionType.UNKNOWN,
                        description=text_response[:500] or "Model response without tool call",
                        screenshot_bytes=screenshot_bytes,
                        llm_response=text_response,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        duration_ms=int((time.monotonic() - step_start) * 1000),
                        success=True,
                    ))
                    # Append a tool_result for the next turn
                    messages.append({"role": "user", "content": [{"type": "text", "text": "Please use a tool to take the next action."}]})
                    continue

                tool_name = tool_use_block.name
                tool_input = tool_use_block.input
                tool_use_id = tool_use_block.id

                # Map tool name to ActionType
                action_type = _tool_to_action_type(tool_name)

                # e) Check if agent signals completion
                if tool_name == "done":
                    step_duration = int((time.monotonic() - step_start) * 1000)
                    steps.append(StepData(
                        step_number=step_num,
                        timestamp=datetime.now(timezone.utc),
                        action_type=ActionType.EXTRACT,
                        description=tool_input.get("message", "Task completed")[:500],
                        screenshot_bytes=screenshot_bytes,
                        llm_response=text_response,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        duration_ms=step_duration,
                        success=True,
                    ))
                    return TaskResult(
                        task_id=task_id,
                        status="completed",
                        success=True,
                        result=tool_input.get("result"),
                        steps=len(steps),
                        duration_ms=int((time.monotonic() - start_time) * 1000),
                        cost_cents=cumulative_cost_cents,
                        step_data=steps,
                    )

                # f) Execute the tool action
                action_error: Optional[str] = None
                action_success = True
                description = ""

                try:
                    description = await self._execute_tool(page, tool_name, tool_input)
                except Exception as exc:
                    action_error = str(exc)
                    action_success = False
                    description = f"{tool_name} failed: {exc}"
                    logger.warning("Action failed at step %d: %s", step_num, exc)

                # g) Wait for network idle (max 5s)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                step_duration = int((time.monotonic() - step_start) * 1000)

                # h) Record StepData
                steps.append(StepData(
                    step_number=step_num,
                    timestamp=datetime.now(timezone.utc),
                    action_type=action_type,
                    description=description[:500],
                    screenshot_bytes=screenshot_bytes,
                    llm_response=text_response,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    duration_ms=step_duration,
                    success=action_success,
                    error=action_error,
                ))

                # Append tool_result to conversation
                tool_result_content = description if action_success else f"Error: {action_error}"
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": tool_result_content}],
                })

                # i) Check cost limit
                if self.config.max_cost_cents and cumulative_cost_cents > self.config.max_cost_cents:
                    logger.warning(
                        "Cost limit exceeded: %.2f cents > %d cents",
                        cumulative_cost_cents, self.config.max_cost_cents,
                    )
                    return TaskResult(
                        task_id=task_id,
                        status="failed",
                        success=False,
                        error="COST_LIMIT_EXCEEDED",
                        steps=len(steps),
                        duration_ms=int((time.monotonic() - start_time) * 1000),
                        cost_cents=cumulative_cost_cents,
                        step_data=steps,
                    )

            # Max steps reached
            return TaskResult(
                task_id=task_id,
                status="completed",
                success=True,
                steps=len(steps),
                duration_ms=int((time.monotonic() - start_time) * 1000),
                cost_cents=cumulative_cost_cents,
                step_data=steps,
            )

        except Exception as exc:
            logger.exception("Task %s failed: %s", task_id, exc)
            return TaskResult(
                task_id=task_id,
                status="failed",
                success=False,
                error=str(exc),
                steps=len(steps),
                duration_ms=int((time.monotonic() - start_time) * 1000),
                cost_cents=cumulative_cost_cents,
                step_data=steps,
            )

        finally:
            if browser is not None:
                try:
                    await self.browser_manager.release_browser(browser)
                except Exception as exc:
                    logger.warning("Error releasing browser: %s", exc)

    async def _execute_tool(self, page: Any, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Dispatch a tool call to the corresponding Playwright action. Returns description."""
        if tool_name == "navigate":
            url = tool_input["url"]
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            return f"Navigated to {url}"

        elif tool_name == "click":
            selector = tool_input["selector"]
            await page.click(selector, timeout=5000)
            return f"Clicked {selector}"

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
            await self._inject_credentials(
                page,
                tool_input.get("username_selector"),
                tool_input.get("password_selector"),
            )
            return "Credentials injected"

        return f"Unknown tool: {tool_name}"

    async def _inject_credentials(
        self,
        page: Any,
        username_selector: Optional[str] = None,
        password_selector: Optional[str] = None,
    ) -> None:
        """Inject credentials into login form fields via page.fill().

        Uses provided CSS selectors or falls back to heuristic detection.
        Credentials are NEVER sent to the LLM.
        """
        if not self.config.credentials:
            raise AuthFormUnrecognizedError("No credentials configured")

        username = self.config.credentials.get("username", "")
        password = self.config.credentials.get("password", "")

        if not username_selector or not password_selector:
            password_selector = await self._find_selector(page, 'input[type="password"]')
            if not password_selector:
                raise AuthFormUnrecognizedError("Could not find password input on page")

            username_selector = await self._find_selector(
                page,
                'input[type="email"], input[type="text"][name*="user"], '
                'input[type="text"][name*="email"], input[type="text"][name*="login"], '
                'input[type="text"]',
            )
            if not username_selector:
                raise AuthFormUnrecognizedError("Could not find username/email input on page")

        await page.fill(username_selector, username)
        await page.fill(password_selector, password)

    async def _find_selector(self, page: Any, selector: str) -> Optional[str]:
        """Return the selector if a matching element exists, else None."""
        try:
            element = await page.query_selector(selector)
            return selector if element else None
        except Exception:
            return None

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
        "done": ActionType.EXTRACT,
    }
    return mapping.get(tool_name, ActionType.UNKNOWN)
