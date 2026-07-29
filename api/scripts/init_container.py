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

    Cache warming is best-effort: startup must not fail just because
    requirements.txt is absent or object storage is unavailable.
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
        return False

    except Exception as e:
        logger.error(f"Failed to warm requirements cache: {e}")
        return False


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

    if not run_migrations():
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
    logger.info("  - Migrations: Applied")
    logger.info(f"  - Requirements cache: {'cached' if requirements_found else 'empty'}")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
