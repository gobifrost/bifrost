#!/usr/bin/env python3
"""
Init container script for Bifrost.

Runs before API and workers start:
1. Run database migrations (alembic upgrade head)
2. Warm Redis requirements cache from database

Usage:
    python -m scripts.init_container

Exit codes:
    0 - Success
    1 - Migration failure
    2 - Requirements cache warming failure
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [init] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("init_container")


async def database_is_ahead() -> bool:
    """True when a newer release already migrated past this image's history.

    Starting an older image (a rollback) against that database must not run
    ``alembic upgrade``, which cannot locate the newer revision. The API and
    executors accept a newer schema (``shared.schema_gate``).
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from shared.schema_gate import revisions_from_a_newer_image
    from src.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        newer = await revisions_from_a_newer_image(engine)
    finally:
        await engine.dispose()
    if newer:
        logger.warning(
            f"Database is at {sorted(newer)}, newer than this image; skipping migrations"
        )
    return bool(newer)


def run_migrations() -> bool:
    """
    Run alembic migrations.

    Returns:
        True if migrations succeeded, False otherwise
    """
    logger.info("Running database migrations...")

    # Get the api/ directory (parent of scripts/)
    api_dir = Path(__file__).parent.parent

    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=api_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,  # 5 minute timeout
        )

        # Log migration output
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    logger.info(f"alembic: {line}")

        logger.info("Database migrations completed successfully")
        return True

    except subprocess.TimeoutExpired:
        logger.error("Migration timed out after 5 minutes")
        return False

    except subprocess.CalledProcessError as e:
        logger.error(f"Migration failed with exit code {e.returncode}")
        if e.stderr:
            for line in e.stderr.strip().split("\n"):
                logger.error(f"alembic: {line}")
        if e.stdout:
            for line in e.stdout.strip().split("\n"):
                logger.info(f"alembic: {line}")
        return False

    except FileNotFoundError:
        logger.error("alembic command not found - ensure it's installed")
        return False


async def warm_requirements_cache() -> bool:
    """
    Warm Redis requirements cache from database.

    Loads requirements.txt content from file_index table into Redis cache
    so workers can install packages without database access during startup.

    Returns:
        True if requirements.txt was cached, False if not found

    Raises:
        Exception: If cache warming fails
    """
    logger.info("Warming requirements cache from S3...")

    try:
        from src.core.requirements_cache import warm_requirements_cache as _warm_cache

        found = await _warm_cache()
        if found:
            logger.info("Requirements cache warmed successfully")
        else:
            logger.info("No requirements.txt found in S3 (cache empty)")
        return found

    except ImportError as e:
        logger.error(f"Failed to import requirements_cache: {e}")
        raise

    except Exception as e:
        logger.error(f"Failed to warm requirements cache: {e}")
        raise


async def main() -> int:
    """
    Main entry point for init container.

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    logger.info("=" * 60)
    logger.info("Bifrost Init Container Starting")
    logger.info("=" * 60)

    # Step 1: Run migrations
    logger.info("")
    logger.info("Step 1/2: Database Migrations")
    logger.info("-" * 40)

    if await database_is_ahead():
        migrations = "Skipped (database is newer than this image)"
    elif run_migrations():
        migrations = "Applied"
    else:
        logger.error("FAILED: Database migrations failed - aborting startup")
        return 1

    # Step 2: Warm requirements cache
    logger.info("")
    logger.info("Step 2/2: Requirements Cache Warming")
    logger.info("-" * 40)

    try:
        requirements_found = await warm_requirements_cache()
    except Exception as e:
        logger.error(f"FAILED: Requirements cache warming failed - {e}")
        return 2

    # Success
    logger.info("")
    logger.info("=" * 60)
    logger.info("Init Container Completed Successfully")
    logger.info(f"  - Migrations: {migrations}")
    logger.info(f"  - Requirements cache: {'cached' if requirements_found else 'empty'}")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
