"""
Test 1: wrap() with adaptive retry — demo of failure diagnosis + recovery.

Flow:
  1. First attempt fails (simulated overlay blocking the submit button)
  2. FailureAnalyzer diagnoses: "element_interaction / overlay_blocking"
  3. RecoveryRouter plans: dismiss overlays, retry with modified prompt
  4. Second attempt runs the real agent — completes the form
  5. Dashboard shows both attempts with diagnosis + strategy

Prerequisites:
    pip install pokant browser-use
    export ANTHROPIC_API_KEY=sk-ant-...

Usage:
    python3 e2e_tests/test_1_enrichment.py
"""

import asyncio
import os
import uuid
from typing import Any

from browser_use import Agent
from browser_use.llm import ChatAnthropic

from computeruse import wrap, WrapConfig

API_URL = "http://localhost:8000"
API_KEY = "cu_test_testkey1234567890abcdef12"


class FailOnceAgent:
    """Demo wrapper: first attempt fails with a realistic overlay error,
    second attempt delegates to the real browser_use Agent.

    This lets the adaptive retry system show its full pipeline:
    diagnosis → recovery plan → modified retry → success.
    """

    def __init__(self, real_agent: Any) -> None:
        self._real = real_agent
        self._attempt = 0
        self.task = real_agent.task
        self.history: list[Any] = []
        self.calculate_cost = False

    async def run(self, **kwargs: Any) -> Any:
        self._attempt += 1
        if self._attempt == 1:
            # Simulate: agent filled the form, but the submit button
            # is covered by a cookie consent overlay.
            raise TimeoutError(
                "Timeout 30000ms exceeded. "
                "Element 'input[type=\"submit\"]' is obscured by "
                "<div class=\"cookie-consent-overlay\">. "
                "Cannot click the submit button — a popup or overlay "
                "is blocking the target element."
            )
        return await self._real.run(**kwargs)

    def add_new_task(self, task: str) -> None:
        self.task = task
        self._real.task = task

    def stop(self) -> None:
        if hasattr(self._real, "stop"):
            self._real.stop()


async def main() -> None:
    print("\n━━━ Test 1: Adaptive Retry Demo ━━━\n")

    task_id = str(uuid.uuid4())
    print(f"  Task ID: {task_id}")

    llm = ChatAnthropic(model="claude-sonnet-4-6")

    real_agent = Agent(
        task=(
            "Go to https://httpbin.org/forms/post and fill out the form:\n"
            "  - Customer name: Avi Patel\n"
            "  - Telephone: 555-867-5309\n"
            "  - E-mail: avi@pokant.dev\n"
            "  - Size: Medium\n"
            "  - Toppings: Bacon, Cheese\n"
            "  - Delivery instructions: Testing adaptive retry\n"
            "Then submit the form and confirm the result page loaded."
        ),
        llm=llm,
    )

    # Wrap the fail-once agent so attempt 1 fails, attempt 2 uses real agent
    demo_agent = FailOnceAgent(real_agent)

    config = WrapConfig(
        max_retries=3,
        adaptive_retry=True,
        diagnostic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        task_id=task_id,
        api_url=API_URL,
        api_key=API_KEY,
        output_dir=".pokant",
        save_screenshots=True,
        generate_replay=True,
    )

    wrapped = wrap(demo_agent, config=config)

    try:
        result = await wrapped.run(max_steps=30)
        print("\n  Task completed successfully!")
    except Exception as exc:
        print(f"\n  Task failed after all retries: {exc}")

    # ── Results ──────────────────────────────────────────────────────
    print(f"\n  Steps:    {len(wrapped.steps)}")
    print(f"  Cost:     ${wrapped.cost_cents / 100:.4f}")
    print(f"  Attempts: {len(wrapped.attempt_history)}")

    for a in wrapped.attempt_history:
        status = "✓" if a["status"] == "completed" else "✗"
        diag = a.get("diagnosis")
        if diag:
            method = (
                "AI" if diag.get("analysis_method") == "llm_haiku" else "Rule"
            )
            print(
                f"\n    {status} Attempt {a['attempt']}: "
                f"{diag['category']} ({method})"
            )
            print(f"      Root cause: {diag.get('root_cause', '')[:80]}")
            hint = diag.get("retry_hint", "")
            if hint:
                print(f"      Strategy:   {hint[:80]}")
            plan = a.get("recovery_plan")
            if plan:
                flags = [
                    k
                    for k in (
                        "fresh_browser",
                        "stealth_mode",
                        "clear_cookies",
                        "increase_timeout",
                        "reduce_max_actions",
                    )
                    if plan.get(k)
                ]
                if flags:
                    print(f"      Env:        {', '.join(flags)}")
                if plan.get("extend_system_message"):
                    print(f"      Sys msg:    {plan['extend_system_message'][:80]}")
        else:
            print(f"\n    {status} Attempt {a['attempt']}: {a['status']}")

    if wrapped.replay_path:
        print(f"\n  Replay: {wrapped.replay_path}")

    print(f"\n  → Dashboard: http://localhost:3000/tasks/{task_id}")


if __name__ == "__main__":
    asyncio.run(main())
