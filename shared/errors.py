from shared.constants import ErrorCode


class ComputerUseError(Exception):
    """Base exception for ComputerUse.dev."""

    def __init__(self, message: str, error_type: ErrorCode) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
