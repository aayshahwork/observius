"""
tests/unit/test_backends_protocol.py — Verify backend protocol, capabilities, and registry.
"""

from __future__ import annotations

import asyncio

import pytest

from workers.backends.protocol import BackendCapabilities, CUABackend
from workers.backends.registry import backend_for_task
from workers.backends._browser_use import BrowserUseBackend
from workers.backends.native_anthropic import NativeAnthropicBackend
from workers.backends.skyvern import SkyvernBackend
from workers.models import TaskConfig
from workers.shared_types import Observation, StepIntent, StepResult


# ---------------------------------------------------------------------------
# BackendCapabilities defaults
# ---------------------------------------------------------------------------


class TestBackendCapabilities:
    def test_defaults(self):
        caps = BackendCapabilities()
        assert caps.supports_single_step is False
        assert caps.supports_goal_delegation is True
        assert caps.supports_screenshots is True
        assert caps.supports_har is False
        assert caps.supports_trace is False
        assert caps.supports_video is False
        assert caps.supports_ax_tree is False

    def test_override(self):
        caps = BackendCapabilities(
            supports_single_step=True,
            supports_har=True,
            supports_ax_tree=True,
        )
        assert caps.supports_single_step is True
        assert caps.supports_har is True
        assert caps.supports_ax_tree is True
        # Others remain default
        assert caps.supports_video is False


# ---------------------------------------------------------------------------
# Registry: correct backend type for each executor_mode
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_browser_use_mode(self):
        config = TaskConfig(url="https://example.com", task="test", executor_mode="browser_use")
        backend = backend_for_task(config)
        assert isinstance(backend, BrowserUseBackend)
        assert backend.name == "browser_use"

    def test_native_mode(self):
        config = TaskConfig(url="https://example.com", task="test", executor_mode="native")
        backend = backend_for_task(config)
        assert isinstance(backend, NativeAnthropicBackend)
        assert backend.name == "native"

    def test_skyvern_mode(self):
        config = TaskConfig(url="https://example.com", task="test", executor_mode="skyvern")
        backend = backend_for_task(config)
        assert isinstance(backend, SkyvernBackend)
        assert backend.name == "skyvern"

    def test_unknown_mode_raises(self):
        config = TaskConfig(url="https://example.com", task="test", executor_mode="nonexistent")
        with pytest.raises(ValueError, match="Unknown executor_mode"):
            backend_for_task(config)

    def test_default_mode_is_browser_use(self):
        config = TaskConfig(url="https://example.com", task="test")
        assert config.executor_mode == "browser_use"
        backend = backend_for_task(config)
        assert isinstance(backend, BrowserUseBackend)


# ---------------------------------------------------------------------------
# Protocol: verify all backends satisfy CUABackend
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Verify each backend class satisfies the CUABackend protocol."""

    @pytest.mark.parametrize("cls", [BrowserUseBackend, NativeAnthropicBackend, SkyvernBackend])
    def test_is_cua_backend(self, cls):
        instance = cls()
        assert isinstance(instance, CUABackend)

    @pytest.mark.parametrize("cls", [BrowserUseBackend, NativeAnthropicBackend, SkyvernBackend])
    def test_has_name_property(self, cls):
        instance = cls()
        assert isinstance(instance.name, str)
        assert len(instance.name) > 0

    @pytest.mark.parametrize("cls", [BrowserUseBackend, NativeAnthropicBackend, SkyvernBackend])
    def test_has_required_methods(self, cls):
        instance = cls()
        assert callable(getattr(instance, "initialize", None))
        assert callable(getattr(instance, "execute_step", None))
        assert callable(getattr(instance, "execute_goal", None))
        assert callable(getattr(instance, "get_observation", None))
        assert callable(getattr(instance, "teardown", None))

    @pytest.mark.parametrize("cls", [BrowserUseBackend, NativeAnthropicBackend, SkyvernBackend])
    def test_initialize_is_async(self, cls):
        instance = cls()
        coro = instance.initialize({})
        assert asyncio.iscoroutine(coro)
        coro.close()

    @pytest.mark.parametrize("cls", [BrowserUseBackend, NativeAnthropicBackend, SkyvernBackend])
    def test_teardown_runs(self, cls):
        instance = cls()
        # teardown should complete without error (it's a no-op on stubs)
        asyncio.run(instance.teardown())


# ---------------------------------------------------------------------------
# Shared types: smoke test
# ---------------------------------------------------------------------------


class TestSharedTypes:
    def test_step_intent_defaults(self):
        intent = StepIntent()
        assert intent.action_type == ""
        assert intent.description == ""

    def test_step_result_defaults(self):
        result = StepResult()
        assert result.success is True
        assert result.error is None
        assert result.duration_ms == 0

    def test_observation_defaults(self):
        obs = Observation()
        assert obs.url == ""
        assert obs.has_screenshot is False

    def test_observation_has_screenshot(self):
        obs = Observation(screenshot_b64="iVBORw0KGgo=")
        assert obs.has_screenshot is True
