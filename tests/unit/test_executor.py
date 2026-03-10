"""
tests/unit/test_executor.py — Unit tests for the task execution engine.

Tests cover:
- System prompt construction (credentials not leaked)
- Credential injection with mock page
- Full execute() with mocked browser and LLM
- Cost limit enforcement
- Replay generation
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.executor import TaskExecutor
from workers.models import ActionType, StepData, TaskConfig, TaskResult
from workers.replay import ReplayGenerator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def task_config():
    """Basic TaskConfig for testing."""
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
    """TaskConfig without credentials."""
    return TaskConfig(
        url="https://example.com",
        task="Extract the page title",
        max_steps=5,
    )


@pytest.fixture
def mock_browser_manager():
    """Mock BrowserManager that returns a mock browser."""
    manager = AsyncMock()

    # Build a mock page
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

    # Build a mock context
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.pages = [mock_page]

    # Build a mock browser
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.contexts = [mock_context]
    mock_browser.close = AsyncMock()

    manager.get_browser = AsyncMock(return_value=mock_browser)
    manager.release_browser = AsyncMock()
    manager.apply_stealth = AsyncMock()

    # Attach mock objects for test inspection
    manager._mock_browser = mock_browser
    manager._mock_context = mock_context
    manager._mock_page = mock_page

    return manager


def _make_llm_response(action_type="done", description="Task completed", result=None,
                        tokens_in=100, tokens_out=50, **extra):
    """Create a mock Anthropic API response."""
    action = {"action_type": action_type, "description": description}
    if result is not None:
        action["result"] = result
    action.update(extra)

    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(action))]
    response.usage = SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out)
    return response


@pytest.fixture
def mock_llm_client_done():
    """LLM client that immediately returns done."""
    client = MagicMock()
    client.messages.create = MagicMock(
        return_value=_make_llm_response(
            action_type="done",
            description="Extracted the title",
            result={"title": "Example Domain"},
        )
    )
    return client


# ---------------------------------------------------------------------------
# Test: _build_system_prompt
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    def test_credentials_not_in_prompt(self, task_config, mock_browser_manager, mock_llm_client_done):
        """Verify credentials are NEVER included in the system prompt."""
        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )
        prompt = executor._build_system_prompt(task_config)

        assert "alice" not in prompt
        assert "s3cr3t_p@ss!" not in prompt
        # Ensure the prompt doesn't contain the credential values
        for value in task_config.credentials.values():
            assert value not in prompt

    def test_prompt_contains_domain_safety(self, task_config, mock_browser_manager, mock_llm_client_done):
        """System prompt should restrict navigation to the target domain."""
        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )
        prompt = executor._build_system_prompt(task_config)

        assert "example.com" in prompt
        assert "inject_credentials" in prompt.lower()

    def test_prompt_includes_output_schema(self, task_config, mock_browser_manager, mock_llm_client_done):
        """When output_schema is provided, it should appear in the prompt."""
        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )
        prompt = executor._build_system_prompt(task_config)

        assert "title" in prompt
        assert "str" in prompt
        assert "done" in prompt

    def test_prompt_no_schema_when_none(self, task_config_no_creds, mock_browser_manager, mock_llm_client_done):
        """When output_schema is None, no schema section in prompt."""
        executor = TaskExecutor(
            config=task_config_no_creds,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )
        prompt = executor._build_system_prompt(task_config_no_creds)

        assert "OUTPUT SCHEMA" not in prompt


# ---------------------------------------------------------------------------
# Test: _inject_credentials
# ---------------------------------------------------------------------------

class TestInjectCredentials:
    async def test_inject_with_selectors(self, task_config, mock_browser_manager, mock_llm_client_done):
        """Verify fill() is called with correct username and password values."""
        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )
        mock_page = mock_browser_manager._mock_page

        await executor._inject_credentials(
            mock_page,
            username_selector="#username",
            password_selector="#password",
        )

        mock_page.fill.assert_any_call("#username", "alice")
        mock_page.fill.assert_any_call("#password", "s3cr3t_p@ss!")

    async def test_inject_heuristic_fallback(self, task_config, mock_browser_manager, mock_llm_client_done):
        """When selectors are not provided, use heuristic to find form fields."""
        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )
        mock_page = mock_browser_manager._mock_page
        # query_selector returns a truthy mock (element found)
        mock_page.query_selector = AsyncMock(return_value=MagicMock())

        await executor._inject_credentials(mock_page, None, None)

        # fill() should have been called twice (username + password)
        assert mock_page.fill.call_count == 2

    async def test_inject_no_credentials_raises(self, mock_browser_manager, mock_llm_client_done):
        """Raise AuthFormUnrecognizedError when no credentials configured."""
        from workers.executor import AuthFormUnrecognizedError

        config = TaskConfig(url="https://example.com", task="test")
        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )
        mock_page = mock_browser_manager._mock_page

        with pytest.raises(AuthFormUnrecognizedError):
            await executor._inject_credentials(mock_page, "#user", "#pass")


# ---------------------------------------------------------------------------
# Test: execute() with mocked browser and LLM
# ---------------------------------------------------------------------------

class TestExecute:
    async def test_execute_simple_done(self, task_config, mock_browser_manager, mock_llm_client_done):
        """Execute completes successfully when LLM returns done on first agent step."""
        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )

        result = await executor.execute()

        assert isinstance(result, TaskResult)
        assert result.success is True
        assert result.status == "completed"
        assert result.steps >= 1  # at least navigation step + done step
        assert result.duration_ms >= 0
        assert result.result == {"title": "Example Domain"}

        # Browser should have been released
        mock_browser_manager.release_browser.assert_called_once()

    async def test_execute_captures_steps(self, task_config, mock_browser_manager):
        """Verify step data is captured for each iteration."""
        # LLM returns click, then done
        responses = [
            _make_llm_response(
                action_type="click",
                description="Clicked login button",
                selector="#login-btn",
            ),
            _make_llm_response(
                action_type="done",
                description="Task completed",
                result={"title": "Dashboard"},
            ),
        ]
        mock_llm = MagicMock()
        mock_llm.messages.create = MagicMock(side_effect=responses)

        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm,
        )

        result = await executor.execute()

        assert result.success is True
        # 1 navigation + 1 click + 1 done = 3 steps
        assert result.steps == 3
        assert len(result.step_data) == 3
        assert result.step_data[0].action_type == ActionType.NAVIGATE
        assert result.step_data[1].action_type == ActionType.CLICK
        assert result.step_data[2].action_type == ActionType.EXTRACT  # done maps to extract

    async def test_execute_records_token_usage(self, task_config, mock_browser_manager):
        """Verify token counts are recorded in step data."""
        mock_llm = MagicMock()
        mock_llm.messages.create = MagicMock(
            return_value=_make_llm_response(
                action_type="done",
                description="Done",
                result={"title": "Test"},
                tokens_in=500,
                tokens_out=200,
            )
        )

        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm,
        )

        result = await executor.execute()

        # The done step should have token counts
        done_step = result.step_data[-1]
        assert done_step.tokens_in == 500
        assert done_step.tokens_out == 200

    async def test_execute_browser_released_on_error(self, task_config, mock_browser_manager, mock_llm_client_done):
        """Browser is released even when navigation fails."""
        mock_page = mock_browser_manager._mock_page
        mock_page.goto = AsyncMock(side_effect=Exception("Navigation timeout"))

        executor = TaskExecutor(
            config=task_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )

        result = await executor.execute()

        assert result.success is False
        assert "Navigation timeout" in result.error
        mock_browser_manager.release_browser.assert_called_once()


# ---------------------------------------------------------------------------
# Test: cost limit enforcement
# ---------------------------------------------------------------------------

class TestCostLimit:
    async def test_cost_limit_terminates_early(self, mock_browser_manager):
        """Set max_cost_cents=1 and mock a multi-step task to verify early termination."""
        config = TaskConfig(
            url="https://example.com",
            task="Do many things",
            max_steps=10,
            max_cost_cents=1,  # Very low limit: 1 cent
        )

        # Each response uses enough tokens to exceed the 1-cent limit:
        # 10000 input tokens * $3/M = $0.03 = 3 cents per step
        responses = [
            _make_llm_response(
                action_type="click",
                description=f"Click step",
                selector="#btn",
                tokens_in=10000,
                tokens_out=5000,
            )
            for _ in range(10)
        ]

        mock_llm = MagicMock()
        mock_llm.messages.create = MagicMock(side_effect=responses)

        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm,
        )

        result = await executor.execute()

        assert result.success is False
        assert result.error == "COST_LIMIT_EXCEEDED"
        # Should have stopped well before 10 steps
        assert result.steps < 10
        assert result.cumulative_cost_cents > 1

    async def test_no_cost_limit_runs_to_completion(self, mock_browser_manager):
        """Without max_cost_cents, the task runs to completion."""
        config = TaskConfig(
            url="https://example.com",
            task="Do things",
            max_steps=3,
        )

        responses = [
            _make_llm_response(action_type="click", description="Click", selector="#a", tokens_in=1000, tokens_out=500),
            _make_llm_response(action_type="done", description="Done", result={"x": 1}),
        ]

        mock_llm = MagicMock()
        mock_llm.messages.create = MagicMock(side_effect=responses)

        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm,
        )

        result = await executor.execute()

        assert result.success is True
        assert result.error is None


# ---------------------------------------------------------------------------
# Test: replay generation
# ---------------------------------------------------------------------------

class TestReplayGeneration:
    def test_replay_generates_valid_html(self):
        """Verify the replay HTML is valid and contains screenshot data."""
        steps = [
            StepData(
                step_number=1,
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
                action_type=ActionType.NAVIGATE,
                description="Navigated to https://example.com",
                screenshot_bytes=b"\xff\xd8\xff\xe0fake-jpeg-data",
                duration_ms=500,
                success=True,
            ),
            StepData(
                step_number=2,
                timestamp=datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
                action_type=ActionType.CLICK,
                description="Clicked the submit button",
                screenshot_bytes=b"\xff\xd8\xff\xe0more-jpeg-data",
                tokens_in=100,
                tokens_out=50,
                duration_ms=200,
                success=True,
            ),
        ]

        metadata = {
            "task_id": "test-replay-123",
            "url": "https://example.com",
            "task": "Test task",
            "generated_at": "2025-01-01T00:00:02Z",
            "duration_ms": 700,
            "success": True,
        }

        generator = ReplayGenerator(steps=steps, task_metadata=metadata)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "replay.html")
            result_path = generator.generate(output_path)

            assert result_path == output_path
            assert os.path.exists(output_path)

            html = open(output_path, "r", encoding="utf-8").read()

            # HTML structure checks
            assert "<!DOCTYPE html>" in html
            assert "<html" in html
            assert "</html>" in html
            assert "Task Replay" in html

            # Contains screenshot data (base64-encoded)
            import base64
            expected_b64 = base64.standard_b64encode(b"\xff\xd8\xff\xe0fake-jpeg-data").decode()
            assert expected_b64 in html

            # Contains step metadata
            assert "test-replay-123" in html
            assert "navigate" in html
            assert "click" in html

            # No external dependencies (no http:// or https:// in CSS/JS links)
            assert 'src="http' not in html
            assert 'href="http' not in html

            # Tailwind CSS is inlined
            assert "box-sizing" in html

    def test_replay_no_screenshots(self):
        """Replay generates correctly even without screenshot data."""
        steps = [
            StepData(
                step_number=1,
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
                action_type=ActionType.NAVIGATE,
                description="Navigated",
                duration_ms=100,
                success=True,
            ),
        ]

        generator = ReplayGenerator(steps=steps, task_metadata={"task_id": "no-screenshots"})

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "replay.html")
            result_path = generator.generate(output_path)

            assert os.path.exists(result_path)
            html = open(result_path, "r", encoding="utf-8").read()
            assert "no-screenshots" in html

    def test_replay_creates_parent_dirs(self):
        """Replay generator creates parent directories if they don't exist."""
        steps = [
            StepData(
                step_number=1,
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
                action_type=ActionType.NAVIGATE,
                description="Test",
                duration_ms=50,
                success=True,
            ),
        ]

        generator = ReplayGenerator(steps=steps, task_metadata={"task_id": "dir-test"})

        with tempfile.TemporaryDirectory() as tmpdir:
            nested_path = os.path.join(tmpdir, "a", "b", "c", "replay.html")
            result_path = generator.generate(nested_path)

            assert os.path.exists(result_path)


