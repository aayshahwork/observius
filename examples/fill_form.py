"""
Fill out a web form using Pokant.

Navigates to https://httpbin.org/forms/post, fills every field with sample
data, submits the form, and returns a confirmation with the submitted values.

Usage:
    python examples/fill_form.py
"""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

from computeruse import ComputerUse  # noqa: E402

if __name__ == "__main__":
    result = ComputerUse().run_task(
        url="https://httpbin.org/forms/post",
        task=(
            "Fill out the form completely: "
            "Customer name: Jane Smith, "
            "Telephone: 555-0100, "
            "E-mail address: jane@example.com, "
            "select Pizza Size Large, "
            "check Bacon and Cheese toppings, "
            "Delivery time: 18:30, "
            "add delivery instructions: Please ring the bell. "
            "Then click Order."
        ),
        output_schema={"submitted": "bool", "confirmation": "str"},
    )
    if result.success:
        print("Submitted:", result.result.get("submitted"))
        print("Confirmation:", result.result.get("confirmation"))
    else:
        print(f"Failed: {result.error}")
    print(f"\n{result.steps} steps  {result.duration_ms/1000:.1f}s")
