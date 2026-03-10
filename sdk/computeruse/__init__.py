"""
ComputerUse SDK - One API to automate any web workflow

Example:
    from computeruse import ComputerUse

    cu = ComputerUse()
    result = cu.run_task(
        url="https://example.com",
        task="Extract the page title",
        output_schema={"title": "str"}
    )

    print(result.result["title"])
"""

from computeruse.client import ComputerUse
from computeruse.exceptions import (
    APIError,
    AuthenticationError,
    BrowserError,
    ComputerUseError,
    RetryExhaustedError,
    SessionError,
    TaskExecutionError,
    TimeoutError,
    ValidationError,
)
from computeruse.models import SessionData, StepData, TaskConfig, TaskResult

__version__ = "0.1.0"

__all__ = [
    # Client
    "ComputerUse",
    # Models
    "TaskConfig",
    "TaskResult",
    "StepData",
    "SessionData",
    # Exceptions
    "ComputerUseError",
    "TaskExecutionError",
    "BrowserError",
    "ValidationError",
    "AuthenticationError",
    "TimeoutError",
    "RetryExhaustedError",
    "SessionError",
    "APIError",
    # Metadata
    "__version__",
]
