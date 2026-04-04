"""
Compile a successful run into a replayable workflow.

Takes a run JSON produced by wrap() or track() and compiles it into
a deterministic CompiledWorkflow that can be replayed without AI.
Optionally generates a standalone Playwright script.

Prerequisites:
    pip install pokant

Usage:
    # First, run a task to generate a run file:
    #   python browser_use_basic.py
    # Then compile it:
    python compile_workflow.py .pokant/runs/<task-id>.json

Expected output:
    Compiled: my-workflow (5 steps, 2 parameters)
    Saved to: .pokant/workflows/my-workflow.json
    Script:   .pokant/workflows/my-workflow.py
"""

import sys
from pathlib import Path

from computeruse import CompilationError, WorkflowCompiler


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python compile_workflow.py <run-file.json>")
        sys.exit(1)

    run_path = sys.argv[1]
    compiler = WorkflowCompiler()

    try:
        workflow = compiler.compile_from_run(run_path, name="my-workflow")
    except CompilationError as exc:
        print(f"Compilation failed: {exc}")
        sys.exit(1)

    # Save the compiled workflow JSON
    wf_path = compiler.save_workflow(workflow, output_dir=".pokant/workflows")
    print(f"Compiled: {workflow.name} ({len(workflow.steps)} steps, {len(workflow.parameters)} parameters)")
    print(f"Saved to: {wf_path}")

    # Generate a standalone Playwright script
    script_path = str(Path(".pokant/workflows") / f"{workflow.name}.py")
    script = compiler.generate_playwright_script(workflow, output_path=script_path)
    print(f"Script:   {script_path}")

    # Show the first few lines of the generated script
    print("\n--- Generated script preview ---")
    for line in script.split("\n")[:15]:
        print(f"  {line}")
    print("  ...")


if __name__ == "__main__":
    main()
