"""Tests for computeruse.desktop — screenshot factory functions."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from computeruse.desktop import (
    mss_screenshot_fn,
    pillow_screenshot_fn,
    pyautogui_screenshot_fn,
)
from computeruse.tracker import PokantTracker, TrackerConfig


class TestPyAutoGUIScreenshotFn:

    def test_returns_png_bytes(self) -> None:
        fake_img = MagicMock()

        def save_side_effect(buf: io.BytesIO, format: str = "PNG") -> None:
            buf.write(b"fake-png-data")

        fake_img.save.side_effect = save_side_effect

        with patch.dict("sys.modules", {"pyautogui": MagicMock()}):
            import sys
            sys.modules["pyautogui"].screenshot.return_value = fake_img

            fn = pyautogui_screenshot_fn()
            result = fn()

        assert isinstance(result, bytes)
        assert result == b"fake-png-data"


class TestPillowScreenshotFn:

    def test_returns_png_bytes(self) -> None:
        fake_img = MagicMock()

        def save_side_effect(buf: io.BytesIO, format: str = "PNG") -> None:
            buf.write(b"pillow-png-data")

        fake_img.save.side_effect = save_side_effect

        mock_imagegrab = MagicMock()
        mock_imagegrab.grab.return_value = fake_img

        mock_pil = MagicMock()
        mock_pil.ImageGrab = mock_imagegrab

        with patch.dict("sys.modules", {
            "PIL": mock_pil,
            "PIL.ImageGrab": mock_imagegrab,
        }):
            fn = pillow_screenshot_fn()
            result = fn()

        assert isinstance(result, bytes)
        assert result == b"pillow-png-data"


class TestMSSScreenshotFn:

    def test_returns_png_bytes(self) -> None:
        fake_png = b"mss-png-data"
        fake_shot = MagicMock()
        fake_shot.rgb = b"\x00" * 12
        fake_shot.size = (2, 2)

        mock_sct = MagicMock()
        mock_sct.monitors = [{}, {"top": 0, "left": 0, "width": 2, "height": 2}]
        mock_sct.grab.return_value = fake_shot
        mock_sct.__enter__ = MagicMock(return_value=mock_sct)
        mock_sct.__exit__ = MagicMock(return_value=False)

        mock_tools = MagicMock()
        mock_tools.to_png.return_value = fake_png

        mock_mss_module = MagicMock()
        mock_mss_module.mss.return_value = mock_sct
        mock_mss_module.tools = mock_tools

        with patch.dict("sys.modules", {
            "mss": mock_mss_module,
            "mss.tools": mock_tools,
        }):
            fn = mss_screenshot_fn(monitor=1)
            result = fn()

        assert result == fake_png


class TestScreenshotFnWithTracker:

    def test_tracker_uses_screenshot_fn(self, tmp_path: Path) -> None:
        """Tracker with a desktop screenshot_fn captures on every step."""
        expected_bytes = b"desktop-screenshot"

        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            screenshot_fn=lambda: expected_bytes,
            enable_stuck_detection=False,
        ))
        tracker.start()

        step1 = tracker.record_desktop_step(
            action_type="desktop_click",
            description="Click start menu",
            window_title="Taskbar",
        )
        step2 = tracker.record_desktop_step(
            action_type="desktop_type",
            description="Typed search query",
            window_title="Search",
        )

        assert step1.screenshot_bytes == expected_bytes
        assert step2.screenshot_bytes == expected_bytes
        assert step1.context["type"] == "desktop_action"
        assert step2.context["window_title"] == "Search"
