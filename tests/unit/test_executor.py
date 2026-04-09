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

from workers.credential_injector import AuthFormUnrecognizedError
from workers.executor import TaskExecutor
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
        from workers.credential_injector import CredentialInjector

        injector = CredentialInjector()
        page = mock_browser_manager._mock_page

        await injector.inject(
            page,
            task_config.credentials,
            selectors={"username_selector": "#user", "password_selector": "#pass"},
        )

        page.fill.assert_any_call("#user", "alice")
        page.fill.assert_any_call("#pass", "s3cr3t_p@ss!")

    async def test_heuristic_fallback(self, task_config, mock_browser_manager, mock_llm_done):
        from workers.credential_injector import CredentialInjector

        injector = CredentialInjector()
        page = mock_browser_manager._mock_page
        page.query_selector = AsyncMock(return_value=MagicMock())

        await injector.inject(page, task_config.credentials)

        assert page.fill.call_count == 2

    async def test_no_credentials_raises(self, mock_browser_manager, mock_llm_done):
        from workers.credential_injector import CredentialInjector

        injector = CredentialInjector()

        with pytest.raises(AuthFormUnrecognizedError):
            await injector.inject(mock_browser_manager._mock_page, {})


# ---------------------------------------------------------------------------
# Test: execute() with mocked browser and LLM
# ---------------------------------------------------------------------------

class TestExecute:
    """Tests for execute() which now delegates to run_pav_loop."""

    async def test_execute_done_immediately(self, task_config, mock_browser_manager):
        from unittest.mock import patch

        expected = TaskResult(
            task_id="pav-123",
            status="completed",
            success=True,
            result={"title": "Example Domain"},
            steps=3,
            duration_ms=500,
            cost_cents=0.12,
        )

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected) as mock_pav:
            executor = TaskExecutor(
                config=task_config,
                browser_manager=mock_browser_manager,
                llm_client=MagicMock(),
            )
            result = await executor.execute()

        assert isinstance(result, TaskResult)
        assert result.success is True
        assert result.status == "completed"
        assert result.result == {"title": "Example Domain"}
        assert result.steps == 3
        mock_pav.assert_awaited_once()

    async def test_execute_passes_budget(self, task_config, mock_browser_manager):
        from unittest.mock import patch

        expected = TaskResult(task_id="t", status="completed", success=True, steps=1)

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected) as mock_pav:
            executor = TaskExecutor(
                config=task_config,
                browser_manager=mock_browser_manager,
                llm_client=MagicMock(),
            )
            await executor.execute()

        call_kwargs = mock_pav.call_args.kwargs
        assert call_kwargs["budget"].max_steps == task_config.max_steps
        assert call_kwargs["budget"].max_seconds == float(task_config.timeout_seconds)
        assert call_kwargs["task_config"] is task_config

    async def test_execute_passes_repair_fn(self, task_config, mock_browser_manager):
        from unittest.mock import patch

        expected = TaskResult(task_id="t", status="completed", success=True, steps=1)

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected) as mock_pav:
            executor = TaskExecutor(
                config=task_config,
                browser_manager=mock_browser_manager,
                llm_client=MagicMock(),
            )
            await executor.execute()

        call_kwargs = mock_pav.call_args.kwargs
        assert call_kwargs["repair_fn"] is not None
        assert callable(call_kwargs["repair_fn"])

    async def test_execute_records_token_usage(self, task_config, mock_browser_manager):
        from unittest.mock import patch

        expected = TaskResult(
            task_id="t", status="completed", success=True,
            cost_cents=0.5, total_tokens_in=200, total_tokens_out=100,
        )

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected):
            executor = TaskExecutor(
                config=task_config,
                browser_manager=mock_browser_manager,
                llm_client=MagicMock(),
            )
            result = await executor.execute()

        assert result.success is True
        assert result.cost_cents == 0.5
        assert result.total_tokens_in == 200
        assert result.total_tokens_out == 100

    async def test_execute_handles_pav_failure(self, task_config, mock_browser_manager):
        from unittest.mock import patch

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, side_effect=RuntimeError("PAV exploded")):
            executor = TaskExecutor(
                config=task_config,
                browser_manager=mock_browser_manager,
                llm_client=MagicMock(),
            )
            result = await executor.execute()

        assert result.success is False
        assert "PAV exploded" in result.error


# ---------------------------------------------------------------------------
# Test: cost limit enforcement
# ---------------------------------------------------------------------------

class TestCostLimit:
    """Budget is now passed to run_pav_loop via the Budget envelope."""

    async def test_cost_limit_passed_to_budget(self, mock_browser_manager):
        from unittest.mock import patch

        config = TaskConfig(url="https://example.com", task="Do many things", max_steps=2, max_cost_cents=1)
        expected = TaskResult(task_id="t", status="completed", success=True, steps=1)

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected) as mock_pav:
            executor = TaskExecutor(
                config=config,
                browser_manager=mock_browser_manager,
                llm_client=MagicMock(),
            )
            await executor.execute()

        budget = mock_pav.call_args.kwargs["budget"]
        assert budget.max_steps == 2
        assert budget.max_cost_cents == 1.0

    async def test_no_limit_runs_to_completion(self, mock_browser_manager):
        from unittest.mock import patch

        config = TaskConfig(url="https://example.com", task="Do things", max_steps=5)
        expected = TaskResult(task_id="t", status="completed", success=True, steps=2)

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected):
            executor = TaskExecutor(
                config=config,
                browser_manager=mock_browser_manager,
                llm_client=MagicMock(),
            )
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
