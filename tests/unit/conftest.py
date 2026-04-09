"""
tests/unit/conftest.py — Unit-test-specific fixtures.

Keeps unit tests from touching real external services by providing
autouse mocks for components that require live connections.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_memory_store_init():
    """Prevent MemoryStore.init() from opening a real asyncpg connection.

    executor.execute() always creates a MemoryStore and calls init().
    In unit tests there is no live database, so we stub it out globally.
    The MemoryStore CRUD methods are still patchable per-test when needed.
    """
    with patch("workers.memory.store.MemoryStore.init", new_callable=AsyncMock):
        yield
