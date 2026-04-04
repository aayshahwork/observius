import asyncio
from playwright.async_api import async_playwright
from computeruse import track, TrackConfig

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        async with track(page, TrackConfig(
            api_url="http://localhost:8000",
            api_key="cu_test_testkey1234567890abcdef12",
        )) as t:
            await t.goto("https://example.com")
            title = await t.title()
            print(f"Page 1: {title}")
            
            await t.goto("https://httpbin.org/html")
            title2 = await t.title()
            print(f"Page 2: {title2}")
            
            await t.goto("https://httpbin.org/forms/post")
            await t.fill('input[name="custname"]', "Test User")
            await t.fill('input[name="custtel"]', "555-1234")
            print("Form filled")
        
        print(f"\nSteps: {len(t.steps)}")
        for step in t.steps:
            status = "✓" if step.success else "✗"
            print(f"  {status} {step.action_type} - {step.description} ({step.duration_ms}ms)")
        
        t.save_replay("form_test.html")
        print(f"Replay saved to form_test.html")
        
        import os
        if os.path.exists(".pokant/runs"):
            runs = os.listdir(".pokant/runs")
            print(f"Total runs saved: {len(runs)}")
        
        await browser.close()

asyncio.run(main())
