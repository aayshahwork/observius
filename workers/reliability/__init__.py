"""
workers/reliability — Self-healing repair layer for the PAV loop.

Classifies failures, checks circuit breakers, and executes repair
playbooks before falling through to replan.
"""

from .circuit_breaker import CircuitBreaker
from .detectors import classify_outcome
from .playbooks import RepairAction, RepairStrategy, get_playbook
from .repair_loop import run_repair

__all__ = [
    "CircuitBreaker",
    "classify_outcome",
    "RepairAction",
    "RepairStrategy",
    "get_playbook",
    "run_repair",
]
