"""Desktop automation tracking with PyAutoGUI.

Prerequisites:
    pip install pokant pyautogui

Captures full-screen screenshots on every step automatically.
"""

import subprocess
import time

import pyautogui

from computeruse import PokantTracker
from computeruse.desktop import pyautogui_screenshot_fn

tracker = PokantTracker(
    screenshot_fn=pyautogui_screenshot_fn(),
    task_description="Open Calculator and compute 42 * 17",
    api_url="http://localhost:8000",
    api_key="cu_test_testkey1234567890abcdef12",
)
tracker.start()

# Launch calculator
subprocess.Popen(["open", "-a", "Calculator"])  # macOS
time.sleep(1)

tracker.record_desktop_step(
    action_type="desktop_launch",
    description="Opened Calculator",
    window_title="Calculator",
)

pyautogui.typewrite("42*17", interval=0.05)
tracker.record_desktop_step(
    action_type="desktop_type",
    description="Typed 42*17",
    window_title="Calculator",
)

pyautogui.press("enter")
tracker.record_desktop_step(
    action_type="desktop_hotkey",
    description="Pressed Enter",
    window_title="Calculator",
)

time.sleep(0.5)
tracker.complete(result={"expression": "42*17", "result": "714"})

print(f"Steps: {len(tracker.steps)}")
print(f"Replay: {tracker.replay_path}")
for s in tracker.steps:
    has_ss = "Y" if s.screenshot_bytes else "N"
    print(f"  [{has_ss}] {s.action_type}: {s.description}")
