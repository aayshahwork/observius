"""
workers/shared_types/validation.py — Validation verdicts and outcomes.

ValidatorVerdict: pass/fail/warn/skip for a single check.
ValidatorOutcome: full result of running one or more validators on a step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional


class ValidatorVerdict(StrEnum):
    """Result of a single validation check."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"
    UNCERTAIN = "uncertain"


@dataclass
class ValidatorOutcome:
    """Result of running validators against a step.

    Each step can have multiple checks (URL pattern, element presence,
    text content, form values). This collects all individual verdicts
    into a single outcome.
    """

    verdict: ValidatorVerdict = ValidatorVerdict.SKIP
    check_name: str = ""
    expected: str = ""
    actual: str = ""
    message: str = ""
    is_critical: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    failure_class: Optional[str] = None   # set by repair_loop
    patch_applied: Optional[str] = None   # set by repair_loop

    @property
    def passed(self) -> bool:
        """Whether this check passed or was skipped (non-blocking)."""
        return self.verdict in (ValidatorVerdict.PASS, ValidatorVerdict.SKIP)

    @property
    def failed(self) -> bool:
        """Whether this check had a hard failure."""
        return self.verdict == ValidatorVerdict.FAIL

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage."""
        d = {
            "verdict": self.verdict.value,
            "check_name": self.check_name,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
            "is_critical": self.is_critical,
        }
        if self.failure_class is not None:
            d["failure_class"] = self.failure_class
        if self.patch_applied is not None:
            d["patch_applied"] = self.patch_applied
        return d
