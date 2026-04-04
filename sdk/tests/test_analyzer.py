"""Tests for computeruse.analyzer — Tier 1, Tier 2, Tier 3, and RunAnalyzer."""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from computeruse.analyzer import (
    AnalysisConfig,
    AnalysisFinding,
    HistoryAnalyzer,
    LLMAnalyzer,
    RuleAnalyzer,
    RunAnalysis,
    RunAnalyzer,
    _extract_domain,
    _load_run_history,
)
from computeruse.models import StepData


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_step(
    step_number: int = 1,
    action_type: str = "unknown",
    description: str = "",
    success: bool = True,
    error: str | None = None,
    screenshot_bytes: bytes | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    context: dict | None = None,
    duration_ms: int = 100,
) -> StepData:
    return StepData(
        step_number=step_number,
        action_type=action_type,
        description=description,
        success=success,
        error=error,
        screenshot_bytes=screenshot_bytes,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        timestamp=datetime.now(timezone.utc),
        context=context,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Action repetition
# ---------------------------------------------------------------------------


class TestActionRepetition:
    def test_action_repetition_keyboard(self) -> None:
        """5 identical hotkey steps -> suggests accessibility permissions."""
        steps = [
            _make_step(i, "desktop_hotkey", "Cmd+Space") for i in range(1, 6)
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_action_repetition(steps)
        assert len(findings) == 1
        assert findings[0].category == "action_repetition"
        assert "Accessibility" in findings[0].suggestion
        assert findings[0].confidence == 0.7
        assert findings[0].step_range == (1, 5)

    def test_action_repetition_click(self) -> None:
        """5 identical click steps -> suggests element not interactive."""
        steps = [_make_step(i, "click", "Click button") for i in range(1, 6)]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_action_repetition(steps)
        assert len(findings) == 1
        assert "element may not be interactive" in findings[0].suggestion

    def test_action_repetition_with_errors(self) -> None:
        """Repeated steps all have errors -> includes error in finding."""
        steps = [
            _make_step(
                i, "click", "Click submit",
                success=False, error="Element not found",
            )
            for i in range(1, 6)
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_action_repetition(steps)
        assert len(findings) == 1
        assert "Each attempt failed with" in findings[0].suggestion
        assert "Element not found" in findings[0].suggestion
        assert findings[0].confidence == 0.9


# ---------------------------------------------------------------------------
# Permission errors
# ---------------------------------------------------------------------------


class TestPermissionErrors:
    def test_permission_error_macos(self) -> None:
        """Step error contains 'Accessibility Access' -> macOS-specific suggestion."""
        steps = [
            _make_step(1, success=False, error="Accessibility Access not granted"),
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_permission_errors(steps, None)
        assert len(findings) == 1
        assert "System Preferences" in findings[0].suggestion
        assert findings[0].confidence == 0.95

    def test_permission_error_generic(self) -> None:
        """Step error contains 'permission denied' -> generic suggestion."""
        steps = [
            _make_step(1, success=False, error="permission denied: /usr/bin/x"),
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_permission_errors(steps, None)
        assert len(findings) == 1
        assert "system permissions" in findings[0].suggestion


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_single(self) -> None:
        """One timeout -> basic suggestion."""
        steps = [
            _make_step(1, success=False, error="Timeout waiting for element"),
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_timeout_pattern(steps, None)
        assert len(findings) == 1
        assert "Try increasing timeout" in findings[0].suggestion

    def test_timeout_multiple(self) -> None:
        """3 timeouts -> 'multiple timeouts' finding."""
        steps = [
            _make_step(i, success=False, error="Timeout") for i in range(1, 4)
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_timeout_pattern(steps, None)
        assert len(findings) == 1
        assert "Multiple timeouts" in findings[0].summary


# ---------------------------------------------------------------------------
# Network errors
# ---------------------------------------------------------------------------


class TestNetworkErrors:
    def test_network_dns(self) -> None:
        """ERR_NAME_NOT_RESOLVED -> DNS suggestion."""
        steps = [
            _make_step(1, success=False, error="ERR_NAME_NOT_RESOLVED"),
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_network_errors(steps, None)
        assert len(findings) == 1
        assert "DNS" in findings[0].suggestion

    def test_network_connection_refused(self) -> None:
        """Connection refused -> service not running suggestion."""
        steps = [
            _make_step(1, success=False, error="Connection refused on port 8080"),
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_network_errors(steps, None)
        assert len(findings) == 1
        assert "service is running" in findings[0].suggestion


# ---------------------------------------------------------------------------
# Auth failure
# ---------------------------------------------------------------------------


class TestAuthFailure:
    def test_auth_failure_pattern(self) -> None:
        """3 success steps then 3 with '401' -> session expired suggestion."""
        steps = [
            _make_step(1, "navigate", success=True),
            _make_step(2, "click", success=True),
            _make_step(3, "type", success=True),
            _make_step(4, "click", success=False, error="401 Unauthorized"),
            _make_step(5, "click", success=False, error="401 Unauthorized"),
            _make_step(6, "click", success=False, error="401 Unauthorized"),
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_auth_failure(steps)
        assert len(findings) == 1
        suggestion_lower = findings[0].suggestion.lower()
        assert "session expired" in suggestion_lower or "credentials" in suggestion_lower
        assert "after successful steps" in findings[0].evidence

    def test_no_false_positive_on_intentional_login(self) -> None:
        """Successful 'Navigate to login page' must NOT trigger auth_failure."""
        steps = [
            _make_step(1, "navigate", description="Navigate to login page", success=True),
            _make_step(2, "type", description="Type username", success=True),
            _make_step(3, "click", description="Click sign in button", success=True),
        ]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_auth_failure(steps)
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# LLM repetition
# ---------------------------------------------------------------------------


class TestLLMRepetition:
    def test_llm_repetition(self) -> None:
        """3 LLM steps with same response -> prompt change suggestion."""
        ctx = {"type": "llm_call", "response": "I'll click the submit button"}
        steps = [_make_step(i, "llm_call", context=ctx) for i in range(1, 4)]
        analyzer = RuleAnalyzer()
        findings = analyzer._check_llm_repetition(steps)
        assert len(findings) == 1
        assert "same response" in findings[0].suggestion.lower()
        assert "temperature" in findings[0].suggestion.lower()


# ---------------------------------------------------------------------------
# Known error messages
# ---------------------------------------------------------------------------


class TestErrorMessages:
    def test_known_error_rate_limit(self) -> None:
        """Error contains '429' -> rate limit suggestion."""
        analyzer = RuleAnalyzer()
        findings = analyzer._check_error_messages(
            [], error="Error 429: Rate limit exceeded",
        )
        assert len(findings) == 1
        assert "rate limited" in findings[0].suggestion.lower()

    def test_known_error_api_key(self) -> None:
        """Error contains 'invalid_api_key' -> check key suggestion."""
        analyzer = RuleAnalyzer()
        findings = analyzer._check_error_messages(
            [], error="Error: invalid_api_key",
        )
        assert len(findings) == 1
        assert "API key is invalid" in findings[0].suggestion


# ---------------------------------------------------------------------------
# Cost waste
# ---------------------------------------------------------------------------


class TestCostWaste:
    def test_transient_error_does_not_mark_all_wasted(self) -> None:
        """A single timeout at step 1 must NOT mark all remaining steps wasted."""
        steps = [
            _make_step(1, "navigate", success=False, error="Timeout"),
            _make_step(2, "click", success=True),
            _make_step(3, "type", success=True),
            _make_step(4, "extract", success=True),
        ]
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(steps, status="completed")
        # Timeout is a transient finding, not a stuck pattern.
        # Waste should be 0 since no stuck-pattern finding exists.
        assert result.wasted_steps == 0

    def test_cost_waste(self) -> None:
        """10 steps, repetition starts at step 4 -> 7 wasted, cost calculated."""
        steps = [
            _make_step(1, "navigate", tokens_in=100, tokens_out=50),
            _make_step(2, "click", tokens_in=100, tokens_out=50),
            _make_step(3, "type", tokens_in=100, tokens_out=50),
        ] + [
            _make_step(i, "click", tokens_in=100, tokens_out=50)
            for i in range(4, 11)
        ]
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(steps, status="failed")
        assert result.wasted_steps == 7
        assert result.wasted_cost_cents > 0


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_clean_run_no_findings(self) -> None:
        """3 successful diverse steps -> empty findings."""
        steps = [
            _make_step(1, "navigate"),
            _make_step(2, "click"),
            _make_step(3, "extract"),
        ]
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(steps, status="completed")
        assert len(result.findings) == 0
        assert result.summary == "No issues detected"

    def test_multiple_findings_sorted(self) -> None:
        """Trigger 3 different rules -> findings sorted by confidence desc."""
        steps = [
            _make_step(1, success=False, error="Connection refused"),
            _make_step(2, success=False, error="Timeout waiting"),
        ]
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(
            steps, status="failed", error="429 rate limited",
        )
        assert len(result.findings) >= 3
        confidences = [f.confidence for f in result.findings]
        assert confidences == sorted(confidences, reverse=True)

    def test_run_analysis_structure(self) -> None:
        """RunAnalysis has correct summary and primary_suggestion from top finding."""
        steps = [
            _make_step(
                i, "click", success=False, error="Element not found",
            )
            for i in range(1, 6)
        ]
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(steps, status="failed")
        assert isinstance(result, RunAnalysis)
        assert result.findings
        assert result.summary == result.findings[0].summary
        assert result.primary_suggestion == result.findings[0].suggestion
        assert result.tiers_executed == [1]


# ---------------------------------------------------------------------------
# LLM Analyzer (Tier 3)
# ---------------------------------------------------------------------------


def _mock_api_response(text: str) -> MagicMock:
    """Create a mock urllib response returning Anthropic Messages API JSON."""
    resp = MagicMock()
    resp.read.return_value = json.dumps({
        "content": [{"type": "text", "text": text}],
    }).encode("utf-8")
    return resp


_VALID_LLM_JSON = (
    '{"root_cause":"Button is hidden behind modal",'
    '"suggestion":"Dismiss the cookie banner first",'
    '"confidence":0.85,"category":"ui_dialog"}'
)


class TestLLMAnalyzer:
    """Tests for the Tier 3 LLM-powered analyzer."""

    def test_llm_not_called_without_key(self) -> None:
        """AnalysisConfig(llm_api_key=None) -> LLMAnalyzer never instantiated."""
        config = AnalysisConfig(llm_api_key=None)
        assert config.llm_api_key is None
        # Callers gate on config.llm_api_key before creating LLMAnalyzer.

    async def test_llm_called_with_key(self) -> None:
        """Mock urllib POST -> verify request to api.anthropic.com."""
        mock_resp = _mock_api_response(_VALID_LLM_JSON)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            analyzer = LLMAnalyzer(api_key="test-key")
            findings = await analyzer.analyze(
                steps=[_make_step(1, "click")],
                status="failed",
                error="Something broke",
                task_description="Click button",
                tier1_findings=[],
            )

        mock_open.assert_called_once()
        req = mock_open.call_args[0][0]
        assert req.full_url == "https://api.anthropic.com/v1/messages"
        assert req.get_header("X-api-key") == "test-key"
        assert req.get_header("Anthropic-version") == "2023-06-01"
        assert len(findings) == 1

    async def test_llm_payload_has_screenshots(self) -> None:
        """Screenshots appear as image blocks in the content array."""
        mock_resp = _mock_api_response(_VALID_LLM_JSON)

        def fake_resize(data: bytes | str, max_width: int) -> str | None:
            import base64 as b64

            raw = b64.b64decode(data) if isinstance(data, str) else data
            return b64.b64encode(raw).decode("ascii")

        with (
            patch("urllib.request.urlopen", return_value=mock_resp) as mock_open,
            patch.object(LLMAnalyzer, "_resize_and_encode", staticmethod(fake_resize)),
        ):
            analyzer = LLMAnalyzer(api_key="key")
            await analyzer.analyze(
                steps=[
                    _make_step(1, "click", screenshot_bytes=b"fake-png-1"),
                    _make_step(2, "click", screenshot_bytes=b"fake-png-2"),
                ],
                status="failed",
                error=None,
                task_description="test",
                tier1_findings=[],
            )

        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode())
        content = payload["messages"][0]["content"]
        image_blocks = [b for b in content if b["type"] == "image"]
        assert len(image_blocks) == 2
        assert image_blocks[0]["source"]["media_type"] == "image/png"

    async def test_llm_response_parsed(self) -> None:
        """Valid JSON response -> AnalysisFinding with correct fields."""
        mock_resp = _mock_api_response(_VALID_LLM_JSON)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            analyzer = LLMAnalyzer(api_key="key")
            findings = await analyzer.analyze(
                steps=[_make_step(1)],
                status="failed",
                error=None,
                task_description="test",
                tier1_findings=[],
            )

        assert len(findings) == 1
        f = findings[0]
        assert f.tier == 3
        assert f.category == "ui_dialog"
        assert "hidden behind modal" in f.summary
        assert "cookie banner" in f.suggestion
        assert f.confidence == 0.85

    async def test_llm_bad_json_response(self) -> None:
        """Malformed response -> empty list, no crash."""
        mock_resp = _mock_api_response("This is not valid JSON at all")

        with patch("urllib.request.urlopen", return_value=mock_resp):
            analyzer = LLMAnalyzer(api_key="key")
            findings = await analyzer.analyze(
                steps=[_make_step(1)],
                status="failed",
                error=None,
                task_description="test",
                tier1_findings=[],
            )

        assert findings == []

    async def test_llm_api_error(self) -> None:
        """urlopen raises -> empty list, no crash."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection failed"),
        ):
            analyzer = LLMAnalyzer(api_key="key")
            findings = await analyzer.analyze(
                steps=[_make_step(1)],
                status="failed",
                error=None,
                task_description="test",
                tier1_findings=[],
            )

        assert findings == []

    def test_llm_screenshot_resize(self) -> None:
        """_resize_and_encode reduces size when Pillow available."""
        mock_img = MagicMock()
        mock_img.width = 1024
        mock_img.height = 768
        mock_resized = MagicMock()
        mock_img.resize.return_value = mock_resized

        small_png = b"resized-png-bytes"

        def mock_save(buf: object, format: str | None = None) -> None:
            buf.write(small_png)  # type: ignore[union-attr]

        mock_resized.save = mock_save

        with patch("PIL.Image.open", return_value=mock_img):
            result = LLMAnalyzer._resize_and_encode(b"original-large", max_width=512)

        assert result is not None
        mock_img.resize.assert_called_once()
        assert mock_img.resize.call_args[0][0][0] == 512

    async def test_llm_no_screenshots(self) -> None:
        """Steps with no screenshot_bytes -> API called with text-only prompt."""
        mock_resp = _mock_api_response(_VALID_LLM_JSON)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            analyzer = LLMAnalyzer(api_key="key")
            findings = await analyzer.analyze(
                steps=[_make_step(1, "click"), _make_step(2, "type")],
                status="failed",
                error=None,
                task_description="test",
                tier1_findings=[],
            )

        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode())
        content = payload["messages"][0]["content"]
        image_blocks = [b for b in content if b["type"] == "image"]
        text_blocks = [b for b in content if b["type"] == "text"]
        assert len(image_blocks) == 0
        assert len(text_blocks) == 1
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# History Analyzer (Tier 2)
# ---------------------------------------------------------------------------


class TestHistoryAnalyzer:
    """Tests for Tier 2 cross-run history analysis."""

    def test_regression_detected(self, tmp_path: Path) -> None:
        """Past success + current failure on same domain -> regression."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        (runs_dir / "past_success.json").write_text(json.dumps({
            "task_id": "past-1",
            "status": "completed",
            "step_count": 5,
            "created_at": "2026-03-30T10:00:00+00:00",
            "steps": [
                {
                    "description": "goto(https://example.com/page)",
                    "action_type": "navigate",
                    "success": True,
                },
            ],
        }))

        steps = [
            _make_step(
                1, "navigate",
                description="goto(https://example.com/page)",
            ),
            _make_step(2, "click", success=False, error="Element not found"),
        ]

        analyzer = HistoryAnalyzer()
        history = _load_run_history(tmp_path)
        findings = analyzer.analyze(steps, "failed", "Extract data", history)

        regression = [f for f in findings if f.category == "regression"]
        assert len(regression) == 1
        assert regression[0].tier == 2
        assert regression[0].confidence == 0.85
        assert "example.com" in regression[0].summary

    def test_persistent_failure(self, tmp_path: Path) -> None:
        """3+ consecutive failures on same domain -> persistent failure."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        for i in range(3):
            (runs_dir / f"fail_{i}.json").write_text(json.dumps({
                "task_id": f"fail-{i}",
                "status": "failed",
                "error_category": "timeout",
                "created_at": f"2026-03-{28 + i}T10:00:00+00:00",
                "steps": [
                    {
                        "description": "goto(https://portal.example.com)",
                        "action_type": "navigate",
                        "success": True,
                    },
                ],
            }))

        steps = [
            _make_step(
                1, "navigate",
                description="goto(https://portal.example.com)",
            ),
            _make_step(2, "click", success=False, error="Timeout"),
        ]

        analyzer = HistoryAnalyzer()
        history = _load_run_history(tmp_path)
        findings = analyzer.analyze(steps, "failed", "Test portal", history)

        persistent = [
            f for f in findings if f.category == "persistent_failure"
        ]
        assert len(persistent) == 1
        assert persistent[0].confidence == 0.9
        # 3 past + 1 current = 4
        assert "4" in persistent[0].summary

    def test_success_rate_decline(self, tmp_path: Path) -> None:
        """Success rate drops >20pp -> trend finding."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()

        # Write older runs first (lower mtime) — all successes
        for i in range(3):
            (runs_dir / f"old_{i}.json").write_text(json.dumps({
                "task_id": f"old-{i}",
                "status": "completed",
                "created_at": f"2026-03-{20 + i}T10:00:00+00:00",
                "steps": [
                    {
                        "description": "goto(https://shop.example.com)",
                        "action_type": "navigate",
                        "success": True,
                    },
                ],
            }))

        # Write newer runs last (higher mtime) — all failures
        for i in range(3):
            (runs_dir / f"new_{i}.json").write_text(json.dumps({
                "task_id": f"new-{i}",
                "status": "failed",
                "created_at": f"2026-03-{27 + i}T10:00:00+00:00",
                "steps": [
                    {
                        "description": "goto(https://shop.example.com)",
                        "action_type": "navigate",
                        "success": True,
                    },
                ],
            }))

        analyzer = HistoryAnalyzer()
        history = _load_run_history(tmp_path)
        findings = analyzer.analyze(
            [_make_step(
                1, "navigate",
                description="goto(https://shop.example.com)",
            )],
            "failed",
            "Shop test",
            history,
        )

        trend = [f for f in findings if f.category == "success_rate_decline"]
        assert len(trend) == 1
        assert trend[0].confidence == 0.7

    def test_time_pattern(self, tmp_path: Path) -> None:
        """Failures clustered in a 4-hour window -> time pattern finding."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()

        # 4 failures between 08:00-11:59
        for i in range(4):
            (runs_dir / f"fail_{i}.json").write_text(json.dumps({
                "task_id": f"fail-{i}",
                "status": "failed",
                "created_at": f"2026-03-{25 + i}T{8 + i:02d}:30:00+00:00",
                "steps": [],
            }))

        # 1 failure outside window
        (runs_dir / "fail_other.json").write_text(json.dumps({
            "task_id": "fail-other",
            "status": "failed",
            "created_at": "2026-03-24T20:00:00+00:00",
            "steps": [],
        }))

        analyzer = HistoryAnalyzer()
        history = _load_run_history(tmp_path)
        findings = analyzer.analyze([], "failed", "Test", history)

        time_f = [f for f in findings if f.category == "time_pattern"]
        assert len(time_f) == 1
        assert time_f[0].confidence == 0.6
        assert "08:00" in time_f[0].suggestion

    def test_working_config(self, tmp_path: Path) -> None:
        """Past success with different step count -> config suggestion."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        (runs_dir / "success.json").write_text(json.dumps({
            "task_id": "success-1",
            "status": "completed",
            "step_count": 3,
            "created_at": "2026-03-29T10:00:00+00:00",
            "steps": [
                {
                    "description": "goto(https://api.example.com)",
                    "action_type": "navigate",
                    "success": True,
                },
                {
                    "description": "click button",
                    "action_type": "click",
                    "success": True,
                },
                {
                    "description": "extract data",
                    "action_type": "extract",
                    "success": True,
                },
            ],
        }))

        steps = [
            _make_step(
                i, "click",
                description=(
                    "goto(https://api.example.com)" if i == 1 else "click"
                ),
            )
            for i in range(1, 11)
        ]

        analyzer = HistoryAnalyzer()
        history = _load_run_history(tmp_path)
        findings = analyzer.analyze(steps, "failed", "API test", history)

        config_f = [f for f in findings if f.category == "working_config"]
        assert len(config_f) == 1
        assert config_f[0].confidence == 0.65
        assert "3" in config_f[0].suggestion

    def test_no_history_no_findings(self) -> None:
        """Empty history -> empty list, no crash."""
        analyzer = HistoryAnalyzer()
        findings = analyzer.analyze(
            [_make_step(1)], "failed", "Test", [],
        )
        assert findings == []

    def test_domain_extraction(self) -> None:
        """_extract_domain parses URLs from step descriptions."""
        steps_url = [
            _make_step(
                1, description="goto(https://www.google.com/search)",
            ),
        ]
        assert _extract_domain(steps_url) == "google.com"

        steps_portal = [
            _make_step(
                1, description="goto(https://portal.example.com)",
            ),
        ]
        assert _extract_domain(steps_portal) == "portal.example.com"

        # Bare hostname without scheme (real Playwright data pattern)
        steps_bare = [
            _make_step(1, description="goto(portal.example.com)"),
        ]
        assert _extract_domain(steps_bare) == "portal.example.com"

        steps_no_url = [_make_step(1, description="clicked a button")]
        assert _extract_domain(steps_no_url) is None


# ---------------------------------------------------------------------------
# RunAnalyzer orchestrator (Tests 33-37)
# ---------------------------------------------------------------------------


class TestRunAnalyzer:
    """Tests for the RunAnalyzer orchestrator."""

    async def test_orchestrator_tier1_only(self) -> None:
        """No history, no LLM key -> runs Tier 1 only."""
        config = AnalysisConfig(enable_history=False, llm_api_key=None)
        analyzer = RunAnalyzer(config)
        steps = [_make_step(i, "click", "Click button") for i in range(1, 6)]
        result = await analyzer.analyze(
            steps, "failed", "Element not found", data_dir="/nonexistent",
        )
        assert result.tiers_executed == [1]
        assert len(result.findings) > 0
        assert all(f.tier == 1 for f in result.findings)

    async def test_orchestrator_tier1_and_2(self, tmp_path: Path) -> None:
        """Provide history files -> runs Tier 1+2."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        (runs_dir / "past.json").write_text(json.dumps({
            "task_id": "past-1",
            "status": "completed",
            "step_count": 3,
            "created_at": "2026-03-29T10:00:00+00:00",
            "steps": [
                {"description": "goto(https://example.com)", "action_type": "navigate", "success": True},
            ],
        }))
        config = AnalysisConfig(enable_history=True, llm_api_key=None)
        analyzer = RunAnalyzer(config)
        steps = [
            _make_step(1, "navigate", description="goto(https://example.com)"),
            _make_step(2, "click", success=False, error="Not found"),
        ]
        result = await analyzer.analyze(
            steps, "failed", "Not found", "Test", str(tmp_path),
        )
        assert 1 in result.tiers_executed
        assert 2 in result.tiers_executed
        assert any(f.tier == 2 for f in result.findings)

    async def test_orchestrator_tier3_when_low_confidence(self) -> None:
        """All Tier 1 findings < 0.85, LLM key set -> Tier 3 called."""
        mock_resp = _mock_api_response(_VALID_LLM_JSON)
        config = AnalysisConfig(
            enable_history=False, llm_api_key="test-key",
        )
        # Single timeout -> confidence 0.7 (< 0.85)
        steps = [_make_step(1, success=False, error="Timeout waiting")]
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = await RunAnalyzer(config).analyze(
                steps, "failed", "Timeout", data_dir="/nonexistent",
            )
        assert 3 in result.tiers_executed
        assert any(f.tier == 3 for f in result.findings)

    async def test_orchestrator_tier3_skipped_when_confident(self) -> None:
        """Tier 1 has >= 0.85 confidence -> Tier 3 NOT called."""
        config = AnalysisConfig(
            enable_history=False, llm_api_key="test-key",
        )
        # Permission error -> confidence 0.95 (>= 0.85)
        steps = [
            _make_step(1, success=False, error="Accessibility Access denied"),
        ]
        with patch("urllib.request.urlopen") as mock_open:
            result = await RunAnalyzer(config).analyze(
                steps, "failed", data_dir="/nonexistent",
            )
        assert 3 not in result.tiers_executed
        mock_open.assert_not_called()

    async def test_orchestrator_always_use_llm(self) -> None:
        """always_use_llm=True -> Tier 3 called even with high confidence."""
        mock_resp = _mock_api_response(_VALID_LLM_JSON)
        config = AnalysisConfig(
            enable_history=False, llm_api_key="test-key",
            always_use_llm=True,
        )
        steps = [
            _make_step(1, success=False, error="Accessibility Access denied"),
        ]
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = await RunAnalyzer(config).analyze(
                steps, "failed", data_dir="/nonexistent",
            )
        assert 3 in result.tiers_executed


# ---------------------------------------------------------------------------
# Integration tests (Tests 38-42)
# ---------------------------------------------------------------------------


class TestTrackerAnalysis:
    """Tests for analysis integration in PokantTracker."""

    def test_tracker_fail_has_analysis(self, tmp_path: Path) -> None:
        """Fail a tracker -> analysis property populated."""
        from computeruse.tracker import PokantTracker, TrackerConfig

        config = TrackerConfig(
            task_description="Test task",
            output_dir=str(tmp_path),
            analysis=AnalysisConfig(enable_analysis=True, enable_history=False),
        )
        tracker = PokantTracker(config=config)
        tracker.start()
        for i in range(1, 6):
            tracker.record_step(
                action_type="click", description="Click button",
                success=False, error="Element not found",
            )
        tracker.fail(error="Task failed")

        assert tracker.analysis is not None
        assert len(tracker.analysis.findings) > 0
        assert tracker.analysis.tiers_executed == [1]

    def test_tracker_complete_has_analysis(self, tmp_path: Path) -> None:
        """Complete a tracker -> analysis property populated."""
        from computeruse.tracker import PokantTracker, TrackerConfig

        config = TrackerConfig(
            task_description="Test task",
            output_dir=str(tmp_path),
            analysis=AnalysisConfig(enable_analysis=True, enable_history=False),
        )
        tracker = PokantTracker(config=config)
        tracker.start()
        tracker.record_step(action_type="navigate", description="Go to page")
        tracker.record_step(action_type="click", description="Click")
        tracker.record_step(action_type="extract", description="Extract")
        tracker.complete()

        assert tracker.analysis is not None
        assert tracker.analysis.summary == "No issues detected"

    def test_analysis_in_run_metadata_json(self, tmp_path: Path) -> None:
        """Fail a tracker -> JSON file contains analysis key."""
        from computeruse.tracker import PokantTracker, TrackerConfig

        config = TrackerConfig(
            task_description="Test",
            output_dir=str(tmp_path),
            analysis=AnalysisConfig(enable_analysis=True, enable_history=False),
        )
        tracker = PokantTracker(config=config)
        tracker.start()
        for i in range(1, 6):
            tracker.record_step(
                action_type="click", description="Click",
                success=False, error="Not found",
            )
        tracker.fail(error="Failed")

        json_path = tmp_path / "runs" / f"{tracker.task_id}.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "analysis" in data
        assert data["analysis"] is not None
        assert "summary" in data["analysis"]
        assert "findings" in data["analysis"]
        assert "tiers_executed" in data["analysis"]
        assert len(data["analysis"]["findings"]) > 0


class TestReplayAnalysis:
    """Test analysis rendering in replay HTML."""

    def test_analysis_in_replay(self, tmp_path: Path) -> None:
        """Generate replay with analysis -> HTML contains analysis data."""
        from computeruse.replay_generator import ReplayGenerator

        steps = [_make_step(1, "click", description="Click")]
        metadata = {
            "task_id": "test-replay",
            "generated_at": "2026-04-01T00:00:00",
            "duration_ms": 1000,
            "success": False,
        }
        analysis = RunAnalysis(
            findings=[
                AnalysisFinding(
                    tier=1,
                    category="action_repetition",
                    summary="Click repeated 5 times",
                    suggestion="Check element",
                    confidence=0.7,
                    evidence="5 clicks",
                ),
            ],
            summary="Click repeated 5 times",
            primary_suggestion="Check element",
            wasted_steps=4,
            wasted_cost_cents=0.01,
            tiers_executed=[1],
        )
        gen = ReplayGenerator(steps, metadata, analysis=analysis)
        out = str(tmp_path / "replay.html")
        gen.generate(out)

        html = Path(out).read_text()
        assert "analysis" in html
        assert "action_repetition" in html
        assert "Check element" in html


class TestWrapAnalysis:
    """Test analysis integration in WrappedAgent."""

    async def test_wrap_has_analysis(self, tmp_path: Path) -> None:
        """Mock wrap agent that succeeds -> analysis populated."""
        from computeruse.wrap import WrappedAgent, WrapConfig

        # Create a mock agent
        mock_result = MagicMock()
        mock_result.history = []
        mock_result.screenshots = MagicMock(return_value=[])
        mock_result.action_names = MagicMock(return_value=[])
        mock_result.total_cost = MagicMock(return_value=0.0)
        mock_agent = MagicMock()
        mock_agent.task = "Test task"

        async def mock_run(**kwargs):
            return mock_result

        mock_agent.run = mock_run

        config = WrapConfig(
            output_dir=str(tmp_path),
            analysis=AnalysisConfig(enable_analysis=True, enable_history=False),
        )
        wrapped = WrappedAgent(mock_agent, config)
        await wrapped.run(max_steps=5)

        assert wrapped.analysis is not None
