"""
tests/unit/test_executor.py — Unit tests for the task execution engine.

Tests:
- System prompt construction (credentials not leaked)
- Credential injection with mock page
- Full execute() with mocked browser and LLM (tool-use API)
- Cost limit enforcement
- Replay generation
"""

from __future__ import annotations

import base64
import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.executor import TaskExecutor, AuthFormUnrecognizedError
from workers.models import ActionType, StepData, TaskConfig, TaskResult
from workers.replay import ReplayGenerator


# ---------------------------------------------------------------------------
# Helpers: mock Anthropic tool-use responses
# ---------------------------------------------------------------------------

def _make_tool_use_block(tool_name: str, tool_input: dict, tool_id: str = "toolu_test") -> MagicMock:
    """Create a mock tool_use content block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = tool_id
    return block


def _make_text_block(text: str = "") -> MagicMock:
    """Create a mock text content block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_llm_response(
    tool_name: str = "done",
    tool_input: dict | None = None,
    tokens_in: int = 100,
    tokens_out: int = 50,
    text: str = "",
    tool_id: str = "toolu_test",
):
    """Create a mock Anthropic messages.create() response with a tool_use block."""
    if tool_input is None:
        tool_input = {}

    content = []
    if text:
        content.append(_make_text_block(text))
    content.append(_make_tool_use_block(tool_name, tool_input, tool_id))

    response = MagicMock()
    response.content = content
    response.usage = SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out)
    return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def task_config():
    return TaskConfig(
        url="https://example.com",
        task="Click the login button",
        credentials={"username": "alice", "password": "s3cr3t_p@ss!"},
        output_schema={"title": "str"},
        max_steps=10,
        timeout_seconds=60,
    )


@pytest.fixture
def task_config_no_creds():
    return TaskConfig(url="https://example.com", task="Extract the page title", max_steps=5)


@pytest.fixture
def mock_browser_manager():
    manager = AsyncMock()

    mock_page = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\xff\xd8\xff\xe0fake-jpeg")
    mock_page.goto = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=None)
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.query_selector = AsyncMock(return_value=MagicMock())
    mock_page.close = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.pages = [mock_page]

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.contexts = [mock_context]
    mock_browser.close = AsyncMock()

    manager.get_browser = AsyncMock(return_value=mock_browser)
    manager.release_browser = AsyncMock()
    manager.apply_stealth = AsyncMock()
    manager._mock_page = mock_page

    return manager


@pytest.fixture
def mock_llm_done():
    """LLM client that returns done on first tool call."""
    client = MagicMock()
    client.messages.create = MagicMock(
        return_value=_make_llm_response(
            tool_name="done",
            tool_input={"result": {"title": "Example Domain"}, "message": "Extracted the title"},
        )
    )
    return client


# ---------------------------------------------------------------------------
# Test: _build_system_prompt
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    def test_credentials_not_in_prompt(self, task_config, mock_browser_manager, mock_llm_done):
        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm_done)
        prompt = executor._build_system_prompt(task_config)

        assert "alice" not in prompt
        assert "s3cr3t_p@ss!" not in prompt
        for v in task_config.credentials.values():
            assert v not in prompt

    def test_prompt_contains_domain_safety(self, task_config, mock_browser_manager, mock_llm_done):
        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm_done)
        prompt = executor._build_system_prompt(task_config)

        assert "example.com" in prompt
        assert "inject_credentials" in prompt

    def test_prompt_includes_output_schema(self, task_config, mock_browser_manager, mock_llm_done):
        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm_done)
        prompt = executor._build_system_prompt(task_config)

        assert "title" in prompt
        assert "str" in prompt
        assert "done" in prompt

    def test_prompt_no_schema_when_none(self, task_config_no_creds, mock_browser_manager, mock_llm_done):
        executor = TaskExecutor(config=task_config_no_creds, browser_manager=mock_browser_manager, llm_client=mock_llm_done)
        prompt = executor._build_system_prompt(task_config_no_creds)

        assert "OUTPUT SCHEMA" not in prompt


# ---------------------------------------------------------------------------
# Test: _inject_credentials
# ---------------------------------------------------------------------------

