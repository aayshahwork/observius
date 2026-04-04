import asyncio
from playwright.async_api import async_playwright
from computeruse import PokantTracker

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        # Test 1: page= auto-screenshot
        tracker = PokantTracker(
            page=page,
            task_description="Auto-screenshot tracker test",
            api_url="http://localhost:8000",
            api_key="cu_test_testkey1234567890abcdef12",
        )
        tracker.start()
        
        await page.goto("https://example.com")
        await tracker.arecord_step(action_type="navigate", description="Opened example.com")
        
        await page.goto("https://httpbin.org/html")
        await tracker.arecord_step(action_type="navigate", description="Opened httpbin")
        
        tracker.complete()
        
        print("=== Auto-screenshot test ===")
        for i, step in enumerate(tracker.steps):
            has_ss = "✅" if step.screenshot_bytes else "❌"
            print(f"  Step {i}: {has_ss} {step.action_type} — {step.description}")
        print(f"  Replay: {tracker.replay_path}")
        
        # Test 2: Context helpers (no browser needed)
        ctx_tracker = PokantTracker(
            task_description="Context system test",
            api_url="http://localhost:8000",
            api_key="cu_test_testkey1234567890abcdef12",
        )
        ctx_tracker.start()
        
        ctx_tracker.record_llm_step(
            prompt="What is the title of example.com?",
            response="The title is 'Example Domain'.",
            model="claude-sonnet-4-6",
            tokens_in=150,
            tokens_out=80,
        )
        
        ctx_tracker.record_api_step(
            method="GET",
            url="https://api.example.com/info",
            status_code=200,
            response_body={"title": "Example Domain"},
        )
        
        ctx_tracker.record_state_snapshot(
            state={"extracted": True, "confidence": 0.95},
            decision="Data extracted, task complete",
        )
        
        ctx_tracker.complete()
        
        print("\n=== Context system test ===")
        for s in ctx_tracker.steps:
            ctx_keys = list(s.context.keys()) if s.context else "none"
            print(f"  {s.action_type}: {s.description} | context: {ctx_keys}")
        print(f"  Replay: {ctx_tracker.replay_path}")
        
        # Test 3: Stuck detection still works
        stuck = PokantTracker(task_description="Stuck test")
        stuck.start()
        for i in range(6):
            stuck.record_step(action_type="click", description="click(#submit)")
        print(f"\n=== Stuck detection ===")
        print(f"  Is stuck: {stuck.is_stuck} {'✅' if stuck.is_stuck else '❌'}")
        stuck.complete()
        
        # Test 4: screenshot_fn manual override
        fn_tracker = PokantTracker(
            screenshot_fn=lambda: b"fake-screenshot-bytes",
            task_description="screenshot_fn test",
        )
        fn_tracker.start()
        fn_tracker.record_step(action_type="click", description="Custom screenshot fn")
        has_ss = fn_tracker.steps[0].screenshot_bytes is not None
        print(f"\n=== screenshot_fn test ===")
        print(f"  Has screenshot from fn: {'✅' if has_ss else '❌'}")
        fn_tracker.complete()
        
        await browser.close()

asyncio.run(main())
