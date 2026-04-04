"""Test 2: Cost tracking with real AI agent — verifies $0 bug is fixed"""
import asyncio
import json
from pathlib import Path
from browser_use.llm.anthropic import ChatAnthropic
from browser_use import Agent
from computeruse import wrap, WrapConfig

API_URL = "http://localhost:8000"
API_KEY = "cu_test_testkey1234567890abcdef12"

async def main():
    print("\n━━━ Test 2: Cost tracking with real AI agent ━━━")
    
    llm = ChatAnthropic(model="claude-sonnet-4-20250514")
    agent = Agent(
        task="Go to https://example.com and tell me the page title",
        llm=llm,
    )
    
    wrapped = wrap(agent, WrapConfig(
        max_cost_cents=500,
        api_url=API_URL,
        api_key=API_KEY,
        task_id="e2e-cost-test",
    ))
    
    result = await wrapped.run(max_steps=5)
    
    print(f"\n  Steps: {len(wrapped.steps)}")
    for i, s in enumerate(wrapped.steps):
        print(f"    Step {i}: tokens_in={s.tokens_in}, tokens_out={s.tokens_out}, cost={getattr(s, 'cost_cents', '?')}")
    
    print(f"\n  {'✅' if wrapped.cost_cents > 0 else '❌'} Total cost: ${wrapped.cost_cents/100:.4f} (should be > $0)")
    print(f"  {'✅' if any(s.tokens_in > 0 for s in wrapped.steps) else '❌'} Token counts populated")
    print(f"  ✅ Result: {str(result.final_result())[:100]}")
    
    run_json = Path(".pokant/runs/e2e-cost-test.json")
    if run_json.exists():
        data = json.loads(run_json.read_text())
        print(f"  {'✅' if data.get('cost_cents', 0) > 0 else '❌'} JSON cost: ${data.get('cost_cents',0)/100:.4f}")
    
    print(f"\n  → Check localhost:3000 for 'e2e-cost-test'")

asyncio.run(main())
