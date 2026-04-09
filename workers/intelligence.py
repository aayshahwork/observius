"""
workers/intelligence.py — Sync-friendly wrappers around SDK intelligence modules.

All SDK imports are gated with try/except ImportError so that workers degrade
gracefully when the SDK is unavailable (e.g., during local dev without the SDK
installed, or if a Docker build step is skipped).

Public API:
    run_failure_analysis()   — wraps FailureAnalyzer.analyze() (async → sync)
    plan_recovery()          — wraps RecoveryRouter.plan_recovery() (sync)
    build_analysis_json()    — assembles RunAnalysis-compatible dict
    enrich_step_context()    — wraps infer_intent_from_step() (pure Python)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_failure_analysis(
    task_description: str,
    steps: list[Any],
    error: str,
    error_category: str = "",
    api_key: str | None = None,
    max_steps: int | None = None,
) -> dict | None:
    """Analyze a failed run and return a FailureDiagnosis dict.

    Runs FailureAnalyzer.analyze() in a fresh asyncio event loop.  This is
    safe in Celery prefork workers: the main execution loop (asyncio.run in
    execute_task) has already returned and closed before this is called.

    Returns None on any error (import failure, SDK error, timeout, etc.).
    """
    try:
        from computeruse.failure_analyzer import FailureAnalyzer
    except ImportError:
        logger.debug("SDK not available — skipping failure analysis")
        return None

    try:
        analyzer = FailureAnalyzer(
            api_key=api_key,
            enable_llm=bool(api_key),
        )
        diagnosis = asyncio.run(
            analyzer.analyze(
                task_description=task_description,
                steps=steps,
                error=error,
                error_category=error_category,
                max_steps=max_steps,
            )
        )
        return diagnosis.to_dict()
    except Exception as exc:
        logger.debug("Failure analysis raised: %s", exc)
        return None


def plan_recovery(
    original_task: str,
    diagnosis_dict: dict,
    attempt_number: int,
    max_attempts: int,
    memory_list: list[dict] | None = None,
) -> dict | None:
    """Map a diagnosis dict to a RecoveryPlan dict.

    Reconstructs SDK objects from JSON-serializable dicts so the function can
    be called with data carried in config_dict across Celery task boundaries.

    Returns RecoveryPlan.to_dict() or None on error.
    """
    try:
        from computeruse.failure_analyzer import FailureCategory, FailureDiagnosis
        from computeruse.recovery_router import RecoveryRouter
        from computeruse.retry_memory import AttemptRecord, RetryMemory
    except ImportError:
        logger.debug("SDK not available — skipping recovery planning")
        return None

    try:
        # Reconstruct FailureDiagnosis from dict
        raw_cat = diagnosis_dict.get("category", "unknown")
        try:
            category = FailureCategory(raw_cat)
        except ValueError:
            category = FailureCategory.UNKNOWN

        diagnosis = FailureDiagnosis(
            category=category,
            subcategory=str(diagnosis_dict.get("subcategory", "")),
            is_retryable=bool(diagnosis_dict.get("is_retryable", True)),
            root_cause=str(diagnosis_dict.get("root_cause", "")),
            progress_achieved=str(diagnosis_dict.get("progress_achieved", "")),
            retry_hint=str(diagnosis_dict.get("retry_hint", "")),
            should_change_approach=bool(diagnosis_dict.get("should_change_approach", False)),
            wait_seconds=int(diagnosis_dict.get("wait_seconds", 0)),
            environment_changes=list(diagnosis_dict.get("environment_changes", [])),
            confidence=float(diagnosis_dict.get("confidence", 0.5)),
            analysis_cost_cents=float(diagnosis_dict.get("analysis_cost_cents", 0.0)),
            analysis_method=str(diagnosis_dict.get("analysis_method", "rule_based")),
        )

        # Reconstruct RetryMemory from serialized list
        memory = RetryMemory(max_entries=3)
        for rd in (memory_list or []):
            try:
                memory.record(AttemptRecord(**rd))
            except Exception:
                pass

        router = RecoveryRouter()
        recovery_plan = router.plan_recovery(
            original_task=original_task,
            diagnosis=diagnosis,
            attempt_number=attempt_number,
            max_attempts=max_attempts,
            memory=memory,
        )
        return recovery_plan.to_dict()
    except Exception as exc:
        logger.debug("Recovery planning raised: %s", exc)
        return None


def build_analysis_json(
    diagnosis_dict: dict | None,
    recovery_dict: dict | None = None,
    step_data: list[Any] | None = None,
    attempt_number: int = 1,
    retry_memory_list: list[dict] | None = None,
) -> dict:
    """Build a RunAnalysis-compatible dict for storage in task.analysis_json.

    Output shape matches the RunAnalysis TypeScript interface exactly:
        summary, primary_suggestion, findings[], wasted_steps,
        wasted_cost_cents, tiers_executed, total_attempts,
        adaptive_retry_used, attempts? (when retry_memory_list present)

    AnalysisFinding shape: {tier, category, summary, suggestion, confidence}
    RetryAttempt shape: {attempt, status, diagnosis, recovery_plan}
    AttemptDiagnosis shape: {category, subcategory, root_cause, retry_hint,
                             analysis_cost_cents, analysis_method,
                             confidence, is_retryable}
    """
    if not diagnosis_dict:
        return {
            "summary": "",
            "primary_suggestion": "",
            "findings": [],
            "wasted_steps": 0,
            "wasted_cost_cents": 0.0,
            "tiers_executed": [],
            "total_attempts": attempt_number,
            "adaptive_retry_used": False,
        }

    category = diagnosis_dict.get("category", "unknown")
    root_cause = str(diagnosis_dict.get("root_cause", ""))
    retry_hint = str(diagnosis_dict.get("retry_hint", ""))
    confidence = float(diagnosis_dict.get("confidence", 0.5))
    analysis_cost = float(diagnosis_dict.get("analysis_cost_cents", 0.0))
    analysis_method = str(diagnosis_dict.get("analysis_method", "rule_based"))

    # Tier numbering: 1 = rule_based, 2 = history, 3 = llm_haiku
    tier = 3 if analysis_method == "llm_haiku" else 1

    # Count failed steps from StepData objects
    wasted_steps = 0
    if step_data:
        try:
            wasted_steps = sum(
                1 for s in step_data if not getattr(s, "success", True)
            )
        except Exception:
            pass

    findings = [
        {
            "tier": tier,
            "category": category,
            "summary": root_cause[:200],
            "suggestion": retry_hint[:500],
            "confidence": confidence,
        }
    ]

    result: dict = {
        "summary": root_cause[:200],
        "primary_suggestion": retry_hint[:500],
        "findings": findings,
        "wasted_steps": wasted_steps,
        "wasted_cost_cents": analysis_cost,
        "tiers_executed": [tier],
        "total_attempts": attempt_number,
        "adaptive_retry_used": bool(retry_memory_list),
    }

    # Include prior attempts from retry_memory (inline AttemptDiagnosis shape)
    if retry_memory_list:
        attempts = []
        for record in retry_memory_list:
            attempts.append({
                "attempt": record.get("attempt_number", 0),
                "status": "failed",
                "diagnosis": {
                    "category": record.get("category", "unknown"),
                    "subcategory": "",
                    "root_cause": record.get("root_cause", ""),
                    "retry_hint": record.get("retry_hint", ""),
                    "analysis_cost_cents": float(record.get("cost_cents", 0.0)),
                    "analysis_method": record.get("analysis_method", "rule_based"),
                    "confidence": 0.8,
                    "is_retryable": True,
                },
                "recovery_plan": None,
            })
        result["attempts"] = attempts

    return result


def enrich_step_context(action_type: str, description: str = "") -> dict | None:
    """Return an enrichment context dict for a step.

    Calls infer_intent_from_step() from the SDK — pure Python, no async,
    no page access required.  Returns None on import failure or any error.
    """
    try:
        from computeruse.step_enrichment import infer_intent_from_step
    except ImportError:
        return None

    try:
        element_meta = {"text": description or ""}
        intent, intent_detail = infer_intent_from_step(action_type, element_meta)
        return {
            "type": "enrichment",
            "intent": intent,
            "intent_detail": intent_detail,
            "action_type": action_type,
        }
    except Exception as exc:
        logger.debug("Step enrichment raised: %s", exc)
        return None
