"""
Database Configuration and Session Management

Provides async SQLAlchemy engine and session factory for PostgreSQL.
Uses async connection pooling for optimal performance.
"""

import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import Settings, get_settings
from src.models import Base  # noqa: F401 - imported for Alembic


def _prepare_asyncpg_url(url: str) -> tuple[str, dict]:
    """
    Prepare a database URL for asyncpg compatibility.

    asyncpg doesn't accept 'sslmode' as a URL query parameter - it requires
    SSL to be configured via connect_args instead. This function extracts
    sslmode from the URL and converts it to the appropriate SSL context.

    Args:
        url: PostgreSQL database URL (may contain sslmode parameter)

    Returns:
        Tuple of (cleaned_url without sslmode, connect_args dict with ssl config)

    Example:
        URL with ?sslmode=require -> (URL without param, {"ssl": SSLContext})
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    connect_args: dict = {}

    # Extract and convert sslmode to asyncpg's ssl parameter
    if "sslmode" in query_params:
        sslmode = query_params.pop("sslmode")[0]

        if sslmode in ("require", "verify-ca", "verify-full"):
            # Create SSL context for secure connections
            ssl_context = ssl.create_default_context()

            if sslmode == "require":
                # Don't verify certificate (common for managed databases like DigitalOcean)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            elif sslmode == "verify-ca":
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_REQUIRED
            # verify-full uses default (check_hostname=True, CERT_REQUIRED)

            connect_args["ssl"] = ssl_context
        elif sslmode == "prefer":
            # Try SSL but don't require it
            connect_args["ssl"] = "prefer"
        # "disable" and "allow" don't need ssl parameter

    # Rebuild URL without sslmode
    new_query = urlencode(query_params, doseq=True)
    cleaned_url = urlunparse(parsed._replace(query=new_query))

    return cleaned_url, connect_args


# Global engine and session factory (initialized on startup)
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """
    Get or create the async SQLAlchemy engine.

    Args:
        settings: Optional settings override (for testing)

    Returns:
        AsyncEngine instance
    """
    global _engine

    if _engine is None:
        if settings is None:
            settings = get_settings()

        # Convert sslmode parameter for asyncpg compatibility
        db_url, connect_args = _prepare_asyncpg_url(settings.database_url)

        _engine = create_async_engine(
            db_url,
            echo=settings.debug,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,  # Verify connections before use
            pool_timeout=30,  # Fail fast when pool is exhausted
            pool_recycle=1800,  # Recycle connections every 30 min
            connect_args=connect_args,
        )

    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """
    Get or create the async session factory.

    Args:
        settings: Optional settings override (for testing)

    Returns:
        async_sessionmaker instance
    """
    global _async_session_factory

    if _async_session_factory is None:
        engine = get_engine(settings)
        _async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    return _async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting database sessions in FastAPI routes.

    Yields:
        AsyncSession that is automatically closed after request

    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_optional_db() -> AsyncGenerator[AsyncSession | None, None]:
    """
    Dependency for optional database sessions.

    Use for endpoints that may not always need the database.

    Yields:
        AsyncSession or None
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for getting database sessions outside of FastAPI routes.

    Useful for background tasks, CLI commands, and tests.

    Usage:
        async with get_db_context() as db:
            result = await db.execute(select(User))
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """
    Initialize database connection and verify connectivity.

    Called on application startup.
    """
    engine = get_engine()

    # Test connection
    async with engine.begin() as conn:
        await conn.run_sync(lambda _: None)


async def close_db() -> None:
    """
    Close database connections.

    Called on application shutdown.
    """
    global _engine, _async_session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None


def reset_db_state() -> None:
    """
    Reset database state (for testing).

    Clears the engine and session factory so they are recreated
    with fresh settings on next access.
    """
    global _engine, _async_session_factory
    _engine = None
    _async_session_factory = None
