"""
workers/backends/registry.py — Maps executor_mode to the correct backend class.

Uses lazy imports so missing optional dependencies (browser-use, skyvern)
don't crash at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workers.backends.protocol import CUABackend
from workers.models import TaskConfig

if TYPE_CHECKING:
    pass


def backend_for_task(config: TaskConfig) -> CUABackend:
    """Return an *uninitialized* backend instance for the given task config.

    Call ``await backend.initialize(...)`` before use.

    Raises ``ValueError`` for unknown executor_mode values.
    """
    mode = config.executor_mode

    if mode == "browser_use":
        from workers.backends._browser_use import BrowserUseBackend

        return BrowserUseBackend()

    if mode == "native":
        from workers.backends.native_anthropic import NativeAnthropicBackend

        return NativeAnthropicBackend()

    if mode == "skyvern":
        from workers.backends.skyvern import SkyvernBackend

        return SkyvernBackend()

    raise ValueError(f"Unknown executor_mode: {mode!r}")
