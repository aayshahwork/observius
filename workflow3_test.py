from computeruse import PokantTracker

# 5 failed tasks to trigger alerts
for i in range(5):
    t = PokantTracker(
        task_description=f"Failing task #{i+1} on portal.example.com",
        api_url="http://localhost:8000",
        api_key="cu_test_testkey1234567890abcdef12",
    )
    t.start()
    t.record_step(action_type="navigate", description="goto(portal.example.com)", success=False, error="Connection refused")
    t.fail(error="Connection refused", error_category="transient_network")
    print(f"  Failed #{i+1}: {t.task_id}")

# 1 expensive task
exp = PokantTracker(
    task_description="Expensive extraction",
    api_url="http://localhost:8000",
    api_key="cu_test_testkey1234567890abcdef12",
)
exp.start()
for i in range(20):
    exp.record_step(action_type="extract", description=f"Extract page {i+1}", tokens_in=50000, tokens_out=25000, success=True)
exp.complete()
print(f"  Expensive: {exp.task_id} — ${exp.cost_cents / 100:.2f}")

print("\nDone. Check:")
print("  1. localhost:3000 — bell icon should show alerts")
print("  2. localhost:3000/health — failure hotspots, error breakdown")
print("  3. Click bell → dropdown → acknowledge an alert")
