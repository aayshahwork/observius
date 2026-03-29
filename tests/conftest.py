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
from unittest.mock import AsyncMock, MagicMock


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
_agent_instance = MagicMock()
_agent_instance.run = AsyncMock(return_value=MagicMock())
_browser_use.Agent = MagicMock(return_value=_agent_instance)  # type: ignore[attr-defined]
_browser_use.Browser = MagicMock        # type: ignore[attr-defined]
_browser_use.BrowserProfile = MagicMock # type: ignore[attr-defined]

_bu_browser = _stub("browser_use.browser")
_bu_browser_browser = _stub(
    "browser_use.browser.browser",
    Browser=MagicMock,
    BrowserConfig=MagicMock,
)
_stub("browser_use.llm", ChatAnthropic=MagicMock)

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

# httpx — NOT stubbed (required by starlette.testclient)

# pydantic_settings ──────────────────────────────────────────────────────────
# Set environment defaults so api.config.Settings can be imported without a
# .env file.  We use the real pydantic_settings since it's installed.
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/computeruse_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY") or "test-key-placeholder"
os.environ.setdefault("BROWSERBASE_API_KEY", "test")
os.environ.setdefault("BROWSERBASE_PROJECT_ID", "test")
os.environ.setdefault("R2_ACCESS_KEY", "test")
os.environ.setdefault("R2_SECRET_KEY", "test")
os.environ.setdefault("R2_ENDPOINT", "https://test.r2.dev")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_placeholder")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")
os.environ.setdefault("API_SECRET_KEY", "test-secret")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "test-encryption-key")
os.environ.setdefault("SESSION_DIR", "./sessions")
os.environ.setdefault("REPLAY_DIR", "./replays")

# boto3 ────────────────────────────────────────────────────────────────────
_boto3 = _stub("boto3", client=MagicMock)
_botocore = _stub("botocore")
_botocore_exceptions = _stub(
    "botocore.exceptions",
    BotoCoreError=Exception,
    ClientError=Exception,
)

# psycopg2 ─────────────────────────────────────────────────────────────────
_psycopg2 = _stub("psycopg2")

# rich ───────────────────────────────────────────────────────────────────────
_rich = _stub("rich")
_rich_console = _stub("rich.console", Console=MagicMock)

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
