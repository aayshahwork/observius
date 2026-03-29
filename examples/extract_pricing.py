"""
Extract structured pricing data from any SaaS website.

Usage:
    python examples/extract_pricing.py https://stripe.com/pricing
    python examples/extract_pricing.py        # defaults to HN (testing)
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

from computeruse import ComputerUse  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://news.ycombinator.com"

SCHEMA = {
    "plans": "list[str]",       # plan/tier names
    "prices": "list[str]",      # price per plan, e.g. "$49/mo"
    "highlights": "list[str]",  # one key feature or description per plan
}

if __name__ == "__main__":
    print(f"Extracting pricing from: {URL}\n")
    try:
        result = ComputerUse().run_task(
            url=URL,
            task="Find all pricing plans on this page. For each plan extract its name, price, and the single most important feature or description.",
            output_schema=SCHEMA,
        )
        if result.success:
            print(json.dumps(result.result, indent=2))
            print(f"\n✓ {result.steps} steps  {result.duration_ms / 1000:.1f}s")
        else:
            print(f"Task failed: {result.error}")
    except Exception as e:
        print(f"Error: {e}")
