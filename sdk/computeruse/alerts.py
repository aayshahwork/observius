"""
computeruse/alerts.py -- SDK-side alerting for browser automation runs.

Provides AlertConfig (frozen dataclass) and AlertEmitter that fire Python
callbacks and/or webhook POSTs on failure, stuck detection, and cost
threshold events.  All methods are synchronous and never raise.

Usage::

    from computeruse import wrap, WrapConfig, AlertConfig

    config = WrapConfig(
        alerts=AlertConfig(
            on_failure=lambda tid, err, cat: print(f"FAIL: {tid} {cat}"),
            on_stuck=lambda tid, reason: print(f"STUCK: {tid} {reason}"),
            on_cost_exceeded=lambda tid, cost: print(f"COST: {tid} ${cost/100:.2f}"),
            cost_threshold_cents=50.0,
            webhook_url="https://hooks.example.com/pokant",
        ),
    )
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("pokant")


@dataclass(frozen=True)
class AlertConfig:
    """Configuration for SDK-side alerts.

    All fields are optional.  When no callbacks or webhook URL are set,
    :class:`AlertEmitter` methods are no-ops.
    """

    # Callback alerts (Python functions)
    on_failure: Optional[Callable[[str, str, Optional[str]], None]] = None
    on_stuck: Optional[Callable[[str, str], None]] = None
    on_cost_exceeded: Optional[Callable[[str, float], None]] = None

    # Threshold alerts
    cost_threshold_cents: Optional[float] = None

    # Webhook alerts (POST JSON to a URL)
    webhook_url: Optional[str] = None
    webhook_headers: Optional[Dict[str, str]] = None


class AlertEmitter:
    """Fires alert callbacks and/or webhook POSTs.

    All public methods are safe to call unconditionally -- they silently
    skip when no handler is configured and never propagate exceptions
    from user callbacks or webhook delivery.
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._cost_alerted: bool = False

    def emit_failure(
        self,
        task_id: str,
        error: str,
        category: Optional[str] = None,
    ) -> None:
        """Emit a failure alert via callback and/or webhook."""
        if self._config.on_failure:
            try:
                self._config.on_failure(task_id, error, category)
            except Exception as exc:
                logger.warning("on_failure callback error: %s", exc)

        if self._config.webhook_url:
            self._post_webhook("failure", {
                "task_id": task_id,
                "error": error,
                "error_category": category,
            })

    def emit_stuck(self, task_id: str, reason: str) -> None:
        """Emit a stuck-detection alert via callback and/or webhook."""
        if self._config.on_stuck:
            try:
                self._config.on_stuck(task_id, reason)
            except Exception as exc:
                logger.warning("on_stuck callback error: %s", exc)

        if self._config.webhook_url:
            self._post_webhook("stuck", {
                "task_id": task_id,
                "reason": reason,
            })

    def check_cost(self, task_id: str, cost_cents: float) -> None:
        """Check cost against threshold and emit alert if exceeded.

        Fires at most once per emitter instance to avoid repeated alerts
        as cost accumulates.
        """
        if self._cost_alerted:
            return
        if (
            self._config.cost_threshold_cents is not None
            and cost_cents > self._config.cost_threshold_cents
        ):
            self._cost_alerted = True

            if self._config.on_cost_exceeded:
                try:
                    self._config.on_cost_exceeded(task_id, cost_cents)
                except Exception as exc:
                    logger.warning("on_cost_exceeded callback error: %s", exc)

            if self._config.webhook_url:
                self._post_webhook("cost_exceeded", {
                    "task_id": task_id,
                    "cost_cents": cost_cents,
                    "threshold_cents": self._config.cost_threshold_cents,
                })

    def _post_webhook(self, alert_type: str, payload: Dict[str, Any]) -> None:
        """POST alert JSON to the configured webhook URL.  Never raises."""
        if not self._config.webhook_url:
            return
        try:
            body = json.dumps({
                "alert_type": alert_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **payload,
            }).encode("utf-8")

            headers: Dict[str, str] = {"Content-Type": "application/json"}
            if self._config.webhook_headers:
                headers.update(self._config.webhook_headers)

            req = urllib.request.Request(
                self._config.webhook_url,
                data=body,
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as exc:
            logger.debug("Alert webhook POST failed: %s", exc)
