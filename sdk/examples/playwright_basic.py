"""
Minimal track() example with Playwright.

Wraps a Playwright Page so every action (goto, click, fill, etc.)
is automatically timed, screenshotted, and recorded. After the
block exits, run metadata and screenshots are saved to .pokant/.

Prerequisites:
    pip install pokant playwright
    playwright install chromium

Expected output:
    Steps: 2
    Step 1: goto(https://example.com) - 234ms
    Step 2: click(a) - 89ms
    Replay saved to .pokant/replays/<run-id>.html
"""

import asyncio

from playwright.async_api import async_playwright

from computeruse import track


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        async with track(page) as t:
            await t.goto("https://example.com")
            await t.click("a")

        for step in t.steps:
            print(f"Step {step.step_number}: {step.description} - {step.duration_ms}ms")

        replay_path = t.save_replay()
        print(f"Replay saved to {replay_path}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
