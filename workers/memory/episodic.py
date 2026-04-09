"""workers/memory/episodic.py — Per-run failure/fix tracking.

Queried across retries to avoid repeating repair actions that have failed
before, and to promote actions that have worked.
"""

from __future__ import annotations

import logging

from workers.memory.store import MemoryEntry, MemoryStore

logger = logging.getLogger(__name__)


class EpisodicMemory:
    """Per-tenant episodic memory for failure/fix outcomes.

    Keys follow the pattern:
        fix:{domain}:{failure_class}:{repair_action}

    Content schema::

        {
            "attempts":      int,
            "successes":     int,
            "repair_action": str,
            "failure_class": str,
            "domain":        str,
            "last_evidence": dict,
        }
    """

    def __init__(self, store: MemoryStore, run_id: str, tenant_id: str) -> None:
        self.store = store
        self.run_id = run_id
        self.tenant_id = tenant_id

    async def record_failure_fix(
        self,
        failure_class: str,
        repair_action: str,
        success: bool,
        domain: str,
        evidence: dict | None = None,
    ) -> None:
        """Record whether a repair action succeeded or failed for a given failure class."""
        key = f"fix:{domain}:{failure_class}:{repair_action}"
        existing = await self.store.get("tenant", self.tenant_id, key)

        if existing:
            content = dict(existing.content)
            content["attempts"] = content.get("attempts", 0) + 1
            content["successes"] = content.get("successes", 0) + (1 if success else 0)
            content["last_evidence"] = evidence or {}
        else:
            content = {
                "attempts": 1,
                "successes": 1 if success else 0,
                "repair_action": repair_action,
                "failure_class": failure_class,
                "domain": domain,
                "last_evidence": evidence or {},
            }

        await self.store.put(MemoryEntry(
            scope="tenant",
            scope_id=self.tenant_id,
            key=key,
            content=content,
            provenance={"run_id": self.run_id},
        ))

    async def get_known_fixes(
        self, failure_class: str, domain: str = ""
    ) -> list[dict]:
        """Return repair actions that have worked before, sorted by success rate desc.

        Only entries with at least one success are returned.
        """
        entries = await self.store.query("tenant", self.tenant_id, "fix:")
        fixes = [
            e.content for e in entries
            if e.content.get("failure_class") == failure_class
            and e.content.get("successes", 0) > 0
            and (not domain or e.content.get("domain") == domain)
        ]
        fixes.sort(
            key=lambda f: f["successes"] / max(f["attempts"], 1),
            reverse=True,
        )
        return fixes