# ---------------------------------------------------------------------------
# Test: models
# ---------------------------------------------------------------------------

class TestModels:
    def test_step_data_truncates_description(self):
        """StepData.description is capped at 500 characters."""
        long_desc = "x" * 1000
        step = StepData(
            step_number=1,
            timestamp=datetime.now(timezone.utc),
            action_type=ActionType.CLICK,
            description=long_desc,
            success=True,
        )
        assert len(step.description) == 500

    def test_action_type_enum(self):
        """ActionType enum has the expected values."""
        assert ActionType.NAVIGATE == "navigate"
        assert ActionType.CLICK == "click"
        assert ActionType.TYPE == "type"
        assert ActionType.SCROLL == "scroll"
        assert ActionType.EXTRACT == "extract"
        assert ActionType.WAIT == "wait"
        assert ActionType.INJECT_CREDENTIALS == "inject_credentials"
        assert ActionType.UNKNOWN == "unknown"

    def test_task_config_defaults(self):
        """TaskConfig has sensible defaults."""
        config = TaskConfig(url="https://example.com", task="test")
        assert config.max_steps == 50
        assert config.timeout_seconds == 300
        assert config.retry_attempts == 3
        assert config.retry_delay_seconds == 2
        assert config.max_cost_cents is None
        assert config.credentials is None
        assert config.output_schema is None
        assert config.session_id is None

    def test_task_result_defaults(self):
        """TaskResult has sensible defaults."""
        result = TaskResult(task_id="test", status="completed", success=True)
        assert result.steps == 0
        assert result.duration_ms == 0
        assert result.step_data == []
        assert result.cumulative_cost_cents == 0.0


