"""
Scrape product/pricing info from multiple sites and save a JSON report.

Usage:
    python examples/monitor_competitors.py

Reads ANTHROPIC_API_KEY from the repo-root .env file. Results are written
to competitor_report.json in the current directory.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

from computeruse import ComputerUse  # noqa: E402

TARGETS = [
    {"name": "Hacker News",    "url": "https://news.ycombinator.com",  "task": "Get the titles and point counts of the top 5 posts on the front page."},
    {"name": "GitHub Trending","url": "https://github.com/trending",    "task": "Get the names and descriptions of the top 5 trending repositories today."},
]

SCHEMA = {"items": "list[str]", "details": "list[str]"}

if __name__ == "__main__":
    cu = ComputerUse()
    results = []
    ok = fail = 0

    for t in TARGETS:
        print(f"Scraping {t['name']} …", flush=True)
        try:
            r = cu.run_task(url=t["url"], task=t["task"], output_schema=SCHEMA)
            results.append({"name": t["name"], "url": t["url"], "success": r.success,
                             "data": r.result, "steps": r.steps, "duration_ms": r.duration_ms})
            ok += r.success; fail += not r.success
            status = "✓" if r.success else "✗"
            print(f"  {status} {r.steps} steps  {r.duration_ms/1000:.1f}s")
        except Exception as e:
            results.append({"name": t["name"], "url": t["url"], "success": False, "error": str(e)})
            fail += 1
            print(f"  ✗ error: {e}", file=sys.stderr)

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "targets": results}
    Path("competitor_report.json").write_text(json.dumps(report, indent=2))
    print(f"\nReport saved to competitor_report.json  ({ok} ok, {fail} failed)")