class TestInjectCredentials:
    async def test_fill_called_with_selectors(self, task_config, mock_browser_manager, mock_llm_done):
        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm_done)
        page = mock_browser_manager._mock_page

        await executor._inject_credentials(page, "#user", "#pass")

        page.fill.assert_any_call("#user", "alice")
        page.fill.assert_any_call("#pass", "s3cr3t_p@ss!")

    async def test_heuristic_fallback(self, task_config, mock_browser_manager, mock_llm_done):
        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm_done)
        page = mock_browser_manager._mock_page
        page.query_selector = AsyncMock(return_value=MagicMock())

        await executor._inject_credentials(page, None, None)

        assert page.fill.call_count == 2

    async def test_no_credentials_raises(self, mock_browser_manager, mock_llm_done):
        config = TaskConfig(url="https://example.com", task="test")
        executor = TaskExecutor(config=config, browser_manager=mock_browser_manager, llm_client=mock_llm_done)

        with pytest.raises(AuthFormUnrecognizedError):
            await executor._inject_credentials(mock_browser_manager._mock_page, "#u", "#p")


# ---------------------------------------------------------------------------
# Test: execute() with mocked browser and LLM
# ---------------------------------------------------------------------------

class TestExecute:
    async def test_execute_done_immediately(self, task_config, mock_browser_manager, mock_llm_done):
        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm_done)
        result = await executor.execute()

        assert isinstance(result, TaskResult)
        assert result.success is True
        assert result.status == "completed"
        assert result.steps >= 2  # navigation + done
        assert result.duration_ms >= 0
        assert result.result == {"title": "Example Domain"}
        mock_browser_manager.release_browser.assert_called_once()

    async def test_execute_captures_steps(self, task_config, mock_browser_manager):
        responses = [
            _make_llm_response("click", {"selector": "#login-btn"}, tool_id="t1"),
            _make_llm_response("done", {"result": {"title": "Dashboard"}, "message": "Done"}, tool_id="t2"),
        ]
        mock_llm = MagicMock()
        mock_llm.messages.create = MagicMock(side_effect=responses)

        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm)
        result = await executor.execute()

        assert result.success is True
        assert result.steps == 3  # navigate + click + done
        assert len(result.step_data) == 3
        assert result.step_data[0].action_type == ActionType.NAVIGATE
        assert result.step_data[1].action_type == ActionType.CLICK
        assert result.step_data[2].action_type == ActionType.EXTRACT

    async def test_execute_records_token_usage(self, task_config, mock_browser_manager):
        mock_llm = MagicMock()
        mock_llm.messages.create = MagicMock(
            return_value=_make_llm_response("done", {"result": {"title": "T"}}, tokens_in=500, tokens_out=200)
        )
        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm)
        result = await executor.execute()

        done_step = result.step_data[-1]
        assert done_step.tokens_in == 500
        assert done_step.tokens_out == 200

    async def test_browser_released_on_error(self, task_config, mock_browser_manager, mock_llm_done):
        mock_browser_manager._mock_page.goto = AsyncMock(side_effect=Exception("Navigation timeout"))

        executor = TaskExecutor(config=task_config, browser_manager=mock_browser_manager, llm_client=mock_llm_done)
        result = await executor.execute()

        assert result.success is False
        assert "Navigation timeout" in result.error
        mock_browser_manager.release_browser.assert_called_once()


# ---------------------------------------------------------------------------
# Test: cost limit enforcement
# ---------------------------------------------------------------------------

class TestCostLimit:
    async def test_cost_limit_terminates_early(self, mock_browser_manager):
        config = TaskConfig(url="https://example.com", task="Do many things", max_steps=10, max_cost_cents=1)

        # Each step: 10k input + 5k output ≈ 10.5 cents → exceeds 1 cent on first step
        responses = [
            _make_llm_response("click", {"selector": "#btn"}, tokens_in=10000, tokens_out=5000, tool_id=f"t{i}")
            for i in range(10)
        ]
        mock_llm = MagicMock()
        mock_llm.messages.create = MagicMock(side_effect=responses)

        executor = TaskExecutor(config=config, browser_manager=mock_browser_manager, llm_client=mock_llm)
        result = await executor.execute()

        assert result.success is False
        assert result.error == "COST_LIMIT_EXCEEDED"
        assert result.steps < 10
        assert result.cost_cents > 1

    async def test_no_limit_runs_to_completion(self, mock_browser_manager):
        config = TaskConfig(url="https://example.com", task="Do things", max_steps=5)
        responses = [
            _make_llm_response("click", {"selector": "#a"}, tokens_in=100, tokens_out=50, tool_id="t1"),
            _make_llm_response("done", {"result": {"x": 1}}, tool_id="t2"),
        ]
        mock_llm = MagicMock()
        mock_llm.messages.create = MagicMock(side_effect=responses)

        executor = TaskExecutor(config=config, browser_manager=mock_browser_manager, llm_client=mock_llm)
        result = await executor.execute()

        assert result.success is True
        assert result.error is None


