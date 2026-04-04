from computeruse import PokantTracker

tracker = PokantTracker(
    task_description="Custom agent: scrape 3 pages",
    api_url="http://localhost:8000",
    api_key="cu_test_testkey1234567890abcdef12",
)

tracker.start()

# Simulate a custom agent loop — no browser_use, no Playwright
tracker.record_step(
    action_type="navigate",
    description="Opened homepage",
    tokens_in=500,
    tokens_out=200,
    success=True,
    duration_ms=2300,
)

tracker.record_step(
    action_type="extract",
    description="Extracted product list",
    tokens_in=1200,
    tokens_out=800,
    success=True,
    duration_ms=4500,
)

tracker.record_step(
    action_type="click",
    description="Clicked next page",
    tokens_in=300,
    tokens_out=150,
    success=True,
    duration_ms=1200,
)

tracker.complete(result={"products": ["Widget A", "Widget B"]})

print(f"Task ID: {tracker.task_id}")
print(f"Steps: {len(tracker.steps)}")
print(f"Cost: ${tracker.cost_cents / 100:.4f}")
print(f"Replay: {tracker.replay_path}")
print(f"Stuck: {tracker.is_stuck}")

# Test stuck detection
print("\n--- Stuck detection test ---")
stuck_tracker = PokantTracker(task_description="Stuck agent test")
stuck_tracker.start()
for i in range(6):
    stuck_tracker.record_step(
        action_type="click",
        description="click(#submit)",
        success=True,
    )
print(f"Is stuck after 6 identical actions: {stuck_tracker.is_stuck}")
if stuck_tracker.is_stuck:
    print(f"✅ Stuck detection works: {stuck_tracker.stuck_reason}")
else:
    print("⚠️  Stuck detection didn't trigger")
stuck_tracker.complete()
