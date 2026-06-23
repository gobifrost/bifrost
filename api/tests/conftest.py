"""
Pytest fixtures for Bifrost API testing infrastructure.

This module provides:
1. Database fixtures (PostgreSQL with SQLAlchemy async)
2. Message queue fixtures (RabbitMQ)
3. Cache fixtures (Redis)
4. Authentication/authorization fixtures
"""

import logging
import os
import sys
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import fixture modules
pytest_plugins = [
    "tests.fixtures.auth",
    "tests.e2e.fixtures.setup",  # E2E session fixtures
    "tests.e2e.fixtures.github_setup",  # GitHub E2E fixtures
    "tests.e2e.fixtures.llm_setup",  # LLM E2E fixtures
    "tests.e2e.fixtures.knowledge_setup",  # Knowledge store E2E fixtures
    "tests.e2e.fixtures.entity_setup",  # Entity creation fixtures for portable refs tests
]


# ==================== CONFIGURATION ====================

# Test database URL (prefer env provided by docker-compose; fall back to container hostnames)
TEST_DATABASE_URL = os.getenv(
    "BIFROST_DATABASE_URL",
    "postgresql+asyncpg://bifrost:bifrost_test@pgbouncer:5432/bifrost_test",
)

TEST_DATABASE_URL_SYNC = os.getenv(
    "BIFROST_DATABASE_URL_SYNC",
    "postgresql://bifrost:bifrost_test@pgbouncer:5432/bifrost_test",
)

TEST_RABBITMQ_URL = os.getenv(
    "BIFROST_RABBITMQ_URL",
    "amqp://bifrost:bifrost_test@rabbitmq:5672/",
)

TEST_REDIS_URL = os.getenv(
    "BIFROST_REDIS_URL",
    "redis://redis:6379/0",
)

TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")


# ==================== SESSION FIXTURES (Start Once Per Test Run) ====================
# Note: pytest-asyncio handles event loop management automatically.
# Session-scoped async fixtures will share a loop; function-scoped ones get fresh loops.


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment(tmp_path_factory):
    """Set up test environment variables once per session."""
    # Set environment variables for testing
    os.environ["BIFROST_ENVIRONMENT"] = "testing"
    os.environ["BIFROST_DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["BIFROST_DATABASE_URL_SYNC"] = TEST_DATABASE_URL_SYNC
    os.environ["BIFROST_RABBITMQ_URL"] = TEST_RABBITMQ_URL
    os.environ["BIFROST_REDIS_URL"] = TEST_REDIS_URL
    os.environ["BIFROST_SECRET_KEY"] = "test-secret-key-for-e2e-testing-must-be-32-chars"

    # Set up workspace and temp locations for tests
    # These are now hardcoded paths - create them for test isolation
    test_workspace = Path("/tmp/bifrost/workspace")
    test_temp = Path("/tmp/bifrost/temp")
    test_uploads = Path("/tmp/bifrost/uploads")
    test_workspace.mkdir(parents=True, exist_ok=True)
    test_temp.mkdir(parents=True, exist_ok=True)
    test_uploads.mkdir(parents=True, exist_ok=True)
    os.environ["BIFROST_TEMP_LOCATION"] = str(test_temp)

    # Reset global database state to ensure it uses test settings
    # This is important because shared code (e.g., shared/metrics.py) uses
    # get_session_factory() which relies on global state
    from src.core.database import reset_db_state

    reset_db_state()

    yield

    # Clean up global database state after tests
    reset_db_state()


# ==================== DATABASE FIXTURES ====================


@pytest.fixture(scope="session")
def async_engine():
    """Create async SQLAlchemy engine for test session.

    Uses NullPool to avoid connection pooling issues with pytest-asyncio's
    function-scoped event loops. Each test gets fresh connections.
    """
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )
    yield engine
    # Cleanup handled by test.sh; NullPool ensures no lingering connections


@pytest.fixture(scope="session")
def async_session_factory(async_engine):
    """Create async session factory."""
    return async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture
