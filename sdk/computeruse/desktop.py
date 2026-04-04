"""Desktop screenshot helpers for PokantTracker.

Convenience factory functions that return zero-arg callables suitable for
the ``screenshot_fn`` parameter of :class:`~computeruse.tracker.TrackerConfig`.

All underlying libraries (``pyautogui``, ``Pillow``, ``mss``) are imported
lazily inside the returned closure, so they remain optional dependencies.

Usage::

    from computeruse import PokantTracker
    from computeruse.desktop import pyautogui_screenshot_fn

    tracker = PokantTracker(
        screenshot_fn=pyautogui_screenshot_fn(),
        task_description="SAP invoice processing",
    )
"""

from __future__ import annotations

from typing import Callable


def pyautogui_screenshot_fn() -> Callable[[], bytes]:
    """Return a screenshot callable that uses PyAutoGUI.

    Captures the full primary screen as a PNG.

    Requires: ``pip install pyautogui``
    """

    def take() -> bytes:
        import io

        import pyautogui

        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    return take


def pillow_screenshot_fn() -> Callable[[], bytes]:
    """Return a screenshot callable that uses Pillow's ImageGrab.

    Captures the full primary screen as a PNG.

    Requires: ``pip install Pillow``

    Note: ``ImageGrab`` works on macOS and Windows.  On Linux, it
    requires the ``scrot`` utility or an X11 display.
    """

    def take() -> bytes:
        import io

        from PIL import ImageGrab

        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    return take


def mss_screenshot_fn(monitor: int = 1) -> Callable[[], bytes]:
    """Return a screenshot callable that uses ``mss`` (cross-platform, fast).

    Args:
        monitor: Monitor index (1 = primary). 0 captures all monitors
            stitched together.

    Requires: ``pip install mss``
    """

    def take() -> bytes:
        import mss
        import mss.tools

        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[monitor])
            return mss.tools.to_png(shot.rgb, shot.size)

    return take
