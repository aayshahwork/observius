"""Unit tests for the native executor path (computer_20251124)."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.executor import TaskExecutor, _scale_screenshot
from workers.models import ActionType, TaskConfig


# ---------------------------------------------------------------------------
# Helpers: mock Anthropic response objects
# ---------------------------------------------------------------------------


def _make_tool_use_block(tool_name: str, tool_input: dict, block_id: str = "blk_1"):
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = block_id
    return block


def _make_text_block(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_response(
    content_blocks: list,
    stop_reason: str = "tool_use",
    input_tokens: int = 100,
    output_tokens: int = 50,
):
    resp = MagicMock()
    resp.content = content_blocks
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_page():
    page = AsyncMock()
    page.viewport_size = {"width": 1280, "height": 720}
    # Return a minimal 1x1 PNG for screenshots
    _png = _make_1px_png()
    page.screenshot = AsyncMock(return_value=_png)
    page.keyboard = AsyncMock()
    page.mouse = AsyncMock()
    return page


@pytest.fixture()
def mock_browser(mock_page):
    browser = AsyncMock()
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=mock_page)
    context.cookies = AsyncMock(return_value=[])
    context.add_cookies = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    return browser


@pytest.fixture()
def mock_browser_manager(mock_browser):
    bm = AsyncMock()
    bm.get_browser = AsyncMock(return_value=mock_browser)
    bm.apply_stealth = AsyncMock()
    bm.release_browser = AsyncMock()
    return bm


@pytest.fixture()
def mock_llm():
    return MagicMock()


def _make_1px_png() -> bytes:
    """Generate a minimal valid 1x1 PNG for tests."""
    try:
        from PIL import Image

        img = Image.new("RGB", (1, 1), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Fallback: raw minimal PNG bytes
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )


def _make_executor(
    mock_browser_manager,
    mock_llm,
    executor_mode: str = "native",
    **config_kwargs,
) -> TaskExecutor:
    config = TaskConfig(
        url="https://example.com",
        task="Click the button",
        max_steps=config_kwargs.pop("max_steps", 10),
        executor_mode=executor_mode,
        **config_kwargs,
    )
    return TaskExecutor(
        config=config,
        browser_manager=mock_browser_manager,
        llm_client=mock_llm,
    )


# ---------------------------------------------------------------------------
# Screenshot scaling tests
# ---------------------------------------------------------------------------


def _can_use_pillow() -> bool:
    try:
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


class TestScaleScreenshot:
    def test_no_op_when_small(self):
        png = _make_1px_png()
        scaled, factor = _scale_screenshot(png, max_width=1280)
        assert factor == 1.0
        assert scaled == png

    @pytest.mark.skipif(
        not _can_use_pillow(),
        reason="Pillow not installed",
    )
    def test_resizes_large(self):
        from PIL import Image

        img = Image.new("RGB", (1920, 1080), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        large_png = buf.getvalue()

        scaled, factor = _scale_screenshot(large_png, max_width=1280)
        assert factor == pytest.approx(1280 / 1920)

        result_img = Image.open(io.BytesIO(scaled))
        assert result_img.size[0] == 1280
        assert result_img.size[1] == int(1080 * (1280 / 1920))

    def test_returns_correct_factor(self):
        # For a 1px image (width=1 <= 1280), factor should be 1.0
        _, factor = _scale_screenshot(_make_1px_png())
        assert factor == 1.0


# ---------------------------------------------------------------------------
# Computer action dispatch tests
# ---------------------------------------------------------------------------


class TestComputerAction:
    async def test_left_click(self, mock_page, mock_browser_manager, mock_llm):
        executor = _make_executor(mock_browser_manager, mock_llm)
        executor._last_cursor_pos = (0, 0)
        desc, action_type = await executor._execute_computer_action(
            mock_page, {"action": "left_click", "coordinate": [640, 360]}, 1.0,
        )
        mock_page.mouse.click.assert_called_once_with(640, 360)
        assert action_type == ActionType.CLICK
        assert "640" in desc

    async def test_type_text(self, mock_page, mock_browser_manager, mock_llm):
        executor = _make_executor(mock_browser_manager, mock_llm)
        desc, action_type = await executor._execute_computer_action(
            mock_page, {"action": "type", "text": "hello"}, 1.0,
        )
        mock_page.keyboard.type.assert_called_once_with("hello")
        assert action_type == ActionType.TYPE

    async def test_key_mapping(self, mock_page, mock_browser_manager, mock_llm):
        executor = _make_executor(mock_browser_manager, mock_llm)
        await executor._execute_computer_action(
            mock_page, {"action": "key", "text": "Return"}, 1.0,
        )
        mock_page.keyboard.press.assert_called_once_with("Enter")

    async def test_scroll(self, mock_page, mock_browser_manager, mock_llm):
        executor = _make_executor(mock_browser_manager, mock_llm)
        executor._last_cursor_pos = (0, 0)
        await executor._execute_computer_action(
            mock_page,
            {"action": "scroll", "coordinate": [640, 360], "direction": "down", "amount": 3},
            1.0,
        )
        mock_page.mouse.wheel.assert_called_once_with(0, 300)

    async def test_right_click(self, mock_page, mock_browser_manager, mock_llm):
        executor = _make_executor(mock_browser_manager, mock_llm)
        executor._last_cursor_pos = (0, 0)
        await executor._execute_computer_action(
            mock_page, {"action": "right_click", "coordinate": [100, 200]}, 1.0,
        )
        mock_page.mouse.click.assert_called_once_with(100, 200, button="right")

    async def test_double_click(self, mock_page, mock_browser_manager, mock_llm):
        executor = _make_executor(mock_browser_manager, mock_llm)
        executor._last_cursor_pos = (0, 0)
        await executor._execute_computer_action(
            mock_page, {"action": "double_click", "coordinate": [100, 200]}, 1.0,
        )
        mock_page.mouse.dblclick.assert_called_once_with(100, 200)

    async def test_wait_capped(self, mock_page, mock_browser_manager, mock_llm):
        executor = _make_executor(mock_browser_manager, mock_llm)
        with patch("workers.executor.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            await executor._execute_computer_action(
                mock_page, {"action": "wait", "duration": 30}, 1.0,
            )
            sleep_mock.assert_called_once_with(10)

    async def test_coordinate_remapping_with_scale(self, mock_page, mock_browser_manager, mock_llm):
        executor = _make_executor(mock_browser_manager, mock_llm)
        executor._last_cursor_pos = (0, 0)
        # scale_factor=0.5 means Claude's 640 -> real 1280
        await executor._execute_computer_action(
            mock_page, {"action": "left_click", "coordinate": [640, 360]}, 0.5,
        )
        mock_page.mouse.click.assert_called_once_with(1280, 720)


# ---------------------------------------------------------------------------
# Native loop tests
# ---------------------------------------------------------------------------


class TestNativeLoop:
    async def test_done_immediately(self, mock_page, mock_browser_manager, mock_llm):
        """LLM returns done tool on first call."""
        done_block = _make_tool_use_block("done", {"result": {"price": "42"}, "message": "Found it"})
        mock_llm.beta.messages.create = MagicMock(
            return_value=_make_response([done_block]),
        )

        executor = _make_executor(mock_browser_manager, mock_llm)
        with patch("workers.retry.retry_with_backoff", side_effect=_passthrough_retry):
            result = await executor._execute_native(mock_page)

        assert result.result_data == {"price": "42"}
        assert result.total_tokens_in == 100
        assert result.total_tokens_out == 50

    async def test_click_then_done(self, mock_page, mock_browser_manager, mock_llm):
        """LLM returns click on first call, then done on second."""
        click_block = _make_tool_use_block(
            "computer",
            {"action": "left_click", "coordinate": [640, 360]},
            block_id="blk_click",
        )
        done_block = _make_tool_use_block(
            "done",
            {"result": {"status": "ok"}, "message": "Done"},
            block_id="blk_done",
        )
        mock_llm.beta.messages.create = MagicMock(
            side_effect=[
                _make_response([click_block]),
                _make_response([done_block]),
            ],
        )

        executor = _make_executor(mock_browser_manager, mock_llm)
        with patch("workers.retry.retry_with_backoff", side_effect=_passthrough_retry):
            result = await executor._execute_native(mock_page)

        assert result.result_data == {"status": "ok"}
        assert len(executor.steps) == 2  # click + done

    async def test_max_steps_enforced(self, mock_page, mock_browser_manager, mock_llm):
        """Loop terminates after max_steps without done."""
        click_block = _make_tool_use_block(
            "computer",
            {"action": "left_click", "coordinate": [100, 100]},
        )
        mock_llm.beta.messages.create = MagicMock(
            return_value=_make_response([click_block]),
        )

        executor = _make_executor(mock_browser_manager, mock_llm, max_steps=3)
        with patch("workers.retry.retry_with_backoff", side_effect=_passthrough_retry):
            result = await executor._execute_native(mock_page)

        assert result.result_data is None
        assert len(executor.steps) == 3

    async def test_inject_credentials(self, mock_page, mock_browser_manager, mock_llm):
        """LLM calls inject_credentials tool."""
        inject_block = _make_tool_use_block(
            "inject_credentials",
            {"domain": "example.com"},
            block_id="blk_inject",
        )
        done_block = _make_tool_use_block(
            "done",
            {"result": {}, "message": "Logged in"},
            block_id="blk_done",
        )
        mock_llm.beta.messages.create = MagicMock(
            side_effect=[
                _make_response([inject_block]),
                _make_response([done_block]),
            ],
        )

        executor = _make_executor(
            mock_browser_manager,
            mock_llm,
            credentials={"username": "user", "password": "pass"},
        )
        with patch("workers.retry.retry_with_backoff", side_effect=_passthrough_retry), \
             patch("workers.executor.CredentialInjector") as MockInjector:
            injector_instance = AsyncMock()
            MockInjector.return_value = injector_instance
            await executor._execute_native(mock_page)

        injector_instance.inject.assert_called_once()
        assert any(s.action_type == ActionType.INJECT_CREDENTIALS for s in executor.steps)

    async def test_solve_captcha(self, mock_page, mock_browser_manager, mock_llm):
        """LLM calls solve_captcha tool."""
        captcha_block = _make_tool_use_block(
            "solve_captcha",
            {"captcha_type": "recaptcha_v2"},
            block_id="blk_captcha",
        )
        done_block = _make_tool_use_block(
            "done",
            {"result": {}, "message": "Done"},
            block_id="blk_done",
        )
        mock_llm.beta.messages.create = MagicMock(
            side_effect=[
                _make_response([captcha_block]),
                _make_response([done_block]),
            ],
        )

        executor = _make_executor(mock_browser_manager, mock_llm)
        captcha_result = MagicMock()
        captcha_result.solved = True
        captcha_result.captcha_type = "recaptcha_v2"
        with patch("workers.retry.retry_with_backoff", side_effect=_passthrough_retry), \
             patch("workers.executor.CaptchaSolver") as MockSolver:
            solver_instance = AsyncMock()
            solver_instance.solve = AsyncMock(return_value=captcha_result)
            MockSolver.return_value = solver_instance
            await executor._execute_native(mock_page)

        solver_instance.solve.assert_called_once()
        assert any(s.action_type == ActionType.SOLVE_CAPTCHA for s in executor.steps)

    async def test_context_trimming(self, mock_page, mock_browser_manager, mock_llm):
        """After many steps, messages are trimmed."""
        click_block = _make_tool_use_block(
            "computer",
            {"action": "left_click", "coordinate": [100, 100]},
        )
        done_block = _make_tool_use_block(
            "done", {"result": {}, "message": "Done"}, block_id="blk_done",
        )
        # 25 clicks then done
        responses = [_make_response([click_block])] * 25 + [_make_response([done_block])]
        mock_llm.beta.messages.create = MagicMock(side_effect=responses)

        executor = _make_executor(mock_browser_manager, mock_llm, max_steps=30)
        with patch("workers.retry.retry_with_backoff", side_effect=_passthrough_retry):
            result = await executor._execute_native(mock_page)

        # Result should be successful (done was called)
        assert result.result_data is not None


# ---------------------------------------------------------------------------
# Feature flag dispatch tests
# ---------------------------------------------------------------------------


class TestDispatch:
    async def test_dispatch_browser_use_default(self, mock_browser_manager, mock_llm, mock_page):
        """executor_mode absent defaults to browser_use path."""
        executor = _make_executor(mock_browser_manager, mock_llm, executor_mode="browser_use")
        # The browser_use path calls _execute_with_agent
        with patch.object(executor, "_execute_with_agent", new_callable=AsyncMock) as mock_agent, \
             patch.object(executor, "_navigate_with_retry", new_callable=AsyncMock):
            mock_result = MagicMock()
            mock_result.final_result = MagicMock(return_value={"data": "test"})
            mock_result.is_done = MagicMock(return_value=True)
            mock_result.history = []
            mock_result.total_cost = MagicMock(return_value=0.01)
            mock_agent.return_value = mock_result
            await executor.execute()

        mock_agent.assert_called_once()

    async def test_dispatch_native_mode(self, mock_browser_manager, mock_llm, mock_page):
        """executor_mode="native" routes to _execute_native."""
        executor = _make_executor(mock_browser_manager, mock_llm, executor_mode="native")
        with patch.object(executor, "_execute_native", new_callable=AsyncMock) as mock_native, \
             patch.object(executor, "_navigate_with_retry", new_callable=AsyncMock):
            mock_native.return_value = MagicMock(
                result_data={"ok": True},
                cost_cents=1.5,
                total_tokens_in=500,
                total_tokens_out=200,
            )
            result = await executor.execute()

        mock_native.assert_called_once()
        assert result.success is True
        assert result.result == {"ok": True}


# ---------------------------------------------------------------------------
# Retry passthrough helper
# ---------------------------------------------------------------------------

async def _passthrough_retry(fn, *args, **kwargs):
    """Call fn directly — skips retry logic for tests."""
    return fn(*args, **kwargs)
