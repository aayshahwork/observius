"""
workers/replay.py — Self-contained HTML replay generator.

Produces a single HTML file with inlined CSS, JS, and base64-encoded
screenshots. No external dependencies required to open.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from workers.models import StepData

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class ReplayGenerator:
    """Generates a self-contained HTML replay from step data."""

    def __init__(self, steps: List[StepData], task_metadata: Dict[str, Any]) -> None:
        self.steps = steps
        self.task_metadata = task_metadata

    def generate(self, output_path: str) -> str:
        """Generate the replay HTML file and return the output path."""
        replay_json = self._build_replay_json()

        html_template = (_TEMPLATES_DIR / "replay.html").read_text(encoding="utf-8")
        tailwind_css = (_TEMPLATES_DIR / "tailwind-subset.css").read_text(encoding="utf-8")

        html = html_template.replace("/* __TAILWIND_CSS__ */", tailwind_css)

        replay_data_js = json.dumps(replay_json, separators=(",", ":"))
        html = html.replace(
            'var replayData = "__REPLAY_DATA__";',
            f"var replayData = {replay_data_js};",
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        logger.info("Replay written to %s", output_path)
        return output_path

    def _build_replay_json(self) -> Dict[str, Any]:
        """Build the replay data structure."""
        return {
            "task_id": self.task_metadata.get("task_id", ""),
            "url": self.task_metadata.get("url", ""),
            "task_description": self.task_metadata.get("task", ""),
            "generated_at": self.task_metadata.get("generated_at", ""),
            "total_steps": len(self.steps),
            "duration_ms": self.task_metadata.get("duration_ms", 0),
            "success": self.task_metadata.get("success", False),
            "steps": [self._serialize_step(s) for s in self.steps],
        }

    def _serialize_step(self, step: StepData) -> Dict[str, Any]:
        """Serialize a StepData to the replay JSON format."""
        screenshot_b64: str | None = None
        if step.screenshot_bytes:
            screenshot_b64 = base64.standard_b64encode(step.screenshot_bytes).decode("ascii")

        return {
            "step_number": step.step_number,
            "timestamp": step.timestamp.isoformat(),
            "action_type": str(step.action_type),
            "description": step.description,
            "screenshot": screenshot_b64,
            "tokens_in": step.tokens_in,
            "tokens_out": step.tokens_out,
            "duration_ms": step.duration_ms,
            "success": step.success,
            "error": step.error,
        }
