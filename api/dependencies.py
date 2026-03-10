from collections.abc import AsyncGenerator
from typing import Any


async def get_db() -> AsyncGenerator[Any, None]:
    """Placeholder: yields an async database session."""
    yield None


async def get_redis() -> AsyncGenerator[Any, None]:
    """Placeholder: yields a Redis connection."""
    yield None
