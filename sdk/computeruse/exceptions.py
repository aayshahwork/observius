from __future__ import annotations

from typing import Any, Dict, Optional


class ComputerUseError(Exception):
    """Base exception for all computeruse SDK errors.

    All SDK-specific exceptions inherit from this class, so callers can
    catch ``ComputerUseError`` to handle any SDK failure in one place.
    """

    def __init__(self, message: str = "An unexpected error occurred") -> None:
        self.message = message
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the exception to a JSON-compatible dict."""
        return {
            "error": type(self).__name__,
            "message": self.message,
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self.message!r})"


class TaskExecutionError(ComputerUseError):
    """Raised when a browser automation task fails during execution.

    This covers failures that occur after the task has started — for example
    the model getting stuck, an unexpected page state, or an unrecoverable
    mid-task error that is not a timeout or retry exhaustion.
    """

    def __init__(self, message: str = "Task execution failed") -> None:
        super().__init__(message)


class BrowserError(ComputerUseError):
    """Raised when a browser-level operation fails.

    Examples include failure to launch the browser, inability to open a new
    page, navigation errors, or crashes inside the Playwright/browser process.
    """

    def __init__(self, message: str = "A browser error occurred") -> None:
        super().__init__(message)


class ValidationError(ComputerUseError):
    """Raised when the task output does not match the expected schema.

    Thrown by the validator after task completion when the extracted result
    is missing required fields or contains values of the wrong type, as
    defined by ``TaskConfig.output_schema``.
    """

    def __init__(self, message: str = "Output validation failed") -> None:
        super().__init__(message)


class AuthenticationError(ComputerUseError):
    """Raised when login or credential injection fails.

    Thrown when the SDK detects that provided credentials were rejected by
    the target site, a login form could not be located, or the session is
    no longer authenticated mid-task.
    """

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class TimeoutError(ComputerUseError):
    """Raised when a task exceeds its configured wall-clock timeout.

    Thrown when execution time surpasses ``TaskConfig.timeout_seconds``,
    regardless of how many steps have been completed. Inherits from
    ``ComputerUseError`` rather than the built-in ``TimeoutError`` so that
    SDK consumers always deal with the SDK's own hierarchy.
    """

    def __init__(self, message: str = "Task exceeded the configured timeout") -> None:
        super().__init__(message)


class RetryExhaustedError(ComputerUseError):
    """Raised when all retry attempts have been consumed without success.

    Thrown after the retry loop exhausts ``TaskConfig.retry_attempts``
    consecutive failures. The ``last_error`` attribute preserves the
    underlying exception that triggered the final attempt.
    """

    def __init__(
        self,
        message: str = "Maximum retry attempts reached",
        last_error: Optional[Exception] = None,
    ) -> None:
        self.last_error = last_error
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["last_error"] = str(self.last_error) if self.last_error else None
        return base

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(message={self.message!r}, "
            f"last_error={self.last_error!r})"
        )


class SessionError(ComputerUseError):
    """Raised when browser session management fails.

    Covers errors during session save, load, restoration, or expiry — for
    example a corrupt session file, an expired session that cannot be
    refreshed, or a mismatch between the stored domain and the active page.
    """

    def __init__(self, message: str = "Session management failed") -> None:
        super().__init__(message)


class APIError(ComputerUseError):
    """Raised when a cloud API call returns an error response.

    Carries the HTTP ``status_code`` and the raw ``response`` body so
    callers can inspect the upstream error without re-parsing it.
    Typical sources include the Anthropic API, BrowserBase, or any other
    third-party service the SDK communicates with.
    """

    def __init__(
        self,
        message: str = "API request failed",
        status_code: Optional[int] = None,
        response: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status_code = status_code
        self.response = response
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["status_code"] = self.status_code
        base["response"] = self.response
        return base

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(message={self.message!r}, "
            f"status_code={self.status_code!r})"
        )
