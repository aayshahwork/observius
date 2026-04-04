"""Test 4: Post-action verification catches real failures on real pages"""
import asyncio
from playwright.async_api import async_playwright
from computeruse.action_verifier import ActionVerifier

async def main():
    print("\n━━━ Test 4: Post-action verification on real pages ━━━")
    
    verifier = ActionVerifier()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        # Navigate to real page
        await page.goto("https://httpbin.org/html")
        await asyncio.sleep(1)
        
        # 1: URL pattern match (should pass)
        r1 = await verifier.verify_action(page, "navigate", expected_url_pattern=r"httpbin\.org/html")
        print(f"  {'✅' if r1.passed else '❌'} URL pattern match: passed={r1.passed}")
        
        # 2: Element exists (should pass — h1 exists on this page)
        r2 = await verifier.verify_action(page, "click", expected_element="h1")
        print(f"  {'✅' if r2.passed else '❌'} Element h1 found: passed={r2.passed}")
        
        # 3: Element missing (should catch it)
        r3 = await verifier.verify_action(page, "click", expected_element="#nonexistent-xyz")
        caught_missing = any(f["check"] == "element_presence" for f in r3.failures)
        print(f"  {'✅' if caught_missing else '❌'} Missing element caught: failures={r3.failures}")
        
        # 4: Wrong URL pattern (should be critical failure)
        r4 = await verifier.verify_action(page, "navigate", expected_url_pattern=r"google\.com/dashboard")
        print(f"  {'✅' if r4.has_critical_failure else '❌'} URL mismatch is critical: critical={r4.has_critical_failure}")
        
        # 5: Text exists (httpbin /html has Herman Melville text)
        r5 = await verifier.verify_action(page, "click", expected_text="Herman Melville")
        print(f"  {'✅' if r5.passed else '❌'} Text 'Herman Melville' found: passed={r5.passed}")
        
        # 6: Text missing
        r6 = await verifier.verify_action(page, "click", expected_text="THIS TEXT DOES NOT EXIST")
        caught_text = any(f["check"] == "text_presence" for f in r6.failures)
        print(f"  {'✅' if caught_text else '❌'} Missing text caught: failures={r6.failures}")
        
        await browser.close()

asyncio.run(main())
