"""Quick start example — from the README "Quick Start" section.

Run:
    python examples/example_quickstart.py
"""

from computeruse import ComputerUse

cu = ComputerUse()
result = cu.run_task(
    url="https://news.ycombinator.com",
    task="Get the top 5 post titles",
    output_schema={"titles": "list[str]"},
)
print(result.result["titles"])
