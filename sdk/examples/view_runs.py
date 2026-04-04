"""
Read .pokant/runs/*.json and print a summary table.

Shows how to programmatically access run metadata without the
dashboard or CLI. Useful for CI pipelines or custom reporting.

Prerequisites:
    pip install pokant

Expected output (after running some tasks):
    Found 3 runs in .pokant/runs/

    ID         Status     Steps  Cost     Duration
    a1b2c3d4   completed  8      $0.0234  12.3s
    e5f6g7h8   failed     3      $0.0089  4.1s
    i9j0k1l2   completed  15     $0.0567  28.7s

    Total cost: $0.0890
    Success rate: 66.7% (2/3)
"""

import json
from pathlib import Path


def main() -> None:
    runs_dir = Path(".pokant/runs")

    if not runs_dir.is_dir():
        print("No runs found. Run a task first to generate data.")
        return

    runs = []
    for f in sorted(runs_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "task_id" in data:
                runs.append(data)
        except (OSError, json.JSONDecodeError):
            continue

    if not runs:
        print("No valid run files found.")
        return

    print(f"Found {len(runs)} runs in {runs_dir}/\n")
    print(f"{'ID':<12} {'Status':<12} {'Steps':>5}  {'Cost':>8}  {'Duration':>8}")

    total_cost = 0.0
    completed = 0

    for run in runs:
        task_id = run["task_id"][:8]
        status = run.get("status", "unknown")
        steps = run.get("step_count", 0)
        cost = run.get("cost_cents", 0) / 100
        duration = run.get("duration_ms", 0) / 1000

        total_cost += cost
        if status == "completed":
            completed += 1

        print(f"{task_id:<12} {status:<12} {steps:>5}  ${cost:>7.4f}  {duration:>6.1f}s")

    print(f"\nTotal cost: ${total_cost:.4f}")
    if runs:
        rate = completed / len(runs) * 100
        print(f"Success rate: {rate:.1f}% ({completed}/{len(runs)})")


if __name__ == "__main__":
    main()
