"""Test 5: Real explore → compile → replay on httpbin form"""
import asyncio
import ast
from pathlib import Path
from playwright.async_api import async_playwright
from computeruse import track, TrackConfig
from computeruse.compiler import WorkflowCompiler
from computeruse.replay_executor import ReplayExecutor, ReplayConfig

API_URL = "http://localhost:8000"
API_KEY = "cu_test_testkey1234567890abcdef12"

async def main():
    print("\n━━━ Test 5: Real explore → compile → replay ━━━")
    
    # === EXPLORE ===
    print("\n  Phase 1: Explore (track real form)")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        async with track(page, TrackConfig(
            api_url=API_URL,
            api_key=API_KEY,
            task_id="e2e-pipeline",
        )) as t:
            await t.goto("https://httpbin.org/forms/post")
            await t.fill("input[name='custname']", "Pipeline Test")
            await t.fill("input[name='custemail']", "pipeline@example.com")
            await t.fill("textarea[name='comments']", "E2E pipeline test")
            await t.click("input[type='submit']")
            await asyncio.sleep(1)
        
        await browser.close()
    
    print(f"  {'✅' if len(t.steps) >= 4 else '❌'} Explored: {len(t.steps)} steps captured")
    
    # === COMPILE ===
    print("\n  Phase 2: Compile")
    compiler = WorkflowCompiler()
    workflow = compiler.compile_from_steps(
        t.steps,
        task_description="Fill httpbin form",
        source_task_id="e2e-pipeline",
        parameter_names=["email"],
    )
    
    wf_path = compiler.save_workflow(workflow, output_dir=".pokant/workflows")
    print(f"  {'✅' if len(workflow.steps) >= 4 else '❌'} Compiled: {len(workflow.steps)} steps")
    print(f"  {'✅' if len(workflow.parameters) > 0 else '❌'} Parameters: {list(workflow.parameters.keys())}")
    print(f"  ✅ Saved: {wf_path}")
    
    # Generate script
    script = compiler.generate_playwright_script(workflow)
    try:
        ast.parse(script)
        print(f"  ✅ Playwright script: {len(script)} chars, valid Python")
    except SyntaxError as e:
        print(f"  ❌ Script syntax error: {e}")
    
    # Show compiled steps
    for i, step in enumerate(workflow.steps):
        sel_count = len(step.selectors) if hasattr(step, 'selectors') and step.selectors else 0
        intent = getattr(step, 'intent', '')[:50]
        action = getattr(step, 'action_type', getattr(step, 'action', ''))
        print(f"    Step {i}: {action} — {intent} ({sel_count} selectors)")
    
    # === REPLAY ===
    print("\n  Phase 3: Replay with different parameters")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        replay_page = await browser.new_page()
        
        executor = ReplayExecutor(ReplayConfig(
            verify_actions=True,
            max_cost_cents=10.0,
        ))
        
        result = await executor.execute(
            workflow,
            params={"email": "replayed@pokant.dev"},
            page=replay_page,
        )
        
        await asyncio.sleep(2)
        final_url = replay_page.url
        await browser.close()
    
    print(f"  {'✅' if result.success else '❌'} Replay succeeded")
    print(f"  {'✅' if result.steps_executed == result.steps_total else '❌'} Steps: {result.steps_executed}/{result.steps_total}")
    print(f"  {'✅' if result.steps_deterministic == result.steps_total else '❌'} All deterministic (Tier 0): {result.steps_deterministic}")
    print(f"  {'✅' if result.cost_cents == 0 else '❌'} Zero cost: ${result.cost_cents/100:.6f}")
    print(f"  {'✅' if 'httpbin' in final_url else '❌'} Hit real page: {final_url}")
    
    if result.steps_healed > 0:
        print(f"  ℹ️  {result.steps_healed} steps needed healing (Tier 1)")
    if result.steps_ai_recovered > 0:
        print(f"  ℹ️  {result.steps_ai_recovered} steps needed AI (Tier 2)")
    
    print(f"\n  → Check localhost:3000 for 'e2e-pipeline'")
    print(f"  → Run: pokant compile e2e-pipeline")

asyncio.run(main())
