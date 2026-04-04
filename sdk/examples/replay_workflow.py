"""
Replay a compiled workflow against a live browser.

Executes a CompiledWorkflow deterministically using Playwright.
Falls back through 4 tiers if selectors break: direct replay,
selector healing, AI single-shot recovery, and full AI fallback.

Prerequisites:
    pip install pokant playwright
    playwright install chromium

Usage:
    # First compile a workflow:
    #   python compile_workflow.py .pokant/runs/<task-id>.json
    # Then replay it:
    python replay_workflow.py .pokant/workflows/my-workflow.json

Expected output:
    Replay: PASSED (5/5 steps)
    Tiers: 5 deterministic
    Cost: $0.0000
    Duration: 3.21s
"""

import asyncio
import sys

from playwright.async_api import async_playwright

from computeruse import ReplayConfig, ReplayExecutor


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python replay_workflow.py <workflow-file.json>")
        sys.exit(1)

    workflow_path = sys.argv[1]

    config = ReplayConfig(
        headless=False,        # show the browser
        max_cost_cents=10.0,   # budget cap for AI fallback
        verify_actions=True,   # check expected outcomes
    )

    executor = ReplayExecutor(config=config)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=config.headless)
        page = await browser.new_page()

        # Pass parameters if the workflow expects them
        params = {
            # "email": "test@example.com",
            # "password": "hunter2",
        }

        result = await executor.execute_from_file(
            workflow_path, params=params, page=page
        )

        await browser.close()

    # Print results
    status = "PASSED" if result.success else "FAILED"
    print(f"Replay: {status} ({result.steps_executed}/{result.steps_total} steps)")

    tier_parts = []
    if result.steps_deterministic:
        tier_parts.append(f"{result.steps_deterministic} deterministic")
    if result.steps_healed:
        tier_parts.append(f"{result.steps_healed} healed")
    if result.steps_ai_recovered:
        tier_parts.append(f"{result.steps_ai_recovered} AI-recovered")
    if tier_parts:
        print(f"Tiers: {', '.join(tier_parts)}")

    print(f"Cost: ${result.cost_cents / 100:.4f}")
    print(f"Duration: {result.duration_ms / 1000:.2f}s")

    if result.error:
        print(f"Error: {result.error}")


if __name__ == "__main__":
    asyncio.run(main())
