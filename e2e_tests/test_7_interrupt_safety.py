"""Test 7: Interrupt safety — partial data saved when run crashes mid-execution"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
from computeruse import track, TrackConfig

async def main():
    print("\n━━━ Test 7: Interrupt safety ━━━")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        error_caught = False
        try:
            async with track(page, TrackConfig(
                task_id="e2e-interrupt-test",
            )) as t:
                # These should succeed
                await t.goto("https://example.com")
                await t.click("a")
                await asyncio.sleep(0.5)
                print("    ✓ First 2 actions succeeded")
                
                # This should fail — domain doesn't exist
                await t.goto("https://this-domain-definitely-does-not-exist-xyz123.com")
        except Exception as e:
            error_caught = True
            print(f"    ✓ Expected error: {type(e).__name__}: {str(e)[:60]}")
        
        await browser.close()
    
    print(f"\n  {'✅' if error_caught else '❌'} Error was raised (not swallowed)")
    
    # Check partial data was saved
    run_json = Path(".pokant/runs/e2e-interrupt-test.json")
    print(f"  {'✅' if run_json.exists() else '❌'} Partial run JSON saved: {run_json}")
    
    if run_json.exists():
        data = json.loads(run_json.read_text())
        step_count = len(data.get("steps", []))
        status = data.get("status", "unknown")
        print(f"  {'✅' if step_count >= 2 else '❌'} Steps saved before crash: {step_count}")
        print(f"  {'✅' if status != 'completed' else '❌'} Status reflects failure: {status}")
    else:
        print("  ❌ No partial data — interrupt safety not working")

asyncio.run(main())
