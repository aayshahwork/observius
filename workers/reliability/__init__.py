"""Reliability subsystem — failure detectors, repair playbooks, and circuit breaker."""

from workers.reliability.circuit_breaker import CircuitBreaker
from workers.reliability.detectors import (
    detect_failure,
    detect_from_error_code,
    detect_from_error_text,
    detect_from_network_signals,
    detect_from_outcome,
    detect_from_url,
    detect_stuck,
)
from workers.reliability.playbooks import (
    REPAIR_PLAYBOOK,
    RepairAction,
    repair_action_to_intent,
)
from workers.reliability.repair_loop import (
    BackendCapabilities,
    CUABackend,
    Planner,
    SubGoal,
    Validator,
    run_repair,
)

__all__ = [
    "CircuitBreaker",
    "REPAIR_PLAYBOOK",
    "RepairAction",
    "detect_failure",
    "detect_from_error_code",
    "detect_from_error_text",
    "detect_from_network_signals",
    "detect_from_outcome",
    "detect_from_url",
    "detect_stuck",
    "repair_action_to_intent",
    "run_repair",
    # Protocols — for callers who want to type-check their implementations
    "BackendCapabilities",
    "CUABackend",
    "Planner",
    "SubGoal",
    "Validator",
]
