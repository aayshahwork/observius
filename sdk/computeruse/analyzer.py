"""
computeruse/analyzer.py -- Post-execution failure analysis.

Tier 1 rule-based analyzer that inspects StepData and produces
actionable AnalysisFinding results.  Runs after task completion.

Tier 2 (cross-run history) is deferred.
Tier 3 (LLM-assisted) is implemented via LLMAnalyzer — opt-in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from computeruse.models import StepData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisFinding:
    """A single finding from failure analysis."""

    tier: int  # 1, 2, or 3
    category: str  # e.g. "stuck_loop", "permission_error"
    summary: str  # Human-readable one-liner
    suggestion: str  # Actionable fix
    confidence: float  # 0.0-1.0
    evidence: str  # What data supports this
    step_range: tuple[int, int] | None = None  # affected steps (start, end)


@dataclass
class RunAnalysis:
    """Full analysis result for a completed task run."""

    findings: list[AnalysisFinding]
    summary: str  # Overall what went wrong
    primary_suggestion: str  # Single most important fix
    wasted_steps: int = 0
    wasted_cost_cents: float = 0.0
    tiers_executed: list[int] = field(default_factory=lambda: [1])


@dataclass(frozen=True)
class AnalysisConfig:
    """Configuration for analysis tiers."""

    enable_analysis: bool = True
    enable_history: bool = True  # Tier 2
    llm_api_key: str | None = None  # Tier 3 opt-in
    llm_model: str = "claude-haiku-4-5-20251001"  # cheapest, fastest
    # Supported: "claude-haiku-4-5-20251001" (~$0.005/analysis)
    #            "claude-sonnet-4-6" (~$0.02/analysis)
    #            "claude-opus-4-6" (~$0.10/analysis, most accurate)
    always_use_llm: bool = False
    max_history_runs: int = 20


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KEYBOARD_ACTIONS = frozenset({
    "key_press", "desktop_hotkey", "desktop_type", "type",
})

_CLICK_ACTIONS = frozenset({
    "click", "double_click", "right_click", "middle_click",
    "triple_click", "desktop_click",
})

_NAVIGATE_ACTIONS = frozenset({"navigate"})

_PERMISSION_KEYWORDS = (
    "accessibility", "permission", "not allowed", "denied",
    "blocked", "authorization", "privilege", "security", "sandboxed",
)

_MACOS_KEYWORDS = (
    "accessibility access", "system preferences", "tccutil",
    "system settings", "privacy & security",
)

_NETWORK_ERROR_MAP: list[tuple[tuple[str, ...], str]] = [
    (("err_name_not_resolved", "dns"),
     "Domain doesn't resolve. Check URL spelling and DNS settings."),
    (("connection refused", "econnrefused"),
     "Server not accepting connections. Check if the service is running and the port is correct."),
    (("econnreset", "connection reset"),
     "Connection dropped by server. May be rate limiting or firewall blocking."),
    (("ssl", "certificate"),
     "SSL/TLS error. The site may have an expired certificate, or check your system clock."),
    (("err_too_many_redirects",),
     "Redirect loop detected. The site may be misconfigured or blocking automated access."),
]

_AUTH_ERROR_KEYWORDS = (
    "401", "403", "unauthorized", "forbidden", "login",
    "session expired", "token expired",
)

_AUTH_DESC_KEYWORDS = (
    "login page", "sign in", "redirected to login",
)

_KNOWN_ERRORS: list[tuple[tuple[str, ...], str, str]] = [
    (("anthropic_api_key",),
     "api_key_missing",
     "Set your ANTHROPIC_API_KEY environment variable."),
    (("rate limit", "429"),
     "rate_limited",
     "API rate limited. Wait 60 seconds and retry, or reduce request frequency."),
    (("insufficient_quota", "billing"),
     "billing",
     "API account has no remaining credits. Check your billing at console.anthropic.com."),
    (("invalid_api_key",),
     "invalid_api_key",
     "API key is invalid. Verify at console.anthropic.com/settings/keys."),
    (("context_length", "too many tokens"),
     "context_length",
     "Conversation too long for the model. Reduce max_steps or enable context trimming."),
    (("overloaded", "529"),
     "overloaded",
     "API server overloaded. This is transient -- retry in 30-60 seconds."),
]


# ---------------------------------------------------------------------------
# RuleAnalyzer
# ---------------------------------------------------------------------------


class RuleAnalyzer:
    """Tier 1: Pattern matching on step data.  Always runs.  Free."""

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        self._config = config or AnalysisConfig()

    def analyze(
        self,
        steps: list[StepData],
        status: str = "failed",
        error: str | None = None,
    ) -> RunAnalysis:
        """Run all Tier 1 rules and return a RunAnalysis."""
        if not self._config.enable_analysis:
            return RunAnalysis(
                findings=[], summary="Analysis disabled", primary_suggestion="",
            )

        try:
            findings: list[AnalysisFinding] = []
            findings.extend(self._check_action_repetition(steps))
            findings.extend(self._check_permission_errors(steps, error))
            findings.extend(self._check_timeout_pattern(steps, error))
            findings.extend(self._check_network_errors(steps, error))
            findings.extend(self._check_auth_failure(steps))
            findings.extend(self._check_visual_stagnation(steps))
            findings.extend(self._check_llm_repetition(steps))
            findings.extend(self._check_error_messages(steps, error))

            findings.sort(key=lambda f: f.confidence, reverse=True)

            wasted_steps, wasted_cost = self._compute_waste(steps, findings)

            cost_findings = self._check_cost_waste(steps, wasted_steps, wasted_cost)
            if cost_findings:
                findings.extend(cost_findings)
                findings.sort(key=lambda f: f.confidence, reverse=True)

            summary = findings[0].summary if findings else "No issues detected"
            primary = findings[0].suggestion if findings else "Task completed successfully"

            return RunAnalysis(
                findings=findings,
                summary=summary,
                primary_suggestion=primary,
                wasted_steps=wasted_steps,
                wasted_cost_cents=wasted_cost,
                tiers_executed=[1],
            )
        except Exception as exc:
            logger.debug("Analysis failed: %s", exc)
            return RunAnalysis(
                findings=[], summary="Analysis error", primary_suggestion="",
            )

    # ------------------------------------------------------------------
    # Rule methods
    # ------------------------------------------------------------------

    def _check_action_repetition(self, steps: list[StepData]) -> list[AnalysisFinding]:
        """Detect 3+ consecutive identical action types."""
        findings: list[AnalysisFinding] = []
        if len(steps) < 3:
            return findings

        run_start = 0
        for i in range(1, len(steps) + 1):
            if i < len(steps) and steps[i].action_type == steps[run_start].action_type:
                continue

            run_len = i - run_start
            if run_len >= 3:
                run = steps[run_start:i]
                action = run[0].action_type
                n = run_len
                first_sn = run[0].step_number
                last_sn = run[-1].step_number

                errors = [s.error for s in run if s.error]
                has_errors = bool(errors)
                screenshots_same = self._screenshots_identical(run)

                if action in _KEYBOARD_ACTIONS:
                    desc = run[0].description or action
                    suggestion = (
                        f"Keyboard shortcut '{desc}' attempted {n} times without effect. "
                        "On macOS, grant Accessibility permissions: System Preferences > "
                        "Privacy & Security > Accessibility. On Linux, check Xdotool "
                        "permissions."
                    )
                elif action in _CLICK_ACTIONS:
                    suggestion = (
                        f"Click action repeated {n} times at same target. The element "
                        "may not be interactive, may be covered by a dialog, or the "
                        "page hasn't finished loading."
                    )
                elif action in _NAVIGATE_ACTIONS:
                    url = run[0].description or "unknown"
                    suggestion = (
                        f"Navigation to '{url}' attempted {n} times. Check if the URL "
                        "is correct and the server is responding."
                    )
                else:
                    suggestion = f"Action '{action}' repeated {n} consecutive times."

                if has_errors:
                    suggestion += f" Each attempt failed with: '{errors[0]}'"
                if screenshots_same:
                    suggestion += (
                        " The screen didn't change between attempts"
                        " -- the action is having no effect."
                    )

                findings.append(AnalysisFinding(
                    tier=1,
                    category="action_repetition",
                    summary=f"'{action}' repeated {n} times (steps {first_sn}-{last_sn})",
                    suggestion=suggestion,
                    confidence=0.9 if has_errors else 0.7,
                    evidence=f"{n} consecutive '{action}' actions",
                    step_range=(first_sn, last_sn),
                ))

            if i < len(steps):
                run_start = i

        return findings

    def _check_permission_errors(
        self, steps: list[StepData], error: str | None = None,
    ) -> list[AnalysisFinding]:
        """Detect permission-related errors with OS-specific suggestions."""
        all_errors = [s.error for s in steps if s.error]
        if error:
            all_errors.append(error)
        if not all_errors:
            return []

        combined = " ".join(all_errors).lower()
        if not any(kw in combined for kw in _PERMISSION_KEYWORDS):
            return []

        is_macos = any(kw in combined for kw in _MACOS_KEYWORDS)
        if is_macos:
            suggestion = (
                "Grant Accessibility permissions: System Preferences > Privacy & "
                "Security > Accessibility > Enable your terminal or Python interpreter. "
                "You may need to restart the application after granting."
            )
        else:
            suggestion = (
                "The automation tool lacks required system permissions. Check OS "
                "security settings for the application running the agent."
            )

        perm_steps = [
            s for s in steps
            if s.error and any(kw in s.error.lower() for kw in _PERMISSION_KEYWORDS)
        ]
        step_range = None
        if perm_steps:
            step_range = (perm_steps[0].step_number, perm_steps[-1].step_number)

        return [AnalysisFinding(
            tier=1,
            category="permission_error",
            summary="Permission error detected",
            suggestion=suggestion,
            confidence=0.95,
            evidence=f"Permission-related errors in {len(perm_steps) or 1} step(s)",
            step_range=step_range,
        )]

    def _check_timeout_pattern(
        self, steps: list[StepData], error: str | None = None,
    ) -> list[AnalysisFinding]:
        """Detect single or multiple timeout errors."""
        timeout_steps = [s for s in steps if s.error and "timeout" in s.error.lower()]
        terminal_timeout = error is not None and "timeout" in error.lower()
        count = len(timeout_steps) + (1 if terminal_timeout and not timeout_steps else 0)

        if count == 0:
            return []

        if count > 1:
            return [AnalysisFinding(
                tier=1,
                category="timeout",
                summary=f"Multiple timeouts ({count} steps)",
                suggestion=(
                    f"Multiple timeouts ({count} steps). The target site may be slow, "
                    "rate-limiting, or the elements don't exist."
                ),
                confidence=0.7,
                evidence=f"{count} steps timed out",
                step_range=(timeout_steps[0].step_number, timeout_steps[-1].step_number)
                if timeout_steps else None,
            )]

        return [AnalysisFinding(
            tier=1,
            category="timeout",
            summary="Timeout detected",
            suggestion=(
                "Page or element timed out. Try increasing timeout, waiting for a "
                "specific element, or checking if the page requires JavaScript."
            ),
            confidence=0.7,
            evidence="Timeout in " + ("step error" if timeout_steps else "terminal error"),
            step_range=(timeout_steps[0].step_number, timeout_steps[0].step_number)
            if timeout_steps else None,
        )]

    def _check_network_errors(
        self, steps: list[StepData], error: str | None = None,
    ) -> list[AnalysisFinding]:
        """Map network error substrings to specific suggestions."""
        all_errors = [s.error for s in steps if s.error]
        if error:
            all_errors.append(error)
        if not all_errors:
            return []

        combined = " ".join(all_errors).lower()
        for keywords, suggestion in _NETWORK_ERROR_MAP:
            if any(kw in combined for kw in keywords):
                return [AnalysisFinding(
                    tier=1,
                    category="network_error",
                    summary=suggestion.split(".")[0],
                    suggestion=suggestion,
                    confidence=0.85,
                    evidence="Network error keyword found in errors",
                )]
        return []

    def _check_auth_failure(self, steps: list[StepData]) -> list[AnalysisFinding]:
        """Detect authentication failures, especially mid-run transitions."""
        if not steps:
            return []

        auth_idx: int | None = None
        for i, s in enumerate(steps):
            err = (s.error or "").lower()
            desc = (s.description or "").lower()
            # Only match error keywords when the step has an error.
            # Only match description keywords on failed steps — a successful
            # "Navigate to login page" is intentional, not an auth failure.
            err_match = s.error is not None and any(
                kw in err for kw in _AUTH_ERROR_KEYWORDS
            )
            desc_match = (not s.success) and any(
                kw in desc for kw in _AUTH_DESC_KEYWORDS
            )
            if err_match or desc_match:
                auth_idx = i
                break

        if auth_idx is None:
            return []

        had_success = any(s.success for s in steps[:auth_idx])
        return [AnalysisFinding(
            tier=1,
            category="auth_failure",
            summary="Authentication failure detected",
            suggestion=(
                "Authentication failed or session expired during execution. "
                "Enable session persistence (session_key= in config) or check credentials."
            ),
            confidence=0.8,
            evidence=(
                f"Auth error at step {steps[auth_idx].step_number}"
                + (" after successful steps" if had_success else " from start")
            ),
            step_range=(steps[auth_idx].step_number, steps[-1].step_number),
        )]

    def _check_visual_stagnation(self, steps: list[StepData]) -> list[AnalysisFinding]:
        """Detect 4+ consecutive identical screenshots via MD5."""
        threshold = 4
        consecutive = 0
        prev_hash: str | None = None
        stag_start: int | None = None

        for step in steps:
            if step.screenshot_bytes is None:
                consecutive = 0
                prev_hash = None
                stag_start = None
                continue

            data = step.screenshot_bytes
            if isinstance(data, str):
                data = data.encode("utf-8")
            h = hashlib.md5(data).hexdigest()

            if h == prev_hash:
                consecutive += 1
            else:
                consecutive = 1
                prev_hash = h
                stag_start = step.step_number

            if consecutive >= threshold:
                stag_steps = [
                    s for s in steps
                    if stag_start is not None
                    and stag_start <= s.step_number <= step.step_number
                ]
                has_errors = any(not s.success for s in stag_steps)

                if has_errors:
                    suggestion = (
                        f"Page displayed an error or dialog for {consecutive} steps "
                        "that the agent couldn't dismiss. Check the screenshots in "
                        "the replay."
                    )
                else:
                    suggestion = (
                        f"Page was static for {consecutive} steps. The agent may be "
                        "waiting for content that requires human interaction (CAPTCHA, "
                        "2FA prompt, cookie consent banner)."
                    )

                return [AnalysisFinding(
                    tier=1,
                    category="visual_stagnation",
                    summary=f"Screen unchanged for {consecutive} steps",
                    suggestion=suggestion,
                    confidence=0.75,
                    evidence=f"{consecutive} consecutive identical screenshots",
                    step_range=(stag_start, step.step_number),
                )]

        return []

    def _check_llm_repetition(self, steps: list[StepData]) -> list[AnalysisFinding]:
        """Detect 3+ consecutive identical LLM responses."""
        threshold = 3
        llm_steps: list[tuple[int, str]] = []
        for s in steps:
            if not isinstance(s.context, dict):
                continue
            if s.context.get("type") != "llm_call":
                continue
            resp = str(s.context.get("response", ""))[:200]
            llm_steps.append((s.step_number, resp))

        if len(llm_steps) < threshold:
            return []

        consecutive = 1
        run_start = 0
        for i in range(1, len(llm_steps)):
            if llm_steps[i][1] == llm_steps[i - 1][1]:
                consecutive += 1
            else:
                consecutive = 1
                run_start = i

            if consecutive >= threshold:
                return [AnalysisFinding(
                    tier=1,
                    category="llm_repetition",
                    summary=f"LLM gave identical response {consecutive} times",
                    suggestion=(
                        f"The LLM gave the same response {consecutive} times despite "
                        "failures. Add a recovery instruction to your prompt like 'If "
                        "your previous approach failed, try a completely different "
                        "method' or increase temperature."
                    ),
                    confidence=0.8,
                    evidence=f"{consecutive} consecutive identical LLM responses",
                    step_range=(llm_steps[run_start][0], llm_steps[i][0]),
                )]

        return []

    def _check_error_messages(
        self, steps: list[StepData], error: str | None = None,
    ) -> list[AnalysisFinding]:
        """Match known error patterns to specific suggestions."""
        all_errors = [s.error for s in steps if s.error]
        if error:
            all_errors.append(error)
        if not all_errors:
            return []

        combined = " ".join(all_errors).lower()
        for keywords, category, suggestion in _KNOWN_ERRORS:
            if any(kw in combined for kw in keywords):
                return [AnalysisFinding(
                    tier=1,
                    category=category,
                    summary=suggestion.split(".")[0],
                    suggestion=suggestion,
                    confidence=0.95,
                    evidence="Known error pattern matched",
                )]
        return []

    def _check_cost_waste(
        self,
        steps: list[StepData],
        wasted_steps: int,
        wasted_cost: float,
    ) -> list[AnalysisFinding]:
        """Produce a finding if >30% of steps were wasted."""
        if not steps or wasted_steps == 0:
            return []
        ratio = wasted_steps / len(steps)
        if ratio <= 0.3:
            return []

        pct = round(ratio * 100)
        start_sn = steps[len(steps) - wasted_steps].step_number

        return [AnalysisFinding(
            tier=1,
            category="cost_waste",
            summary=f"{wasted_steps} wasted steps ({pct}% of run)",
            suggestion=(
                f"{wasted_steps} of {len(steps)} steps ({pct}%) occurred after the "
                f"agent got stuck, wasting ~{wasted_cost:.4f} cents. Enable stuck "
                "detection to abort earlier."
            ),
            confidence=0.6,
            evidence=(
                f"{wasted_steps} steps after progress stopped, "
                f"~{wasted_cost:.4f} cents wasted"
            ),
            step_range=(start_sn, steps[-1].step_number),
        )]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Categories that represent sustained stuck patterns (not transient errors).
    _STUCK_CATEGORIES = frozenset({
        "action_repetition", "visual_stagnation", "llm_repetition",
    })

    def _compute_waste(
        self,
        steps: list[StepData],
        findings: list[AnalysisFinding],
    ) -> tuple[int, float]:
        """Find where progress stopped and calculate wasted steps/cost.

        Only sustained stuck-pattern findings (action_repetition,
        visual_stagnation, llm_repetition) are used as waste anchors.
        Transient single-step errors like timeouts do not indicate the
        agent is stuck.
        """
        if not findings or not steps:
            return 0, 0.0

        stuck_step: int | None = None
        for f in [f for f in findings if f.category in self._STUCK_CATEGORIES]:
            if f.step_range and f.step_range[0] is not None:
                if stuck_step is None or f.step_range[0] < stuck_step:
                    stuck_step = f.step_range[0]

        if stuck_step is None:
            return 0, 0.0

        stuck_idx: int | None = None
        for i, s in enumerate(steps):
            if s.step_number >= stuck_step:
                stuck_idx = i
                break

        if stuck_idx is None:
            return 0, 0.0

        wasted = steps[stuck_idx:]
        wasted_cost = sum(
            (s.tokens_in * 3 / 1_000_000 + s.tokens_out * 15 / 1_000_000) * 100
            for s in wasted
        )
        return len(wasted), round(wasted_cost, 4)

    @staticmethod
    def _screenshots_identical(steps: list[StepData]) -> bool:
        """True if all steps have identical non-None screenshot_bytes."""
        hashes: set[str] = set()
        for s in steps:
            if s.screenshot_bytes is None:
                return False
            data = s.screenshot_bytes
            if isinstance(data, str):
                data = data.encode("utf-8")
            hashes.add(hashlib.md5(data).hexdigest())
        return len(hashes) == 1


# ---------------------------------------------------------------------------
# Helpers (cross-tier)
# ---------------------------------------------------------------------------


_NON_DOMAIN_EXTENSIONS = frozenset({
    "css", "csv", "gif", "gz", "html", "jpg", "js", "json", "log",
    "md", "pdf", "png", "py", "svg", "tar", "ts", "tsx", "txt",
    "xml", "yaml", "yml", "zip",
})


def _find_domain_in_text(text: str) -> str | None:
    """Find a domain name in *text*.

    Tries a schemed URL first (``https://host``), then falls back to a
    bare hostname pattern (``portal.example.com``).  Common file
    extensions are filtered out to reduce false positives.
    """
    # URL with scheme
    m = re.search(r'https?://([^/\s)]+)', text)
    if m:
        host = m.group(1).lower()
        return host[4:] if host.startswith("www.") else host
    # Bare hostname — require at least one dot and a 2+ letter TLD
    m = re.search(
        r'\b((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})\b',
        text,
    )
    if m:
        candidate = m.group(1).lower().rstrip(".")
        tld = candidate.rsplit(".", 1)[-1]
        if tld not in _NON_DOMAIN_EXTENSIONS:
            return candidate[4:] if candidate.startswith("www.") else candidate
    return None


def _extract_domain(steps: list) -> str | None:
    """Extract the primary domain from step descriptions.

    Works with both :class:`StepData` objects and step dicts from JSON.
    """
    for step in steps:
        desc = step.get("description", "") if isinstance(step, dict) else step.description
        domain = _find_domain_in_text(desc)
        if domain:
            return domain
    return None


def _load_run_history(data_dir: str | Path, max_runs: int = 20) -> list[dict]:
    """Load past run metadata from ``{data_dir}/runs/*.json``.

    Returns a list of dicts sorted by file modification time (most recent
    first), capped at *max_runs*.
    """
    runs_path = Path(data_dir) / "runs"
    if not runs_path.exists():
        return []
    runs: list[dict] = []
    for f in sorted(
        runs_path.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            runs.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
        if len(runs) >= max_runs:
            break
    return runs


def _run_status(run: dict) -> str:
    """Normalize run status across both JSON formats."""
    if "status" in run:
        return run["status"]
    return "completed" if run.get("success", False) else "failed"


def _run_created_at(run: dict) -> str:
    """Get creation timestamp from either JSON format."""
    return run.get("created_at", run.get("generated_at", ""))


def _run_step_count(run: dict) -> int:
    """Get step count from either JSON format."""
    return run.get(
        "step_count", run.get("steps_count", len(run.get("steps", []))),
    )


def _run_domain(run: dict) -> str | None:
    """Extract domain from a past run dict (checks url, task, steps)."""
    for field in ("url", "task_description", "task"):
        text = run.get(field, "")
        if text:
            domain = _find_domain_in_text(text)
            if domain:
                return domain
    return _extract_domain(run.get("steps", []))


# ---------------------------------------------------------------------------
# HistoryAnalyzer (Tier 2)
# ---------------------------------------------------------------------------


class HistoryAnalyzer:
    """Tier 2: Compares current run against past runs.  Free.  Needs history."""

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        self._config = config or AnalysisConfig()

    def analyze(
        self,
        steps: list[StepData],
        status: str,
        task_description: str,
        run_history: list[dict],
    ) -> list[AnalysisFinding]:
        """Run all Tier 2 rules and return findings."""
        if not self._config.enable_history or not run_history:
            return []
        try:
            findings: list[AnalysisFinding] = []
            findings.extend(self._check_regression(steps, status, run_history))
            findings.extend(
                self._check_persistent_failure(steps, status, run_history),
            )
            findings.extend(self._check_success_rate_trend(run_history))
            findings.extend(self._check_time_pattern(run_history))
            findings.extend(
                self._check_working_config(steps, status, run_history),
            )
            findings.sort(key=lambda f: f.confidence, reverse=True)
            return findings
        except Exception as exc:
            logger.debug("Tier 2 analysis failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Rule methods
    # ------------------------------------------------------------------

    def _check_regression(
        self,
        steps: list[StepData],
        status: str,
        history: list[dict],
    ) -> list[AnalysisFinding]:
        """Detect regression: task previously succeeded but now fails."""
        if status != "failed":
            return []
        current_domain = _extract_domain(steps)
        if current_domain is None:
            return []
        for run in history:
            if _run_status(run) != "completed":
                continue
            if _run_domain(run) != current_domain:
                continue
            past_steps = _run_step_count(run)
            created = _run_created_at(run)
            return [AnalysisFinding(
                tier=2,
                category="regression",
                summary=(
                    f"Regression: task previously succeeded on {current_domain}"
                ),
                suggestion=(
                    f"This task succeeded previously (last success: {created}) "
                    f"but now fails. Previous run completed in {past_steps} "
                    f"steps; current run failed after {len(steps)} steps. The "
                    "target site or environment may have changed since then."
                ),
                confidence=0.85,
                evidence=f"Past success on {current_domain} at {created}",
            )]
        return []

    def _check_persistent_failure(
        self,
        steps: list[StepData],
        status: str,
        history: list[dict],
    ) -> list[AnalysisFinding]:
        """Detect 3+ consecutive failures on the same domain."""
        if status != "failed":
            return []
        current_domain = _extract_domain(steps)
        if current_domain is None:
            return []
        consecutive = 0
        error_categories: list[str] = []
        for run in history:
            if _run_domain(run) != current_domain:
                continue
            if _run_status(run) == "failed":
                consecutive += 1
                cat = run.get("error_category")
                if cat:
                    error_categories.append(cat)
            else:
                break
        total = consecutive + 1  # +1 for the current failed run
        if total < 3:
            return []
        most_common = (
            Counter(error_categories).most_common(1)[0][0]
            if error_categories
            else "unknown"
        )
        return [AnalysisFinding(
            tier=2,
            category="persistent_failure",
            summary=f"{total} consecutive failures on {current_domain}",
            suggestion=(
                f"This task has failed {total} consecutive times on "
                f"{current_domain}. Most common error: {most_common}. The site "
                "may be blocking automation, or the workflow needs updating."
            ),
            confidence=0.9,
            evidence=(
                f"{total} consecutive failures, top error: {most_common}"
            ),
        )]

    def _check_success_rate_trend(
        self,
        history: list[dict],
    ) -> list[AnalysisFinding]:
        """Detect declining success rate (>20pp drop, newer vs older half)."""
        domain_runs: dict[str, list[dict]] = {}
        for run in history:
            d = _run_domain(run)
            if d:
                domain_runs.setdefault(d, []).append(run)
        for domain, runs in domain_runs.items():
            if len(runs) < 4:
                continue
            mid = len(runs) // 2
            newer = runs[:mid]
            older = runs[mid:]
            newer_rate = (
                sum(1 for r in newer if _run_status(r) == "completed")
                / len(newer)
                * 100
            )
            older_rate = (
                sum(1 for r in older if _run_status(r) == "completed")
                / len(older)
                * 100
            )
            if older_rate - newer_rate > 20:
                return [AnalysisFinding(
                    tier=2,
                    category="success_rate_decline",
                    summary=f"Success rate declining on {domain}",
                    suggestion=(
                        f"Success rate on {domain} dropped from "
                        f"{older_rate:.0f}% to {newer_rate:.0f}%. The site may "
                        "be gradually adding bot detection or changing its "
                        "layout."
                    ),
                    confidence=0.7,
                    evidence=(
                        f"Success rate: {older_rate:.0f}% -> {newer_rate:.0f}%"
                    ),
                )]
        return []

    def _check_time_pattern(
        self,
        history: list[dict],
    ) -> list[AnalysisFinding]:
        """Detect failures clustering in a specific 4-hour window."""
        failed_hours: list[int] = []
        for run in history:
            if _run_status(run) != "failed":
                continue
            ts = _run_created_at(run)
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
                failed_hours.append(dt.hour)
            except (ValueError, TypeError):
                continue
        if len(failed_hours) < 3:
            return []
        for start in range(0, 24, 4):
            end = start + 4
            count = sum(1 for h in failed_hours if start <= h < end)
            if count >= 3 and count / len(failed_hours) > 0.5:
                return [AnalysisFinding(
                    tier=2,
                    category="time_pattern",
                    summary=(
                        f"Failures cluster between "
                        f"{start:02d}:00-{end:02d}:00 UTC"
                    ),
                    suggestion=(
                        f"Failures tend to occur between {start:02d}:00 and "
                        f"{end:02d}:00 UTC. The site may have maintenance "
                        "windows or peak-hour rate limiting during this period."
                    ),
                    confidence=0.6,
                    evidence=(
                        f"{count}/{len(failed_hours)} failures in "
                        f"{start:02d}:00-{end:02d}:00"
                    ),
                )]
        return []

    def _check_working_config(
        self,
        steps: list[StepData],
        status: str,
        history: list[dict],
    ) -> list[AnalysisFinding]:
        """Suggest config from a past successful run with different step count."""
        if status != "failed":
            return []
        current_domain = _extract_domain(steps)
        if current_domain is None or not steps:
            return []
        current_step_count = len(steps)
        for run in history:
            if _run_status(run) != "completed":
                continue
            if _run_domain(run) != current_domain:
                continue
            past_steps = _run_step_count(run)
            diff = abs(past_steps - current_step_count) / current_step_count
            if diff > 0.3:
                created = _run_created_at(run)
                return [AnalysisFinding(
                    tier=2,
                    category="working_config",
                    summary=(
                        f"Past success used {past_steps} steps "
                        f"(vs. current {current_step_count})"
                    ),
                    suggestion=(
                        f"A previous successful run on {created} completed in "
                        f"{past_steps} steps (current run used "
                        f"{current_step_count}). Try matching the previous "
                        "run's approach or settings."
                    ),
                    confidence=0.65,
                    evidence=(
                        f"Past success: {past_steps} steps on {created}"
                    ),
                )]
        return []


# ---------------------------------------------------------------------------
# LLMAnalyzer (Tier 3)
# ---------------------------------------------------------------------------


class LLMAnalyzer:
    """Tier 3: Sends screenshots to LLM for novel failure analysis.

    Opt-in via ``AnalysisConfig.llm_api_key``.  Uses the Anthropic Messages
    API directly via :mod:`urllib` (no ``anthropic`` package dependency).
    Typical cost is ~$0.01 per call with Haiku.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self._api_key = api_key
        self._model = model

    async def analyze(
        self,
        steps: list[StepData],
        status: str,
        error: str | None,
        task_description: str,
        tier1_findings: list[AnalysisFinding],
    ) -> list[AnalysisFinding]:
        """Run Tier 3 LLM analysis on step screenshots and context.

        Returns a list with a single :class:`AnalysisFinding` on success,
        or an empty list if the call fails or returns unparseable output.
        Never raises.
        """
        try:
            screenshots = self._collect_screenshots(steps, max_count=3, max_width=512)
            step_summaries = self._build_step_summaries(steps, max_count=5)
            tier1_text = (
                "\n".join(f"- {f.summary}" for f in tier1_findings)
                or "No patterns detected."
            )

            content: list[dict[str, Any]] = []
            for ss in screenshots:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": ss,
                    },
                })

            content.append({
                "type": "text",
                "text": (
                    "You are analyzing a failed browser/desktop automation task.\n\n"
                    f"Task: {task_description}\n"
                    f"Status: {status}\n"
                    f"Error: {error or 'None'}\n\n"
                    f"Automated analysis found:\n{tier1_text}\n\n"
                    f"Last steps:\n{step_summaries}\n\n"
                    "Based on the screenshots and step history:\n"
                    "1. What is the root cause? Reference what you see in the screenshots.\n"
                    "2. What specific action should the user take to fix this?\n"
                    "3. Is this an environmental issue (permissions, network) "
                    "or a task logic issue?\n\n"
                    "Respond in ONLY this JSON format, no other text:\n"
                    '{"root_cause": "one sentence", "suggestion": "specific actionable '
                    'fix", "confidence": 0.0-1.0, "category": "permission_error|'
                    "site_change|bot_detection|session_expired|network_issue|ui_dialog|"
                    'incorrect_task|environmental|other"}'
                ),
            })

            payload = {
                "model": self._model,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": content}],
            }

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )

            resp = urllib.request.urlopen(req, timeout=30)
            resp_data = json.loads(resp.read().decode("utf-8"))

            text = ""
            for block in resp_data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")

            text = (
                text.strip()
                .removeprefix("```json")
                .removeprefix("```")
                .removesuffix("```")
                .strip()
            )
            result = json.loads(text)

            return [AnalysisFinding(
                tier=3,
                category=result.get("category", "other"),
                summary=result.get("root_cause", "LLM analysis inconclusive"),
                suggestion=result.get(
                    "suggestion", "Review the replay screenshots for clues.",
                ),
                confidence=min(float(result.get("confidence", 0.7)), 1.0),
                evidence="LLM analyzed screenshots and step history",
            )]

        except Exception as exc:
            logger.debug("Tier 3 LLM analysis failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _collect_screenshots(
        self,
        steps: list[StepData],
        max_count: int = 3,
        max_width: int = 512,
    ) -> list[str]:
        """Get last *max_count* screenshots as base64 strings, resized."""
        screenshots: list[str] = []
        for step in reversed(steps):
            if step.screenshot_bytes is not None:
                encoded = self._resize_and_encode(step.screenshot_bytes, max_width)
                if encoded:
                    screenshots.append(encoded)
            if len(screenshots) >= max_count:
                break
        screenshots.reverse()
        return screenshots

    @staticmethod
    def _resize_and_encode(
        screenshot_bytes: bytes | str,
        max_width: int,
    ) -> str | None:
        """Resize screenshot and return base64.

        Falls back to raw base64 if Pillow is not installed.
        """
        if isinstance(screenshot_bytes, str):
            try:
                screenshot_bytes = base64.b64decode(screenshot_bytes)
            except Exception:
                return None

        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(screenshot_bytes))
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize(
                    (max_width, int(img.height * ratio)), Image.LANCZOS,
                )
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except ImportError:
            return base64.b64encode(screenshot_bytes).decode("ascii")
        except Exception:
            return None

    @staticmethod
    def _build_step_summaries(steps: list[StepData], max_count: int = 5) -> str:
        """Format last *max_count* steps as text for the LLM prompt."""
        recent = steps[-max_count:] if len(steps) > max_count else steps
        lines: list[str] = []
        for i, s in enumerate(recent):
            ok = "\u2713" if s.success else "\u2717"
            line = f"  Step {i}: [{ok}] {s.action_type} \u2014 {s.description}"
            if s.error:
                line += f" | Error: {s.error}"
            lines.append(line)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# RunAnalyzer (orchestrator)
# ---------------------------------------------------------------------------


class RunAnalyzer:
    """Orchestrates all three analysis tiers."""

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        self._config = config or AnalysisConfig()

    async def analyze(
        self,
        steps: list[StepData],
        status: str = "failed",
        error: str | None = None,
        task_description: str = "",
        data_dir: str = ".pokant",
    ) -> RunAnalysis:
        """Run Tier 1 (always), Tier 2 (if history), Tier 3 (if LLM key + low confidence)."""
        findings: list[AnalysisFinding] = []
        tiers = [1]

        # Tier 1: Always runs
        tier1_result = RuleAnalyzer(self._config).analyze(steps, status, error)
        tier1_findings = tier1_result.findings
        findings.extend(tier1_findings)

        # Tier 2: If history enabled and data exists
        if self._config.enable_history:
            history = _load_run_history(data_dir, self._config.max_history_runs)
            if history:
                tiers.append(2)
                tier2 = HistoryAnalyzer(self._config).analyze(
                    steps, status, task_description, history,
                )
                findings.extend(tier2)

        # Tier 3: If LLM key provided AND (no high-confidence findings OR always_use_llm)
        if self._config.llm_api_key:
            has_high_confidence = any(f.confidence >= 0.85 for f in findings)
            if not has_high_confidence or self._config.always_use_llm:
                tiers.append(3)
                tier3 = await LLMAnalyzer(
                    api_key=self._config.llm_api_key,
                    model=self._config.llm_model,
                ).analyze(steps, status, error, task_description, tier1_findings)
                findings.extend(tier3)

        # Sort by confidence
        findings.sort(key=lambda f: f.confidence, reverse=True)

        # Compute waste from Tier 1 findings only (stuck categories are Tier 1)
        wasted_steps, wasted_cost = RuleAnalyzer(self._config)._compute_waste(
            steps, tier1_findings,
        )

        # Build summary from top finding
        summary = findings[0].summary if findings else (
            "No issues detected" if status == "completed" else "Analysis inconclusive."
        )
        primary = findings[0].suggestion if findings else "No suggestions."

        return RunAnalysis(
            findings=findings,
            summary=summary,
            primary_suggestion=primary,
            wasted_steps=wasted_steps,
            wasted_cost_cents=wasted_cost,
            tiers_executed=tiers,
        )

    def analyze_sync(
        self,
        steps: list[StepData],
        status: str = "failed",
        error: str | None = None,
        task_description: str = "",
        data_dir: str = ".pokant",
    ) -> RunAnalysis:
        """Synchronous wrapper for analyze().

        If called from within a running event loop, skips Tier 3 (LLM)
        and runs Tier 1 + Tier 2 only.  Otherwise uses asyncio.run().
        """
        import asyncio

        has_loop = True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            has_loop = False

        if not has_loop:
            return asyncio.run(
                self.analyze(steps, status, error, task_description, data_dir),
            )

        # Already in async context — can't nest asyncio.run().
        # Run Tier 1 + 2 synchronously (Tier 3 requires await).
        findings: list[AnalysisFinding] = []
        tiers = [1]

        tier1_result = RuleAnalyzer(self._config).analyze(steps, status, error)
        findings.extend(tier1_result.findings)

        if self._config.enable_history:
            history = _load_run_history(data_dir, self._config.max_history_runs)
            if history:
                tiers.append(2)
                findings.extend(
                    HistoryAnalyzer(self._config).analyze(
                        steps, status, task_description, history,
                    ),
                )

        findings.sort(key=lambda f: f.confidence, reverse=True)
        wasted_steps, wasted_cost = RuleAnalyzer(self._config)._compute_waste(
            steps, tier1_result.findings,
        )
        summary = findings[0].summary if findings else (
            "No issues detected" if status == "completed" else "Analysis inconclusive."
        )
        primary = findings[0].suggestion if findings else "No suggestions."
        return RunAnalysis(
            findings=findings,
            summary=summary,
            primary_suggestion=primary,
            wasted_steps=wasted_steps,
            wasted_cost_cents=wasted_cost,
            tiers_executed=tiers,
        )