# ---------------------------------------------------------------------------
# Test: parse_action edge cases
# ---------------------------------------------------------------------------

class TestParseAction:
    def _get_executor(self, mock_browser_manager, mock_llm_client_done):
        config = TaskConfig(url="https://example.com", task="test")
        return TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client_done,
        )

    def test_parse_plain_json(self, mock_browser_manager, mock_llm_client_done):
        executor = self._get_executor(mock_browser_manager, mock_llm_client_done)
        result = executor._parse_action('{"action_type": "click", "selector": "#btn"}')
        assert result["action_type"] == "click"

    def test_parse_markdown_json(self, mock_browser_manager, mock_llm_client_done):
        executor = self._get_executor(mock_browser_manager, mock_llm_client_done)
        text = '```json\n{"action_type": "done", "description": "finished"}\n```'
        result = executor._parse_action(text)
        assert result["action_type"] == "done"

    def test_parse_json_with_surrounding_text(self, mock_browser_manager, mock_llm_client_done):
        executor = self._get_executor(mock_browser_manager, mock_llm_client_done)
        text = 'Here is the action: {"action_type": "scroll"} end'
        result = executor._parse_action(text)
        assert result["action_type"] == "scroll"

    def test_parse_invalid_json_fallback(self, mock_browser_manager, mock_llm_client_done):
        executor = self._get_executor(mock_browser_manager, mock_llm_client_done)
        result = executor._parse_action("This is not JSON at all")
        assert result["action_type"] == "unknown"
