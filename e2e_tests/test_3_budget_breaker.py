"""Test 3: Budget circuit breaker stops expensive AI agent"""
import asyncio
from pathlib import Path
from computeruse.budget import BudgetMonitor, BudgetExceededError

async def main():
    print("\n━━━ Test 3: Budget circuit breaker ━━━")
    
    # Part A: Unit-level budget enforcement
    monitor = BudgetMonitor(max_cost_cents=0.1)
    steps_before_kill = 0
    try:
        for i in range(1000):
            monitor.record_step_cost(5000, 2000)
            steps_before_kill = i + 1
    except BudgetExceededError:
        pass
    
    print(f"  {'✅' if steps_before_kill < 1000 else '❌'} Budget enforced: killed after {steps_before_kill} steps at {monitor.total_cost_cents:.4f}¢")
    print(f"  {'✅' if monitor.spend_rate_cents_per_minute > 0 else '❌'} Spend rate: {monitor.spend_rate_cents_per_minute:.2f}¢/min")
    
    # Part B: Anomaly detection
    monitor2 = BudgetMonitor(max_cost_cents=1000.0)
    for _ in range(5):
        monitor2.record_step_cost(1000, 500)
    warning = monitor2.check_anomaly()
    print(f"  {'✅' if warning is None else '❌'} No false anomaly: {warning}")
    
    # Part C: Real agent with tiny budget
    try:
        from langchain_anthropic import ChatAnthropic
        from browser_use import Agent
        from computeruse import wrap, WrapConfig
        
        llm = ChatAnthropic(model="claude-sonnet-4-20250514")
        agent = Agent(
            task="Go to https://en.wikipedia.org/wiki/Main_Page and click on 10 different links, reading each page",
            llm=llm,
        )
        
        wrapped = wrap(agent, WrapConfig(
            max_cost_cents=2.0,
            api_url="http://localhost:8000",
            api_key="cu_test_testkey1234567890abcdef12",
            task_id="e2e-budget-test",
        ))
        
        try:
            await wrapped.run(max_steps=50)
        except Exception:
            pass
        
        print(f"  {'✅' if wrapped.cost_cents <= 10.0 else '❌'} Agent stopped near budget: {wrapped.cost_cents:.2f}¢ (limit: 2¢)")
        print(f"  {'✅' if Path('.pokant/runs/e2e-budget-test.json').exists() else '❌'} Partial data saved")
        print(f"\n  → Check localhost:3000 for 'e2e-budget-test'")
    except ImportError:
        print("  ⚠️  Skipping real agent test — browser_use not installed")

asyncio.run(main())
