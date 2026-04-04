"""
Minimal wrap() example with browser-use.

Wraps a browser-use Agent with automatic retry, stuck detection,
cost tracking, and replay generation. Run output is saved to
.pokant/ in the current directory.

Prerequisites:
    pip install pokant browser-use langchain-anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

Expected output:
    Cost: $0.0123
    Steps: 5
    Replay: .pokant/replays/<task-id>.html
"""

import asyncio

from browser_use import Agent
from browser_use.llm import ChatAnthropic

from computeruse import wrap


async def main() -> None:
    llm = ChatAnthropic(model="claude-sonnet-4-6")
    agent = Agent(task="Find the top story on Hacker News", llm=llm)

    wrapped = wrap(agent)
    result = await wrapped.run()

    print(f"Cost: ${wrapped.cost_cents / 100:.4f}")
    print(f"Steps: {len(wrapped.steps)}")
    if wrapped.replay_path:
        print(f"Replay: {wrapped.replay_path}")


if __name__ == "__main__":
    asyncio.run(main())
