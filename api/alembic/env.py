"""
Alembic Migration Environment

This file configures Alembic for database migrations.
It uses the SQLAlchemy models and settings from the application.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.config import get_settings
from src.core.database import Base

# Import all models so they're registered with Base.metadata
from src.models import (  # noqa: F401
    Organization,
    User,
    Role,
    RoleAssignment,
    Form,
    FormRole,
    Execution,
    ExecutionLog,
    CLISession,
    Config,
    Workflow,
    OAuthProvider,
    OAuthToken,
    AuditLog,
    UserMFAMethod,
    MFARecoveryCode,
    TrustedDevice,
    UserOAuthAccount,
    GlobalBranding,
    # Applications (App Builder)
    Application,
    AppRole,
)

# Alembic Config object
config = context.config

# Setup logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def get_url() -> str:
    """Get sync database URL from settings (for offline mode)."""
    settings = get_settings()
    return settings.database_url_sync


def get_async_url() -> str:
    """Get async database URL from settings (for online mode)."""
    settings = get_settings()
    return settings.database_url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations using provided connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations in 'online' mode with async engine.

    In this scenario we need to create an Engine and associate a
    connection with the context.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_async_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