async def db_session(async_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """
    Provide a database session for each test.

    Each test gets its own session that is rolled back after the test,
    ensuring test isolation.
    """
    async with async_session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(autouse=True)
async def isolate_s3(request) -> AsyncGenerator[None, None]:
    """Wipe .bifrost/ from S3 before every async test that touches the repo.

    Prevents stale manifest files written by one test (via manifest/import's
    ``files`` param or RepoStorage.write) from polluting subsequent tests.
    Only runs when S3 is configured in the test environment.
    """
    # Skip for unit tests — they mock S3 or don't touch it at all.
    if "unit" in request.fspath.strpath:
        yield
        return

    try:
        from src.config import get_settings
        from src.services.repo_storage import RepoStorage

        settings = get_settings()
        if not settings.s3_configured:
            yield
            return

        repo = RepoStorage(settings)
        paths = await repo.list(".bifrost/")
        for path in paths:
            try:
                await repo.delete(path)
            except Exception as e:
                # Best-effort per-path cleanup — failure logged at debug, sweep continues
                logger.debug(f"isolate_s3_manifest could not delete {path}: {e}")
    except Exception as e:
        # S3 not configured / unreachable — fixture is best-effort, skip cleanup
        logger.debug(f"isolate_s3_manifest sweep skipped: {e}")

    yield


@pytest_asyncio.fixture(autouse=True)
async def isolate_redis_module_cache(request) -> AsyncGenerator[None, None]:
    """Flush Redis module-cache keys before every async test.

    Prevents stale bytecode cached by one test from affecting virtual imports
    in subsequent tests. Only runs when Redis is reachable.
    """
    if "unit" in request.fspath.strpath:
        yield
        return

    try:
        from src.core.redis_client import get_redis_client

        redis = get_redis_client()
        # Delete only module-cache keys, not session/rate-limit/pubsub keys
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match="bifrost:module:*", count=100)
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break
    except Exception as e:
        # Redis not reachable — fixture is best-effort
        logger.debug(f"isolate_redis_module_cache sweep skipped: {e}")

    yield


@pytest_asyncio.fixture(autouse=True)
async def isolate_file_policies(request) -> AsyncGenerator[None, None]:
    """Wipe file policies + metadata before every async test that touches them.

    File policies are global session state keyed by (org, location, path). A
    test that grants a broad/root-prefix policy (e.g. a `workspace` root grant
    for a CLI write) would otherwise leak an allow-everything rule into later
    tests — flipping a sibling's default-deny assertion from 403 to 404. Mirror
    the S3/redis isolation fixtures: best-effort sweep, skipped for unit tests.
    """
    if "unit" in request.fspath.strpath:
        yield
        return

    try:
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from src.models.orm.file_metadata import FileMetadata, FilePolicy

        # Use a throwaway NullPool engine bound to THIS test's event loop — the
        # cached app session factory is bound to a different (possibly closed)
        # loop under pytest-asyncio's function-scoped loops, which raises
        # "Event loop is closed". Mirrors the `async_engine` fixture.
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await session.execute(delete(FilePolicy))
                await session.execute(delete(FileMetadata))
                await session.commit()
        finally:
            await engine.dispose()
    except Exception as e:
        # DB not reachable / models unavailable — fixture is best-effort
        logger.debug(f"isolate_file_policies sweep skipped: {e}")

    yield


# ==================== MOCK FIXTURES ====================


@pytest.fixture
def mock_rabbitmq():
    """Mock RabbitMQ connection for unit tests."""
    mock = AsyncMock()
    mock.publish = AsyncMock(return_value=None)
    mock.consume = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def mock_redis():
    """Mock Redis connection for unit tests."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=True)
    mock.expire = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def cleanup_workspace_files():
    """
    Fixture to track and clean up test files created in workspace.
    Yields paths to clean up, then removes them after test completes.
    """
    import shutil

    # Hardcoded workspace path
    workspace_path = Path("/tmp/bifrost/workspace")
    cleanup_paths = []

    def register_cleanup(path: str):
        """Register a path for cleanup."""
        cleanup_paths.append(path)

    yield register_cleanup

    # Cleanup after test
    for path_str in cleanup_paths:
        full_path = workspace_path / path_str
        if full_path.exists():
            if full_path.is_dir():
                shutil.rmtree(full_path)
            else:
                full_path.unlink()

    # Also clean up common test directories
    test_dirs = ["test_files", "test_folders", "test_listings", "forms"]
    for test_dir in test_dirs:
        dir_path = workspace_path / test_dir
        if dir_path.exists():
            # Only remove test files, not the whole directory structure
            for item in dir_path.glob("*"):
                if "test" in item.name.lower() or item.suffix == ".tmp":
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()


# ==================== MARKERS ====================


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "unit: Unit tests (fast, no Docker required)")
    config.addinivalue_line(
        "markers",
        "e2e: End-to-end tests (Docker stack with database, message queue, and services)",
    )
    config.addinivalue_line("markers", "slow: Tests that take >1 second")
