"""Desktop automation with Claude's computer_use tool + Pokant tracking.

Shows how to track a Claude computer_use agent that controls the full desktop.

Prerequisites:
    pip install pokant anthropic mss
"""

import anthropic

from computeruse import PokantTracker
from computeruse.desktop import mss_screenshot_fn

tracker = PokantTracker(
    screenshot_fn=mss_screenshot_fn(),
    task_description="Claude computer_use desktop agent",
    api_url="http://localhost:8000",
    api_key="cu_test_testkey1234567890abcdef12",
)
tracker.start()

client = anthropic.Anthropic()

# Your Claude computer_use loop would go here.
# After each tool execution:
#   tracker.record_desktop_step(
#       action_type="desktop_click",
#       description="Clicked OK button",
#       window_title="Settings",
#       coordinates=(450, 320),
#   )
#
# After each LLM call:
#   tracker.record_llm_step(
#       prompt=..., response=...,
#       tokens_in=..., tokens_out=...,
#   )
#
# The tracker auto-captures desktop screenshots on every step
# and sends everything to the Pokant dashboard.

tracker.complete()
