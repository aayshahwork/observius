"""Test 6: Click chain through Wikipedia — multiple sequential clicks with enrichment"""
import asyncio
from playwright.async_api import async_playwright
from computeruse import track, TrackConfig

API_URL = "http://localhost:8000"
API_KEY = "cu_test_testkey1234567890abcdef12"

async def main():
    print("\n━━━ Test 6: Click chain on Wikipedia ━━━")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        async with track(page, TrackConfig(
            api_url=API_URL,
            api_key=API_KEY,
            task_id="e2e-click-chain",
        )) as t:
            # Start at Web browser article
            await t.goto("https://en.wikipedia.org/wiki/Web_browser")
            await asyncio.sleep(1)
            
            # Click TOC link
            try:
                await t.click("a[href='#History']")
                await asyncio.sleep(0.5)
                print("    Clicked #History")
            except Exception:
                try:
                    await t.click("a[href='#Function']")
                    await asyncio.sleep(0.5)
                    print("    Clicked #Function")
                except Exception:
                    print("    ⚠️  TOC links not found")
            
            # Click an internal wiki link
            try:
                await t.click("a[title='Netscape Navigator']")
                await asyncio.sleep(1)
                print(f"    Navigated to: {page.url}")
            except Exception:
                try:
                    await t.click("a[title='Mozilla Firefox']")
                    await asyncio.sleep(1)
                    print(f"    Navigated to: {page.url}")
                except Exception:
                    print("    ⚠️  Wiki link click failed")
            
            # Go back
            await t.goto("https://en.wikipedia.org/wiki/Web_browser")
            await asyncio.sleep(0.5)
            print("    Navigated back")
            
            # Click another section
            try:
                await t.click("a[href='#See_also']")
                await asyncio.sleep(0.5)
                print("    Clicked #See_also")
            except Exception:
                pass
            
            # Click a link in See Also
            try:
                await t.click("#See_also ~ ul a")
                await asyncio.sleep(1)
                print(f"    Followed See Also link to: {page.url}")
            except Exception:
                print("    ⚠️  See Also link not found")
        
        await browser.close()
    
    # Analyze results
    click_steps = [s for s in t.steps if s.action_type in ("click", "CLICK")]
    nav_steps = [s for s in t.steps if s.action_type in ("navigate", "NAVIGATE")]
    
    urls_seen = set()
    for s in t.steps:
        if hasattr(s, 'pre_url') and s.pre_url:
            urls_seen.add(s.pre_url)
        if hasattr(s, 'post_url') and s.post_url:
            urls_seen.add(s.post_url)
    
    clicks_with_selectors = [s for s in click_steps if hasattr(s, 'selectors') and s.selectors]
    clicks_with_intent = [s for s in click_steps if hasattr(s, 'intent') and s.intent]
    steps_with_screenshots = [s for s in t.steps if s.screenshot_bytes]
    
    print(f"\n  {'✅' if len(t.steps) >= 4 else '❌'} Total steps: {len(t.steps)}")
    print(f"  {'✅' if len(click_steps) >= 2 else '❌'} Clicks: {len(click_steps)}")
    print(f"  {'✅' if len(nav_steps) >= 2 else '❌'} Navigations: {len(nav_steps)}")
    print(f"  {'✅' if len(urls_seen) >= 2 else '❌'} Unique URLs tracked: {len(urls_seen)}")
    print(f"  {'✅' if len(clicks_with_selectors) > 0 else '❌'} Clicks with selectors: {len(clicks_with_selectors)}/{len(click_steps)}")
    print(f"  {'✅' if len(clicks_with_intent) > 0 else '❌'} Clicks with intent: {len(clicks_with_intent)}/{len(click_steps)}")
    print(f"  {'✅' if len(steps_with_screenshots) > 0 else '❌'} Screenshots: {len(steps_with_screenshots)}/{len(t.steps)}")
    
    print(f"\n  Click chain:")
    for i, step in enumerate(t.steps):
        status = "✓" if step.success else "✗"
        intent = getattr(step, 'intent', '')[:50]
        pre = getattr(step, 'pre_url', '')[-40:]
        post = getattr(step, 'post_url', '')[-40:]
        changed = " → URL changed" if pre and post and pre != post else ""
        print(f"    {status} {step.action_type:10} {step.description[:45]}{changed}")
        if intent:
            print(f"      intent: {intent}")
    
    print(f"\n  → Check localhost:3000 for 'e2e-click-chain'")

asyncio.run(main())