# ---------------------------------------------------------------------------
# Test: replay generation
# ---------------------------------------------------------------------------

class TestReplayGeneration:
    def test_replay_valid_html_with_screenshots(self):
        steps = [
            StepData(
                step_number=1, timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
                action_type=ActionType.NAVIGATE, description="Navigated to https://example.com",
                screenshot_bytes=b"\xff\xd8\xff\xe0fake-jpeg", duration_ms=500, success=True,
            ),
            StepData(
                step_number=2, timestamp=datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
                action_type=ActionType.CLICK, description="Clicked submit",
                screenshot_bytes=b"\xff\xd8\xff\xe0more-jpeg", tokens_in=100, tokens_out=50,
                duration_ms=200, success=True,
            ),
        ]
        metadata = {"task_id": "test-replay-123", "url": "https://example.com", "task": "Test", "duration_ms": 700, "success": True}
        generator = ReplayGenerator(steps=steps, task_metadata=metadata)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "replay.html")
            result = generator.generate(path)

            assert result == path
            assert os.path.exists(path)
            html = open(path, encoding="utf-8").read()

            # Structure
            assert "<!DOCTYPE html>" in html
            assert "</html>" in html
            assert "Task Replay" in html

            # Screenshot data inlined
            expected_b64 = base64.standard_b64encode(b"\xff\xd8\xff\xe0fake-jpeg").decode()
            assert expected_b64 in html

            # Metadata present
            assert "test-replay-123" in html
            assert "navigate" in html
            assert "click" in html

            # No external dependencies
            assert 'src="http' not in html
            assert 'href="http' not in html

            # CSS inlined
            assert "box-sizing" in html

    def test_replay_no_screenshots(self):
        steps = [StepData(step_number=1, timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
                          action_type=ActionType.NAVIGATE, description="Nav", duration_ms=100, success=True)]
        gen = ReplayGenerator(steps=steps, task_metadata={"task_id": "no-ss"})

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate(os.path.join(tmpdir, "replay.html"))
            assert os.path.exists(path)
            assert "no-ss" in open(path, encoding="utf-8").read()

    def test_replay_creates_parent_dirs(self):
        steps = [StepData(step_number=1, timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
                          action_type=ActionType.NAVIGATE, description="T", duration_ms=50, success=True)]
        gen = ReplayGenerator(steps=steps, task_metadata={"task_id": "dir-test"})

        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "a", "b", "c", "replay.html")
            assert os.path.exists(gen.generate(nested))


# ---------------------------------------------------------------------------
# Test: models
# ---------------------------------------------------------------------------

class TestModels:
    def test_step_data_truncates_description(self):
        step = StepData(step_number=1, timestamp=datetime.now(timezone.utc),
                        action_type=ActionType.CLICK, description="x" * 1000, success=True)
        assert len(step.description) == 500

    def test_action_type_values(self):
        assert ActionType.NAVIGATE == "navigate"
        assert ActionType.CLICK == "click"
        assert ActionType.TYPE == "type"
        assert ActionType.SCROLL == "scroll"
        assert ActionType.EXTRACT == "extract"
        assert ActionType.WAIT == "wait"
        assert ActionType.INJECT_CREDENTIALS == "inject_credentials"
        assert ActionType.UNKNOWN == "unknown"

    def test_task_config_defaults(self):
        c = TaskConfig(url="https://example.com", task="test")
        assert c.max_steps == 50
        assert c.timeout_seconds == 300
        assert c.max_cost_cents is None
        assert c.credentials is None

    def test_task_result_defaults(self):
        r = TaskResult(task_id="t", status="completed", success=True)
        assert r.steps == 0
        assert r.cost_cents == 0.0
        assert r.step_data == []
