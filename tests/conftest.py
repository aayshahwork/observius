"""
Pytest configuration and shared fixtures.

Heavy third-party packages (anthropic, browser_use, playwright, langchain_anthropic)
are not required to run the unit tests in this directory.  We stub them out in
sys.modules before any test module is imported so that collection succeeds in
environments where only the dev dependencies (pytest, pytest-asyncio) are installed.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _stub(name: str, **attrs: object) -> types.ModuleType:
    """Create and register a minimal stub module under *name*."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# ---------------------------------------------------------------------------
# Stub heavy dependencies before they are imported by the SDK modules.
# Each stub only needs to expose the names that the SDK actually references
# at module level (import time).  Runtime behaviour is covered by per-test
# mocks using unittest.mock.
# ---------------------------------------------------------------------------

# anthropic ──────────────────────────────────────────────────────────────────
_anthropic = _stub("anthropic")
_anthropic.Anthropic = MagicMock  # type: ignore[attr-defined]

# browser_use ────────────────────────────────────────────────────────────────
_browser_use = _stub("browser_use")
_browser_use.Agent = MagicMock  # type: ignore[attr-defined]

_bu_browser = _stub("browser_use.browser")
_bu_browser_browser = _stub(
    "browser_use.browser.browser",
    Browser=MagicMock,
    BrowserConfig=MagicMock,
)

# langchain_anthropic ────────────────────────────────────────────────────────
_lca = _stub("langchain_anthropic")
_lca.ChatAnthropic = MagicMock  # type: ignore[attr-defined]

# playwright ─────────────────────────────────────────────────────────────────
_pw = _stub("playwright")
_pw_async = _stub("playwright.async_api")
_pw_async.async_playwright = MagicMock  # type: ignore[attr-defined]
_pw_async.Browser = MagicMock           # type: ignore[attr-defined]
_pw_async.BrowserContext = MagicMock    # type: ignore[attr-defined]
_pw_async.Page = MagicMock             # type: ignore[attr-defined]
_pw_async.Playwright = MagicMock       # type: ignore[attr-defined]

# aiohttp ────────────────────────────────────────────────────────────────────
_aiohttp = _stub("aiohttp")
_aiohttp.ClientSession = MagicMock  # type: ignore[attr-defined]
_aiohttp.ClientError = Exception     # type: ignore[attr-defined]

# httpx ──────────────────────────────────────────────────────────────────────
_httpx = _stub("httpx")
_httpx.AsyncClient = MagicMock   # type: ignore[attr-defined]
_httpx.Response = MagicMock      # type: ignore[attr-defined]

# pydantic_settings ──────────────────────────────────────────────────────────
# The real Settings object reads from the environment; stub it so config.py
# can be imported without a .env file present.
_pydantic_settings = _stub("pydantic_settings")

class _BaseSettings:  # minimal stand-in
    def __init__(self, **_: object) -> None: ...
    class model_config: ...  # noqa: N801

_pydantic_settings.BaseSettings = _BaseSettings          # type: ignore[attr-defined]
_pydantic_settings.SettingsConfigDict = dict             # type: ignore[attr-defined]

# Patch config.settings so modules that import it get a predictable object
# rather than triggering real environment variable validation.
import importlib, os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")
os.environ.setdefault("SESSION_DIR", "./sessions")
os.environ.setdefault("REPLAY_DIR", "./replays")

# Force re-evaluation of config with the stubs in place.
if "computeruse.config" in sys.modules:
    del sys.modules["computeruse.config"]

# rich ── already available as a dev dependency; no stub needed.

# ---------------------------------------------------------------------------
# pytest-asyncio global mode
# ---------------------------------------------------------------------------
# Applying asyncio_mode = "auto" here means every async test function is
# treated as an asyncio coroutine without needing @pytest.mark.asyncio.
# The setting is also declared in pyproject.toml but duplicated here for
# environments that run pytest without reading pyproject.toml.

def pytest_configure(config):  # type: ignore[no-untyped-def]
    config.addinivalue_line(
        "markers", "asyncio: mark test as an asyncio coroutine"
    )
