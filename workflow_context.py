from computeruse import PokantTracker

tracker = PokantTracker(
    task_description="Full context system test",
    api_url="http://localhost:8000",
    api_key="cu_test_testkey1234567890abcdef12",
)
tracker.start()

# LLM step
tracker.record_llm_step(
    prompt="You are an insurance quoting agent. The user needs a quote for a 2024 Toyota Camry in California. Navigate to the Geico portal and start a new quote.",
    response="I'll navigate to geico.com and click 'Start a Quote'. I'll select 'Auto Insurance' and begin entering the vehicle details for a 2024 Toyota Camry.",
    model="claude-sonnet-4-6",
    tokens_in=250,
    tokens_out=120,
)

# API step
tracker.record_api_step(
    method="POST",
    url="https://api.geico.com/v1/quotes",
    status_code=201,
    request_body={"vehicle": "2024 Toyota Camry", "state": "CA", "driver_age": 30},
    response_body={"quote_id": "Q-12345", "monthly_premium": 142.50, "coverage": "full"},
)

# State snapshot
tracker.record_state_snapshot(
    state={
        "quote_id": "Q-12345",
        "premium": 142.50,
        "vehicle": "2024 Toyota Camry",
        "state": "CA",
        "step": "quote_received",
    },
    decision="Quote received successfully. Premium is within budget. Proceeding to comparison step.",
)

# Regular step with manual context
tracker.record_step(
    action_type="extract",
    description="Compared with Progressive quote",
    tokens_in=180,
    tokens_out=90,
    context={
        "comparison": {
            "geico": 142.50,
            "progressive": 156.00,
            "savings": 13.50,
        },
        "recommendation": "Geico is cheaper by $13.50/month",
    },
)

tracker.complete(result={"best_quote": "Geico", "monthly": 142.50})

print(f"Steps: {len(tracker.steps)}")
for s in tracker.steps:
    ctx_type = s.context.get("type", "custom") if s.context else "none"
    print(f"  {s.action_type}: {s.description[:60]} | context type: {ctx_type}")
print(f"\nReplay: {tracker.replay_path}")
print("Open the replay to verify context renders correctly:")
print(f"  open {tracker.replay_path}")
