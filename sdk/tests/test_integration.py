"""Integration tests — cross-module pipeline verification.

Tests the complete explore-to-replay pipeline: run JSON → compile → save →
script → replay, plus post-action verification, budget circuit breakers,
selector healing, enrichment degradation, and parameter substitution.

Note: async tests rely on ``asyncio_mode = "auto"`` in pyproject.toml.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

import pytest

from computeruse.action_verifier import ActionVerifier
from computeruse.budget import BudgetExceededError, BudgetMonitor
from computeruse.compiler import WorkflowCompiler
from computeruse.cost import calculate_cost_cents
from computeruse.models import ActionType, CompiledStep, CompiledWorkflow
from computeruse.replay_executor import ReplayConfig, ReplayExecutor


# ---------------------------------------------------------------------------
# Mock pages
# ---------------------------------------------------------------------------


class MockPage:
    """Mock Playwright page that succeeds on all actions."""

    def __init__(self, url: str = "https://example.com") -> None:
        self.url = url
        self._click_called = 0
        self._fill_args: list[tuple[str, str]] = []

    async def goto(self, url: str) -> None:
        self.url = url

    async def click(self, selector: str, **kwargs: object) -> None:
        self._click_called += 1

    async def fill(self, selector: str, value: str) -> None:
        self._fill_args.append((selector, value))

    async def select_option(self, selector: str, value: str) -> None:
        pass

    async def press(self, selector: str, key: str) -> None:
        pass

    async def dblclick(self, selector: str) -> None:
        pass

    async def hover(self, selector: str) -> None:
        pass

    async def evaluate(self, expr: str) -> Any:
        return {}

    async def eval_on_selector(self, selector: str, expr: str) -> Any:
        return None

    async def text_content(self, selector: str) -> str:
        return ""

    async def wait_for_timeout(self, ms: int) -> None:
        pass

    async def wait_for_selector(self, sel: str, **kw: object) -> bool:
        return True

    async def content(self) -> str:
        return "<html></html>"

    async def screenshot(self, **kw: object) -> bytes:
        return b"fake-screenshot"


class HealingPage(MockPage):
    """Page where primary selector fails but alternate succeeds."""

    def __init__(self) -> None:
        super().__init__()
        self._fail_selector = "#broken"

    async def click(self, selector: str, **kwargs: object) -> None:
        if selector == self._fail_selector:
            raise Exception(f"Element not found: {selector}")
        self._click_called += 1

    async def wait_for_selector(self, sel: str, **kw: object) -> bool:
        if sel == self._fail_selector:
            raise Exception("timeout")
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_enriched_step(
    action_type: str = "click",
    description: str = "Click the submit button",
    selectors: list | None = None,
    intent: str = "Click Submit button",
    fill_value_template: str = "",
    pre_url: str = "https://example.com",
    expected_url_pattern: str = "",
) -> dict:
    """Build a realistic enriched step dict (matches wrap.py _serialize_step)."""
    step: dict = {
        "action_type": action_type,
        "description": description,
        "duration_ms": 150,
        "success": True,
        "tokens_in": 100,
        "tokens_out": 50,
        "screenshot_path": "",
    }
    if selectors is not None:
        step["selectors"] = selectors
    else:
        step["selectors"] = [
            {"type": "css", "value": "#submit-btn", "confidence": 0.9},
        ]
    if intent:
        step["intent"] = intent
    if fill_value_template:
        step["fill_value_template"] = fill_value_template
    if pre_url:
        step["pre_url"] = pre_url
    if expected_url_pattern:
        step["expected_url_pattern"] = expected_url_pattern
    return step


def _make_run_json(
    steps: list,
    task_id: str = "integration-test-001",
    status: str = "completed",
) -> dict:
    """Build run metadata dict matching wrap.py _save_run_metadata format."""
    return {
        "task_id": task_id,
        "status": status,
        "step_count": len(steps),
        "cost_cents": 0.5,
        "error_category": None,
        "error": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:01:00+00:00",
        "duration_ms": 60000,
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Test 1: Full pipeline — run JSON → compile → save → script → replay
# ---------------------------------------------------------------------------


async def test_full_pipeline_wrap_compile_replay(tmp_path: Path) -> None:
    """Simulate: AI explores → compile → save → script → deterministic replay."""
    steps = [
        _make_enriched_step(
            action_type="navigate",
            description="goto(https://example.com)",
            intent="Navigate to example.com",
            pre_url="https://example.com",
            selectors=[],
        ),
        _make_enriched_step(
            action_type="click",
            description="click(#login)",
            intent="Click the login button",
            selectors=[
                {"type": "css", "value": "#login", "confidence": 0.95},
                {"type": "text", "value": "Log In", "confidence": 0.7},
            ],
        ),
        _make_enriched_step(
            action_type="type",
            description="fill(#email, [REDACTED])",
            intent="Enter email address",
            fill_value_template="{{email}}",
            selectors=[
                {"type": "css", "value": "#email", "confidence": 0.9},
            ],
        ),
    ]

    run_data = _make_run_json(steps=steps)
    run_path = tmp_path / "runs" / "integration-test-001.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text(json.dumps(run_data))

    # Compile
    compiler = WorkflowCompiler()
    workflow = compiler.compile_from_run(str(run_path))

    assert len(workflow.steps) == 3
    assert "email" in workflow.parameters
    assert workflow.steps[0].action_type == "goto"
    assert workflow.steps[1].action_type == "click"
    assert workflow.steps[2].action_type == "fill"
    assert len(workflow.steps[1].selectors) == 2

    # Save workflow
    wf_path = compiler.save_workflow(
        workflow, output_dir=str(tmp_path / "workflows")
    )
    assert Path(wf_path).exists()
    loaded = json.loads(Path(wf_path).read_text())
    assert loaded["source_task_id"] == "integration-test-001"

    # Generate Playwright script
    script = compiler.generate_playwright_script(workflow)
    ast.parse(script)  # Must be valid Python
    assert "PARAMS" in script
    assert '"email"' in script

    # Replay with mock page — start at about:blank so goto assertion is meaningful
    config = ReplayConfig(verify_actions=False, max_retries_per_step=1)
    executor = ReplayExecutor(config=config)
    page = MockPage(url="about:blank")

    with patch("computeruse.replay_executor.asyncio.sleep"):
        result = await executor.execute(
            workflow, params={"email": "test@test.com"}, page=page,
        )

    assert result.success is True
    assert result.steps_executed == 3
    assert result.steps_deterministic == 3
    assert result.cost_cents == 0.0
    assert result.error is None
    # Proves goto() actually fired (page started at about:blank)
    assert page.url == "https://example.com"


# ---------------------------------------------------------------------------
# Test 2: Post-action verification catches wrong URL
# ---------------------------------------------------------------------------


async def test_post_action_verification_catches_wrong_url() -> None:
    """ActionVerifier detects URL mismatch → critical failure."""
    verifier = ActionVerifier()
    page = MockPage(url="https://example.com/error")

    result = await verifier.verify_action(
        page,
        "navigate",
        expected_url_pattern=r"https://example\.com/dashboard.*",
    )

    assert not result.passed
    assert result.has_critical_failure
    assert result.checks_run >= 1
    assert result.checks_passed == 0
    assert len(result.failures) == 1
    assert result.failures[0]["check"] == "url_pattern"
    assert result.failures[0]["actual"] == "https://example.com/error"
    assert result.failures[0]["expected"] == r"https://example\.com/dashboard.*"


# ---------------------------------------------------------------------------
# Test 3: Budget circuit breaker stops the agent via wrap() on_step_end
# ---------------------------------------------------------------------------


async def test_budget_circuit_breaker_stops_agent(tmp_path: Path) -> None:
    """BudgetMonitor triggers agent.stop() when cost exceeds limit."""

    # Verify the per-step cost to compute expected trip point
    step_cost = calculate_cost_cents(100_000, 50_000)
    assert step_cost > 0, "sanity: step cost must be positive"

    class BudgetTestAgent:
        """Agent whose run() calls on_step_end with high-token steps.

        Like a real browser_use Agent, stops iterating when stop() is called.
        """

        def __init__(self) -> None:
            self._stopped = False
            self.history: list = []
            self.task = "test budget"
            self.calculate_cost = True

        async def run(
            self,
            max_steps: int = 100,
            on_step_end: Any = None,
            **kw: Any,
        ) -> Any:
            for _ in range(20):
                if self._stopped:
                    break
                step = SimpleNamespace(
                    metadata=SimpleNamespace(
                        input_tokens=100_000,
                        output_tokens=50_000,
                        step_duration=0.1,
                    ),
                    result=[SimpleNamespace(error=None)],
                    model_output=SimpleNamespace(
                        action=[SimpleNamespace()],
                        next_goal="budget step",
                        evaluation_previous_goal=None,
                    ),
                    state=SimpleNamespace(screenshot=None),
                )
                self.history.append(step)
                if on_step_end:
                    await on_step_end(self)
            return SimpleNamespace(
                history=self.history,
                action_names=lambda: [],
                screenshots=lambda: [],
                total_cost=lambda: None,
            )

        def stop(self) -> None:
            self._stopped = True

    from computeruse.wrap import WrapConfig, wrap

    agent = BudgetTestAgent()
    # max_cost_cents=1.0 → should trip after a few steps at 100k/50k tokens
    config = WrapConfig(
        max_cost_cents=1.0,
        max_retries=0,
        output_dir=str(tmp_path / ".pokant"),
        generate_replay=False,
        save_screenshots=False,
        enable_stuck_detection=False,
    )
    wrapped = wrap(agent, config=config)
    await wrapped.run()

    assert agent._stopped is True
    # Agent stopped early — not all 20 steps ran
    max_steps_before_trip = int(1.0 / step_cost) + 2  # +2 for rounding margin
    assert len(agent.history) <= max_steps_before_trip


# ---------------------------------------------------------------------------
# Test 4: Selector healing falls back to alternate in replay
# ---------------------------------------------------------------------------


async def test_selector_healing_falls_back_to_alternate() -> None:
    """Primary selector fails, SelectorHealer cascades to alternate."""
    steps = [
        CompiledStep(
            action_type="click",
            selectors=[
                {"type": "css", "value": "#broken", "confidence": 0.9},
                {"type": "text", "value": "Submit", "confidence": 0.7},
            ],
            intent="Click Submit",
            timeout_ms=0,
        ),
    ]
    workflow = CompiledWorkflow(
        name="healing-test",
        steps=steps,
        parameters={},
        source_task_id="heal-001",
        compiled_at="2026-01-01T00:00:00+00:00",
    )

    config = ReplayConfig(
        verify_actions=False,
        max_retries_per_step=0,
    )
    executor = ReplayExecutor(config=config)
    page = HealingPage()

    result = await executor.execute(workflow, page=page)

    assert result.success is True
    assert result.steps_healed == 1
    assert result.steps_deterministic == 0
    assert page._click_called == 1


# ---------------------------------------------------------------------------
# Test 5: Enrichment graceful degradation in track()
# ---------------------------------------------------------------------------


async def test_enrichment_graceful_degradation_in_track(
    tmp_path: Path,
) -> None:
    """track() works even when step_enrichment functions raise."""
    from computeruse.track import TrackConfig, track

    page = MockPage()
    config = TrackConfig(
        output_dir=str(tmp_path / ".pokant"),
        capture_screenshots=False,
    )

    # Patch enrichment functions to simulate unavailability
    with patch(
        "computeruse.step_enrichment.extract_selectors",
        side_effect=ImportError("simulated"),
    ), patch(
        "computeruse.step_enrichment.extract_element_metadata",
        side_effect=ImportError("simulated"),
    ), patch(
        "computeruse.step_enrichment.infer_intent_from_step",
        side_effect=ImportError("simulated"),
    ), patch(
        "computeruse.step_enrichment.infer_expected_outcomes",
        side_effect=ImportError("simulated"),
    ), patch(
        "computeruse.step_enrichment.detect_parameterizable_values",
        side_effect=ImportError("simulated"),
    ):
        async with track(page, config=config) as t:
            await t.goto("https://example.com")
            await t.click("#btn")
            await t.fill("#email", "test@test.com")

    assert len(t.steps) == 3
    assert t.steps[0].action_type == ActionType.NAVIGATE
    assert t.steps[1].action_type == ActionType.CLICK
    assert t.steps[2].action_type == ActionType.TYPE
    # All succeeded despite enrichment failures
    assert all(s.success for s in t.steps)
    # Run metadata was saved
    runs_dir = tmp_path / ".pokant" / "runs"
    run_files = list(runs_dir.glob("*.json"))
    assert len(run_files) == 1


# ---------------------------------------------------------------------------
# Test 6: Parameter substitution through the full pipeline
# ---------------------------------------------------------------------------


async def test_parameter_substitution_end_to_end(tmp_path: Path) -> None:
    """Compile a run with {{email}} template → replay with params →
    verify mock page received the substituted value, not the template."""
    steps = [
        _make_enriched_step(
            action_type="navigate",
            description="goto(https://app.example.com/login)",
            intent="Navigate to login page",
            pre_url="https://app.example.com/login",
            selectors=[],
        ),
        _make_enriched_step(
            action_type="type",
            description="fill(#email, [REDACTED])",
            intent="Enter email address",
            fill_value_template="{{email}}",
            selectors=[
                {"type": "css", "value": "#email", "confidence": 0.95},
            ],
        ),
        _make_enriched_step(
            action_type="type",
            description="fill(#password, [REDACTED])",
            intent="Enter password",
            fill_value_template="{{password}}",
            selectors=[
                {"type": "css", "value": "#password", "confidence": 0.95},
            ],
        ),
        _make_enriched_step(
            action_type="click",
            description="click(#submit)",
            intent="Click login button",
            selectors=[
                {"type": "css", "value": "#submit", "confidence": 0.9},
            ],
        ),
    ]

    run_data = _make_run_json(steps=steps, task_id="param-test-001")
    run_path = tmp_path / "runs" / "param-test-001.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text(json.dumps(run_data))

    # Compile — parameters should be auto-detected
    compiler = WorkflowCompiler()
    workflow = compiler.compile_from_run(str(run_path))

    assert "email" in workflow.parameters
    assert "password" in workflow.parameters

    # Replay with actual parameter values
    config = ReplayConfig(verify_actions=False, max_retries_per_step=1)
    executor = ReplayExecutor(config=config)
    page = MockPage(url="about:blank")

    with patch("computeruse.replay_executor.asyncio.sleep"):
        result = await executor.execute(
            workflow,
            params={"email": "user@company.com", "password": "s3cret!"},
            page=page,
        )

    assert result.success is True
    assert result.steps_executed == 4

    # Core assertion: fill() received substituted values, NOT templates
    assert len(page._fill_args) == 2

    email_selector, email_value = page._fill_args[0]
    assert email_selector == "#email"
    assert email_value == "user@company.com"
    assert "{{" not in email_value

    password_selector, password_value = page._fill_args[1]
    assert password_selector == "#password"
    assert password_value == "s3cret!"
    assert "{{" not in password_value
