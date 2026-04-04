"""
Configured wrap() example with custom retry, stuck detection, and sessions.

Shows how to use WrapConfig to tune the reliability layer:
- Increase retries for flaky sites
- Adjust stuck detection thresholds
- Persist cookies across runs with session_key
- Set a custom output directory

Prerequisites:
    pip install pokant browser-use langchain-anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

Expected output:
    Task ID: <uuid>
    Cost: $0.0456
    Steps: 12
    Replay: ./my-output/replays/<task-id>.html
"""

import asyncio

from browser_use import Agent
from browser_use.llm import ChatAnthropic

from computeruse import wrap, WrapConfig


async def main() -> None:
    llm = ChatAnthropic(model="claude-sonnet-4-6")
    agent = Agent(
        task="Log in and check the account balance",
        llm=llm,
    )

    config = WrapConfig(
        max_retries=5,
        stuck_screenshot_threshold=3,
        stuck_action_threshold=4,
        session_key="myapp.example.com",
        output_dir="./my-output",
    )

    wrapped = wrap(agent, config=config)
    result = await wrapped.run(max_steps=50)

    print(f"Task ID: {wrapped.task_id}")
    print(f"Cost: ${wrapped.cost_cents / 100:.4f}")
    print(f"Steps: {len(wrapped.steps)}")
    if wrapped.replay_path:
        print(f"Replay: {wrapped.replay_path}")


if __name__ == "__main__":
    asyncio.run(main())
