import pytest


@pytest.fixture
def test_db_url():
    """Placeholder: returns test database URL."""
    return "postgresql+asyncpg://postgres:postgres@localhost:5432/computeruse_test"


@pytest.fixture
def test_redis_url():
    """Placeholder: returns test Redis URL."""
    return "redis://localhost:6379/1"
