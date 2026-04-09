"""Validation types — validator verdicts and outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ValidatorVerdict(StrEnum):
    PASS = "pass"
    FAIL_UI = "fail_ui"
    FAIL_GOAL = "fail_goal"
    FAIL_NETWORK = "fail_network"
    FAIL_POLICY = "fail_policy"
    FAIL_STUCK = "fail_stuck"
    UNCERTAIN = "uncertain"


@dataclass
class ValidatorOutcome:
    verdict: ValidatorVerdict
    evidence: dict[str, Any] = field(default_factory=dict)
    failure_class: str | None = None  # from FailureClass enum
    message: str = ""
    confidence: float = 1.0  # 0-1, how sure the validator is
