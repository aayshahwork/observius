"""
Real desktop automation: Claude looks at your screen, decides actions, 
PyAutoGUI executes them, PokantTracker records everything.
"""
import time
import anthropic
import pyautogui
import base64
import io
from PIL import ImageGrab
from computeruse import PokantTracker
from computeruse.desktop import pillow_screenshot_fn

# Safety: PyAutoGUI failsafe — move mouse to corner to abort
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.5

client = anthropic.Anthropic()

tracker = PokantTracker(
    screenshot_fn=pillow_screenshot_fn(),
    task_description="Open TextEdit, type a haiku about coding, save it",
    api_url="http://localhost:8000",
    api_key="cu_test_testkey1234567890abcdef12",
)
tracker.start()

def take_screenshot_b64():
    """Capture screen and return base64 for Claude."""
    img = ImageGrab.grab()
    # Resize to max 1280px wide for Claude
    if img.width > 1280:
        ratio = 1280 / img.width
        img = img.resize((1280, int(img.height * ratio)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")

def execute_action(action):
    """Execute a Claude computer_use action via PyAutoGUI."""
    action_type = action.get("type", "")
    
    if action_type == "left_click":
        x, y = action["x"], action["y"]
        pyautogui.click(x, y)
        return f"Clicked at ({x}, {y})"
    
    elif action_type == "type":
        text = action["text"]
        pyautogui.typewrite(text, interval=0.03) if text.isascii() else pyautogui.write(text)
        return f"Typed: {text[:50]}"
    
    elif action_type == "key":
        key = action["key"]
        pyautogui.hotkey(*key.split("+"))
        return f"Pressed: {key}"
    
    elif action_type == "screenshot":
        return "Screenshot taken"
    
    elif action_type == "scroll":
        x, y = action.get("x", 0), action.get("y", 0)
        clicks = action.get("amount", 3)
        pyautogui.scroll(clicks, x, y)
        return f"Scrolled {clicks} at ({x}, {y})"
    
    return f"Unknown action: {action_type}"

# Agent loop
messages = []
system = """You are controlling a macOS computer. Your task: Open TextEdit, type "Hello from Pokant" and then close it without saving.

Use keyboard shortcuts:
- cmd+space to open Spotlight
- Type "TextEdit" and press Return to launch it
- cmd+w to close the window
- Click "Don't Save" if prompted

Take a screenshot first to see the current state, then act step by step. When done, respond with "TASK_COMPLETE".
"""

MAX_STEPS = 15
for step_num in range(MAX_STEPS):
    # Get screenshot
    screenshot_b64 = take_screenshot_b64()
    
    # Build message
    if step_num == 0:
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}},
                {"type": "text", "text": "Here is the current screen. Please complete the task."},
            ],
        }]
    else:
        messages.append({
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}},
                {"type": "text", "text": "Here is the screen after the last action. Continue."},
            ],
        })
    
    # Ask Claude
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=messages,
    )
    
    # Parse response
    response_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            response_text += block.text
    
    # Record LLM step
    tracker.record_llm_step(
        prompt=f"[Screenshot + 'Continue'] (step {step_num})",
        response=response_text[:500],
        model="claude-sonnet-4-6",
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
    )
    
    print(f"\nStep {step_num}: Claude says: {response_text[:100]}...")
    
    # Check if done
    if "TASK_COMPLETE" in response_text:
        print("\n✅ Claude says task is complete!")
        break
    
    # Extract action from response (simple parsing — Claude describes actions in text)
    # For a real agent you'd use tool_use, but this works for a demo
    if "cmd+space" in response_text.lower() or "spotlight" in response_text.lower():
        pyautogui.hotkey("command", "space")
        time.sleep(0.5)
        tracker.record_desktop_step(
            action_type="desktop_hotkey",
            description="Opened Spotlight (Cmd+Space)",
            window_title="Spotlight",
        )
    elif "type" in response_text.lower() and "textedit" in response_text.lower():
        pyautogui.typewrite("TextEdit", interval=0.05)
        time.sleep(0.3)
        pyautogui.press("return")
        time.sleep(1)
        tracker.record_desktop_step(
            action_type="desktop_type",
            description="Typed 'TextEdit' and pressed Return",
            window_title="Spotlight",
        )
    elif "hello from pokant" in response_text.lower() or "type" in response_text.lower():
        pyautogui.typewrite("Hello from Pokant", interval=0.03)
        tracker.record_desktop_step(
            action_type="desktop_type",
            description="Typed 'Hello from Pokant'",
            window_title="TextEdit",
        )
    elif "cmd+w" in response_text.lower() or "close" in response_text.lower():
        pyautogui.hotkey("command", "w")
        time.sleep(0.5)
        tracker.record_desktop_step(
            action_type="desktop_hotkey",
            description="Closed window (Cmd+W)",
            window_title="TextEdit",
        )
    elif "don't save" in response_text.lower() or "delete" in response_text.lower():
        pyautogui.hotkey("command", "d")  # Don't Save shortcut on macOS
        time.sleep(0.3)
        tracker.record_desktop_step(
            action_type="desktop_click",
            description="Clicked Don't Save",
            window_title="TextEdit",
        )
    else:
        tracker.record_step(
            action_type="unknown",
            description=f"Claude response not parsed: {response_text[:80]}",
        )
    
    messages.append({"role": "assistant", "content": response_text})
    time.sleep(0.5)

tracker.complete(result={"task": "completed"})

print(f"\n=== Results ===")
print(f"Steps: {len(tracker.steps)}")
print(f"Cost: ${tracker.cost_cents / 100:.4f}")
print(f"Replay: {tracker.replay_path}")
print(f"\nOpen replay: open {tracker.replay_path}")
print(f"Dashboard: http://localhost:3000/tasks")
