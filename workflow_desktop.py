import time
from computeruse import PokantTracker
from computeruse.desktop import pillow_screenshot_fn

# Use Pillow (works on macOS without extra installs)
tracker = PokantTracker(
    screenshot_fn=pillow_screenshot_fn(),
    task_description="Desktop automation: capture current screen",
    api_url="http://localhost:8000",
    api_key="cu_test_testkey1234567890abcdef12",
)
tracker.start()

tracker.record_desktop_step(
    action_type="desktop_launch",
    description="Observing current desktop",
    window_title="Terminal",
)

time.sleep(1)

tracker.record_desktop_step(
    action_type="desktop_click",
    description="Simulated click on desktop",
    window_title="Finder",
    coordinates=(500, 300),
)

time.sleep(1)

tracker.record_desktop_step(
    action_type="desktop_type",
    description="Simulated typing in editor",
    window_title="TextEdit",
)

tracker.complete(result={"status": "desktop captured"})

print(f"Steps: {len(tracker.steps)}")
for i, s in enumerate(tracker.steps):
    has_ss = "✅" if s.screenshot_bytes else "❌"
    has_ctx = list(s.context.keys()) if s.context else "none"
    print(f"  Step {i}: {has_ss} {s.action_type} — {s.description}")
    print(f"           context: {has_ctx}")
print(f"Replay: {tracker.replay_path}")
