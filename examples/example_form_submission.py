"""Form submission example — from the README "Form Submission" section.

Run:
    python examples/example_form_submission.py
"""

from computeruse import ComputerUse, TaskExecutionError

cu = ComputerUse()

try:
    result = cu.run_task(
        url="https://example.com/contact",
        task=(
            "Fill in the contact form with the following details and submit it:\n"
            "  name: Alice Example\n"
            "  email: alice@example.com\n"
            "  message: Hello, I'd like more information about your product."
        ),
        output_schema={
            "submitted": "bool",
            "confirmation_message": "str",
        },
        max_steps=20,
    )
except TaskExecutionError as exc:
    print("Agent error:", exc)
else:
    if result.result.get("submitted"):
        print("Form submitted:", result.result["confirmation_message"])
    else:
        print("Form was not accepted:", result.result.get("confirmation_message"))
