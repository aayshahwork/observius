"""Unit tests for workers/memory — MemoryStore, EpisodicMemory, LongTermMemory.

All asyncpg I/O is mocked so no live DB is needed.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.memory.episodic import EpisodicMemory
from workers.memory.longterm import LongTermMemory
from workers.memory.store import MemoryEntry, MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> MemoryStore:
    """Return a MemoryStore with a pre-initialised mock pool."""
    store = MemoryStore("postgresql://fake/db")
    store._pool = MagicMock()  # prevent RuntimeError in _acquire
    return store


def _fake_row(
    scope: str = "tenant",
    scope_id: str = "t1",
    key: str = "fix::cls:action",
    content: dict | None = None,
    provenance: dict | None = None,
) -> dict:
    return {
        "scope": scope,
        "scope_id": scope_id,
        "key": key,
        "content": content or {},
        "provenance": provenance or {},
        "safety_label": None,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_used_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }


@asynccontextmanager
async def _mock_acquire(conn):
    """Context manager that yields the given mock connection."""
    yield conn


# ---------------------------------------------------------------------------
# MemoryStore tests
# ---------------------------------------------------------------------------

class TestMemoryStore:
    def test_init_sets_url_and_no_pool(self):
        store = MemoryStore("postgresql://x/y")
        assert store._db_url == "postgresql://x/y"
        assert store._pool is None

    @pytest.mark.asyncio
    async def test_get_returns_none_on_miss(self):
        store = _make_store()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        store._pool.acquire = lambda: _mock_acquire(conn)

        result = await store.get("tenant", "t1", "missing:key")
        assert result is None
        conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_returns_entry_on_hit(self):
        store = _make_store()
        conn = AsyncMock()
        row = _fake_row(content={"foo": "bar"})
        conn.fetchrow = AsyncMock(return_value=row)
        store._pool.acquire = lambda: _mock_acquire(conn)

        entry = await store.get("tenant", "t1", "fix::cls:action")
        assert entry is not None
        assert entry.content == {"foo": "bar"}
        assert entry.scope == "tenant"
        assert entry.scope_id == "t1"
        assert entry.key == "fix::cls:action"

    @pytest.mark.asyncio
    async def test_put_calls_upsert(self):
        store = _make_store()
        conn = AsyncMock()
        store._pool.acquire = lambda: _mock_acquire(conn)

        entry = MemoryEntry(
            scope="tenant", scope_id="t1", key="site:example.com:playbook",
            content={"steps": []}, provenance={"run_id": "r1"},
        )
        await store.put(entry)

        conn.execute.assert_awaited_once()
        sql, *args = conn.execute.call_args.args
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql
        # args: scope=$1, scope_id=$2, key=$3, content=$4, provenance=$5, safety_label=$6
        assert json.loads(args[3]) == {"steps": []}
        assert json.loads(args[4]) == {"run_id": "r1"}

    @pytest.mark.asyncio
    async def test_query_filters_by_prefix(self):
        store = _make_store()
        conn = AsyncMock()
        rows = [
            _fake_row(key="fix::cls:action_a", content={"repair_action": "a"}),
            _fake_row(key="fix::cls:action_b", content={"repair_action": "b"}),
        ]
        conn.fetch = AsyncMock(return_value=rows)
        store._pool.acquire = lambda: _mock_acquire(conn)

        results = await store.query("tenant", "t1", "fix:")
        assert len(results) == 2
        # Verify LIKE pattern was passed
        sql, *args = conn.fetch.call_args.args
        assert "LIKE" in sql
        assert args[2] == "fix:%"

    @pytest.mark.asyncio
    async def test_touch_updates_last_used_at(self):
        store = _make_store()
        conn = AsyncMock()
        store._pool.acquire = lambda: _mock_acquire(conn)

        await store.touch("tenant", "t1", "some:key")

        conn.execute.assert_awaited_once()
        sql, *_ = conn.execute.call_args.args
        assert "last_used_at" in sql

    @pytest.mark.asyncio
    async def test_acquire_raises_if_not_initialised(self):
        store = MemoryStore("postgresql://x/y")  # pool = None
        with pytest.raises(RuntimeError, match="not initialised"):
            async with store._acquire():
                pass


# ---------------------------------------------------------------------------
# EpisodicMemory tests
# ---------------------------------------------------------------------------

class TestEpisodicMemory:
    @pytest.mark.asyncio
    async def test_record_failure_fix_first_write(self):
        store = AsyncMock(spec=MemoryStore)
        store.get = AsyncMock(return_value=None)
        store.put = AsyncMock()

        em = EpisodicMemory(store, run_id="run1", tenant_id="t1")
        await em.record_failure_fix(
            "element_not_found", "scroll_search", success=True,
            domain="example.com", evidence={"url": "https://example.com"},
        )

        store.put.assert_awaited_once()
        entry: MemoryEntry = store.put.call_args.args[0]
        assert entry.content["attempts"] == 1
        assert entry.content["successes"] == 1
        assert entry.content["repair_action"] == "scroll_search"
        assert entry.content["failure_class"] == "element_not_found"
        assert entry.content["domain"] == "example.com"
        assert entry.provenance == {"run_id": "run1"}

    @pytest.mark.asyncio
    async def test_record_failure_fix_increments_on_update(self):
        existing_content = {
            "attempts": 3, "successes": 2,
            "repair_action": "scroll_search", "failure_class": "element_not_found",
            "domain": "example.com", "last_evidence": {},
        }
        existing_entry = MemoryEntry(
            scope="tenant", scope_id="t1",
            key="fix:example.com:element_not_found:scroll_search",
            content=existing_content,
        )
        store = AsyncMock(spec=MemoryStore)
        store.get = AsyncMock(return_value=existing_entry)
        store.put = AsyncMock()

        em = EpisodicMemory(store, run_id="run2", tenant_id="t1")
        await em.record_failure_fix(
            "element_not_found", "scroll_search", success=False,
            domain="example.com",
        )

        entry: MemoryEntry = store.put.call_args.args[0]
        assert entry.content["attempts"] == 4
        assert entry.content["successes"] == 2  # no increment on failure

    @pytest.mark.asyncio
    async def test_get_known_fixes_filters_and_sorts(self):
        entries = [
            MemoryEntry(
                scope="tenant", scope_id="t1", key="fix::element_not_found:scroll",
                content={
                    "failure_class": "element_not_found", "repair_action": "scroll",
                    "attempts": 10, "successes": 9,  # 90% success rate
                    "domain": "example.com",
                },
            ),
            MemoryEntry(
                scope="tenant", scope_id="t1", key="fix::element_not_found:wait",
                content={
                    "failure_class": "element_not_found", "repair_action": "wait",
                    "attempts": 5, "successes": 5,  # 100% success rate
                    "domain": "example.com",
                },
            ),
            MemoryEntry(
                scope="tenant", scope_id="t1", key="fix::auth_required:re_auth",
                content={
                    "failure_class": "auth_required", "repair_action": "re_auth",
                    "attempts": 3, "successes": 3,  # different failure_class — excluded
                    "domain": "example.com",
                },
            ),
            MemoryEntry(
                scope="tenant", scope_id="t1", key="fix::element_not_found:broaden",
                content={
                    "failure_class": "element_not_found", "repair_action": "broaden",
                    "attempts": 4, "successes": 0,  # zero successes — excluded
                    "domain": "example.com",
                },
            ),
        ]
        store = AsyncMock(spec=MemoryStore)
        store.query = AsyncMock(return_value=entries)

        em = EpisodicMemory(store, run_id="run1", tenant_id="t1")
        fixes = await em.get_known_fixes("element_not_found")

        # Only 2 entries pass (correct class + successes > 0), sorted 100% first
        assert len(fixes) == 2
        assert fixes[0]["repair_action"] == "wait"    # 100%
        assert fixes[1]["repair_action"] == "scroll"  # 90%

    @pytest.mark.asyncio
    async def test_get_known_fixes_domain_filter(self):
        """Fixes from a different domain must not bleed into results when domain is specified."""
        entries = [
            MemoryEntry(
                scope="tenant", scope_id="t1", key="fix:example.com:element_not_found:scroll",
                content={
                    "failure_class": "element_not_found", "repair_action": "scroll",
                    "attempts": 2, "successes": 2, "domain": "example.com",
                },
            ),
            MemoryEntry(
                scope="tenant", scope_id="t1", key="fix:other.com:element_not_found:wait",
                content={
                    "failure_class": "element_not_found", "repair_action": "wait",
                    "attempts": 3, "successes": 3, "domain": "other.com",  # wrong domain
                },
            ),
        ]
        store = AsyncMock(spec=MemoryStore)
        store.query = AsyncMock(return_value=entries)

        em = EpisodicMemory(store, run_id="run1", tenant_id="t1")
        fixes = await em.get_known_fixes("element_not_found", domain="example.com")

        assert len(fixes) == 1
        assert fixes[0]["domain"] == "example.com"

    @pytest.mark.asyncio
    async def test_get_known_fixes_no_domain_returns_all_domains(self):
        """When domain is empty string, all matching failure_class entries are returned."""
        entries = [
            MemoryEntry(
                scope="tenant", scope_id="t1", key="fix:a.com:element_not_found:scroll",
                content={
                    "failure_class": "element_not_found", "repair_action": "scroll",
                    "attempts": 1, "successes": 1, "domain": "a.com",
                },
            ),
            MemoryEntry(
                scope="tenant", scope_id="t1", key="fix:b.com:element_not_found:wait",
                content={
                    "failure_class": "element_not_found", "repair_action": "wait",
                    "attempts": 1, "successes": 1, "domain": "b.com",
                },
            ),
        ]
        store = AsyncMock(spec=MemoryStore)
        store.query = AsyncMock(return_value=entries)

        em = EpisodicMemory(store, run_id="run1", tenant_id="t1")
        fixes = await em.get_known_fixes("element_not_found", domain="")
        assert len(fixes) == 2

    @pytest.mark.asyncio
    async def test_record_failure_fix_increments_successes_on_true_update(self):
        existing_content = {
            "attempts": 2, "successes": 1,
            "repair_action": "scroll_search", "failure_class": "element_not_found",
            "domain": "example.com", "last_evidence": {},
        }
        existing_entry = MemoryEntry(
            scope="tenant", scope_id="t1",
            key="fix:example.com:element_not_found:scroll_search",
            content=existing_content,
        )
        store = AsyncMock(spec=MemoryStore)
        store.get = AsyncMock(return_value=existing_entry)
        store.put = AsyncMock()

        em = EpisodicMemory(store, run_id="run2", tenant_id="t1")
        await em.record_failure_fix(
            "element_not_found", "scroll_search", success=True,
            domain="example.com",
        )

        entry: MemoryEntry = store.put.call_args.args[0]
        assert entry.content["attempts"] == 3
        assert entry.content["successes"] == 2  # incremented

    @pytest.mark.asyncio
    async def test_get_known_fixes_empty_when_none_match(self):
        store = AsyncMock(spec=MemoryStore)
        store.query = AsyncMock(return_value=[])

        em = EpisodicMemory(store, run_id="run1", tenant_id="t1")
        fixes = await em.get_known_fixes("captcha_challenge")
        assert fixes == []


# ---------------------------------------------------------------------------
# LongTermMemory tests
# ---------------------------------------------------------------------------

class TestLongTermMemory:
    @pytest.mark.asyncio
    async def test_get_site_playbook_miss(self):
        store = AsyncMock(spec=MemoryStore)
        store.get = AsyncMock(return_value=None)

        ltm = LongTermMemory(store, tenant_id="t1")
        result = await ltm.get_site_playbook("amazon.com")
        assert result is None
        store.get.assert_awaited_once_with("tenant", "t1", "site:amazon.com:playbook")

    @pytest.mark.asyncio
    async def test_get_site_playbook_hit(self):
        playbook = {"steps": ["login", "search"]}
        entry = MemoryEntry(scope="tenant", scope_id="t1",
                            key="site:amazon.com:playbook", content=playbook)
        store = AsyncMock(spec=MemoryStore)
        store.get = AsyncMock(return_value=entry)

        ltm = LongTermMemory(store, tenant_id="t1")
        result = await ltm.get_site_playbook("amazon.com")
        assert result == playbook

    @pytest.mark.asyncio
    async def test_save_site_playbook_delegates_to_store(self):
        store = AsyncMock(spec=MemoryStore)
        store.put = AsyncMock()

        ltm = LongTermMemory(store, tenant_id="t1")
        await ltm.save_site_playbook("amazon.com", {"steps": ["login"]})

        store.put.assert_awaited_once()
        entry: MemoryEntry = store.put.call_args.args[0]
        assert entry.scope == "tenant"
        assert entry.scope_id == "t1"
        assert entry.key == "site:amazon.com:playbook"
        assert entry.content == {"steps": ["login"]}

    @pytest.mark.asyncio
    async def test_get_compiled_route_miss(self):
        store = AsyncMock(spec=MemoryStore)
        store.get = AsyncMock(return_value=None)

        ltm = LongTermMemory(store, tenant_id="t1")
        result = await ltm.get_compiled_route("amazon.com", "checkout")
        assert result is None
        store.get.assert_awaited_once_with("tenant", "t1", "route:amazon.com:checkout")

    @pytest.mark.asyncio
    async def test_save_compiled_route_uses_correct_key(self):
        store = AsyncMock(spec=MemoryStore)
        store.put = AsyncMock()

        ltm = LongTermMemory(store, tenant_id="t1")
        await ltm.save_compiled_route("amazon.com", "checkout", {"selector": "#buy"})

        entry: MemoryEntry = store.put.call_args.args[0]
        assert entry.key == "route:amazon.com:checkout"
        assert entry.content == {"selector": "#buy"}
