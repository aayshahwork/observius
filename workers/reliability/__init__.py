"""
workers/reliability — Self-healing repair layer for the PAV loop.

Classifies failures, checks circuit breakers, and executes repair
playbooks before falling through to replan.
"""

from .circuit_breaker import CircuitBreaker
from .detectors import classify_outcome, detect_failure
from .playbooks import RepairAction, RepairStrategy, get_playbook
from .repair_loop import run_repair

__all__ = [
    "CircuitBreaker",
    "classify_outcome",
    "detect_failure",
    "RepairAction",
    "RepairStrategy",
    "get_playbook",
    "run_repair",
]
