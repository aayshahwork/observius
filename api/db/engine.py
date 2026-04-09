import ssl
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import settings

# Supabase (and most managed Postgres providers) require SSL for external connections.
# asyncpg does not enable SSL by default, so we must pass an ssl context explicitly
# when connecting to a remote host.
def _detect_remote(url: str) -> bool:
    """Return True only for external hosts that require SSL (e.g. Supabase, RDS).
    Excludes localhost, 127.0.0.1, and bare hostnames like Docker service names (postgres, db).
    """
    import re
    match = re.search(r"@([^:/]+)", url)
    if not match:
        return False
    host = match.group(1)
    if host in ("localhost", "127.0.0.1"):
        return False
    # Bare hostname (no dots) = internal Docker/compose service — no SSL needed
    if "." not in host:
        return False
    return True

_is_remote = _detect_remote(settings.DATABASE_URL)
if _is_remote:
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    _connect_args: dict = {"ssl": _ssl_ctx}
else:
    _connect_args = {}

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
