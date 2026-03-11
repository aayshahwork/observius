from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorCode(StrEnum):
    AUTHENTICATION_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    TASK_TIMEOUT = "task_timeout"
    BROWSER_ERROR = "browser_error"
    INVALID_INPUT = "invalid_input"
    INTERNAL_ERROR = "internal_error"
    QUOTA_EXCEEDED = "quota_exceeded"
    COST_LIMIT_EXCEEDED = "cost_limit_exceeded"


# Tier limits: max concurrent tasks
TIER_LIMITS = {
    "free": {"max_concurrent": 1, "max_steps": 50, "timeout": 120},
    "startup": {"max_concurrent": 5, "max_steps": 200, "timeout": 300},
    "growth": {"max_concurrent": 10, "max_steps": 350, "timeout": 450},
    "enterprise": {"max_concurrent": 20, "max_steps": 500, "timeout": 600},
}

# Monthly step limits per tier (used when upgrading/downgrading)
TIER_STEP_LIMITS: dict[str, int] = {
    "free": 500,
    "startup": 5_000,
    "growth": 25_000,
    "enterprise": 100_000,
}
