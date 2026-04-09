"""workers/memory/longterm.py — Site-specific long-term knowledge.

Stores playbooks, known selectors, login flows, and compiled routes
keyed by domain so they survive across task runs.
"""

from __future__ import annotations

import logging

from workers.memory.store import MemoryEntry, MemoryStore

logger = logging.getLogger(__name__)


class LongTermMemory:
    """Per-tenant long-term memory for site-specific knowledge."""

    def __init__(self, store: MemoryStore, tenant_id: str) -> None:
        self.store = store
        self.tenant_id = tenant_id

    async def get_site_playbook(self, domain: str) -> dict | None:
        entry = await self.store.get("tenant", self.tenant_id, f"site:{domain}:playbook")
        return entry.content if entry else None

    async def save_site_playbook(self, domain: str, playbook: dict) -> None:
        await self.store.put(MemoryEntry(
            scope="tenant",
            scope_id=self.tenant_id,
            key=f"site:{domain}:playbook",
            content=playbook,
        ))

    async def get_compiled_route(self, domain: str, workflow_type: str) -> dict | None:
        entry = await self.store.get(
            "tenant", self.tenant_id, f"route:{domain}:{workflow_type}"
        )
        return entry.content if entry else None

    async def save_compiled_route(
        self, domain: str, workflow_type: str, route: dict
    ) -> None:
        await self.store.put(MemoryEntry(
            scope="tenant",
            scope_id=self.tenant_id,
            key=f"route:{domain}:{workflow_type}",
            content=route,
        ))
