"""Login automation example — from the README "Login Automation" section.

Run:
    python examples/example_login.py
"""

from computeruse import ComputerUse

cu = ComputerUse()

result = cu.run_task(
    url="https://github.com/login",
    task="Log in and confirm the login was successful",
    credentials={
        "username": "alice",
        "password": "s3cr3t",
    },
    output_schema={
        "logged_in": "bool",
        "username_displayed": "str",
    },
)

if result.success:
    print("Logged in as:", result.result["username_displayed"])
else:
    print("Login failed:", result.error)
