import ssl
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import settings

# Supabase (and most managed Postgres providers) require SSL for external connections.
# asyncpg does not enable SSL by default, so we must pass an ssl context explicitly
# when connecting to a remote host.
_is_remote = "localhost" not in settings.DATABASE_URL and "127.0.0.1" not in settings.DATABASE_URL
_connect_args = {"ssl": ssl.create_default_context()} if _is_remote else {}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
    connect_args=_connect_args,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
