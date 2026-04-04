"""
Example: Using Pokant to track a Stagehand session.

Requires:
    pip install stagehand computeruse playwright

Set environment variables:
    BROWSERBASE_API_KEY=...
    BROWSERBASE_PROJECT_ID=...
    MODEL_API_KEY=...   (your Anthropic or OpenAI API key)
"""

import asyncio

from playwright.async_api import async_playwright
from stagehand import AsyncStagehand

from computeruse import observe_stagehand


async def main() -> None:
    # 1. Create a Stagehand client and start a session
    async with AsyncStagehand() as client:
        session = await client.sessions.start(
            model_name="anthropic/claude-sonnet-4-6",
        )

        # 2. Connect Playwright to the same session for screenshots
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(session.data.cdp_url)
        page = browser.contexts[0].pages[0]

        try:
            # 3. Wrap the session with Pokant tracking
            async with observe_stagehand(session, page=page) as t:
                await t.navigate("https://news.ycombinator.com")
                await t.act("click on the first article link")
                data = await t.extract(
                    "get the article title and URL",
                    schema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                        },
                    },
                )
                print("Extracted:", data)

            # 4. View results
            print(f"Steps recorded: {len(t.steps)}")
            for step in t.steps:
                print(f"  [{step.action_type}] {step.description} ({step.duration_ms}ms)")

            # 5. Save replay
            replay_path = t.save_replay()
            print(f"Replay saved to: {replay_path}")
        finally:
            await session.end()
            await browser.close()
            await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
