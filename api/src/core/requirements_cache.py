"""
Requirements.txt Persistence Architecture
=========================================

This module is part of the package persistence system that ensures
installed packages survive container restarts.

Persistence Flow:
1. User installs package via /api/packages/install
2. API handler appends package to requirements, saves to object storage + Redis cache
3. API broadcasts "recycle_workers" to all workers
4. Workers recycle processes; ProcessPoolManager calls install_requirements() at pool startup
5. pip install runs from requirements.txt (shared filesystem, all workers inherit)

Read path: Redis cache → object storage fallback (re-caches on hit)
Source of truth: object storage (_repo/requirements.txt) via RepoStorage

Related Files:
- api/src/routers/packages.py - Saves requirements + broadcasts recycle
- api/scripts/init_container.py - Warms cache on container startup
- api/src/services/execution/simple_worker.py - Installs on worker startup

Key Pattern:
- bifrost:requirements:content - JSON: {content, hash}
"""

import hashlib
import json
import logging
import os
from typing import Any, TypedDict

from src.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

REQUIREMENTS_KEY = "bifrost:requirements:content"
REQUIREMENTS_CACHE_TTL = 86400  # 24 hours


class CachedRequirements(TypedDict):
    """Schema for cached requirements data."""

    content: str
    hash: str


async def get_requirements() -> CachedRequirements | None:
    """
    Fetch requirements.txt content.

    Lookup order:
    1. Redis cache (fast path)
    2. Object storage _repo/requirements.txt (fallback, re-caches to Redis)
    3. None (not found)

    Returns:
        CachedRequirements dict if found, None otherwise
    """
    redis = get_redis_client()
    data = await redis.get(REQUIREMENTS_KEY)
    if data:
        return json.loads(data)

    # Redis miss — fall back to S3
    if await warm_requirements_cache():
        data = await redis.get(REQUIREMENTS_KEY)
        if data:
            return json.loads(data)

    return None


def get_requirements_sync() -> str | None:
    """
    Fetch requirements.txt content (synchronous).

    Used by install_requirements() which runs in a worker thread.
    Same lookup order as get_requirements(): Redis → object storage → None.

    Returns:
        Requirements content string, or None if not found
    """
    from src.core.module_cache_sync import _get_sync_redis

    try:
        client = _get_sync_redis()
        data: str | None = client.get(REQUIREMENTS_KEY)  # type: ignore[assignment]
        if data:
            cached: dict[str, Any] = json.loads(data)
            content = cached.get("content", "")
            if content.strip():
                return content
            return None

        # Redis miss — fall back to the configured object storage provider.
        logger.info("[requirements] Redis cache empty, falling back to object storage")
        content = _read_requirements_from_object_storage()
        if not content:
            return None

        # Re-cache to Redis for next time
        try:
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            cached_data = CachedRequirements(content=content, hash=content_hash)
            client.setex(
                REQUIREMENTS_KEY, REQUIREMENTS_CACHE_TTL, json.dumps(cached_data)
            )
            logger.info(
                "[requirements] Re-cached requirements from object storage to Redis"
            )
        except Exception as e:
            logger.warning(f"[requirements] Failed to re-cache to Redis: {e}")

        return content

    except Exception as e:
        logger.warning(f"[requirements] Error reading requirements: {e}")
        return None


def _object_storage_provider() -> str:
    return os.environ.get("BIFROST_OBJECT_STORAGE_PROVIDER", "s3").lower()


def _read_requirements_from_object_storage() -> str | None:
    if _object_storage_provider() == "azure_blob":
        return _read_requirements_from_blob()
    from src.core.module_cache_sync import _get_s3_client

    return _read_requirements_from_s3(_get_s3_client)


def _read_requirements_from_blob() -> str | None:
    """Read _repo/requirements.txt from Azure Blob using the sync Blob client."""
    from src.core.module_cache_sync import _get_blob_container_client

    client = _get_blob_container_client()
    if client is None:
        return None

    try:
        content = client.download_blob("_repo/requirements.txt").readall().decode()
        if content.strip():
            logger.info("[requirements] Loaded requirements.txt from Azure Blob")
            return content
        return None
    except Exception as e:
        error_code = getattr(e, "error_code", "")
        if error_code == "BlobNotFound":
            logger.info("[requirements] No requirements.txt in Azure Blob")
            return None
        logger.warning(f"[requirements] Azure Blob read error: {e}")
        return None


def _read_requirements_from_s3(get_client_fn: Any) -> str | None:
    """Read _repo/requirements.txt from S3 using a sync botocore client."""
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return None

    client = get_client_fn()
    if client is None:
        return None

    try:
        response = client.get_object(Bucket=bucket, Key="_repo/requirements.txt")
        content = response["Body"].read().decode()
        if content.strip():
            logger.info("[requirements] Loaded requirements.txt from S3")
            return content
        return None
    except Exception as e:
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            code = resp.get("Error", {}).get("Code", "")
            if code == "NoSuchKey":
                logger.info("[requirements] No requirements.txt in S3")
                return None
        logger.warning(f"[requirements] S3 read error: {e}")
        return None


async def set_requirements(content: str, content_hash: str) -> None:
    """
    Cache requirements.txt content.

    Args:
        content: Full requirements.txt content
        content_hash: SHA-256 hash of content (for change detection)
    """
    redis = get_redis_client()
    cached = CachedRequirements(content=content, hash=content_hash)
    await redis.setex(REQUIREMENTS_KEY, REQUIREMENTS_CACHE_TTL, json.dumps(cached))
    logger.debug("Cached requirements.txt")


async def warm_requirements_cache() -> bool:
    """
    Load requirements.txt from object storage into Redis cache.

    Called by init container or API startup to ensure cache is warm.
    Also called before broadcasting recycle when installing from requirements.txt,
    to ensure workers pick up the latest file content.

    Returns:
        True if requirements.txt was cached, False if not found
    """
    from src.services.repo_storage import RepoStorage

    try:
        repo = RepoStorage()
        content_bytes = await repo.read("requirements.txt")
        content = content_bytes.decode()

        if content.strip():
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            await set_requirements(content=content, content_hash=content_hash)
            logger.info("Warmed requirements cache from object storage")
            return True

        logger.info("requirements.txt in object storage is empty")
        return False
    except Exception as e:
        # File may not exist yet in object storage
        logger.info(f"No requirements.txt found in object storage: {e}")
        return False


async def save_requirements(content: str) -> None:
    """
    Save requirements.txt to object storage and update Redis cache.

    Args:
        content: Full requirements.txt content
    """
    from src.services.repo_storage import RepoStorage

    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # Write to object storage (source of truth)
    repo = RepoStorage()
    await repo.write("requirements.txt", content.encode())

    # Update Redis cache (workers read this synchronously at startup)
    await set_requirements(content, content_hash)
    logger.info("Saved requirements.txt to object storage and cache")


def append_package_to_requirements(
    current: str, package: str, version: str | None
) -> tuple[str, bool]:
    """
    Append or update a package in requirements.txt content.

    Args:
        current: Current requirements.txt content
        package: Package name
        version: Optional version specifier

    Returns:
        Tuple of (updated content, is_update) where is_update is True if
        an existing package was updated (needs process recycle to clear
        sys.modules), False if a new package was added.
    """
    lines = current.strip().split("\n") if current.strip() else []
    package_lower = package.lower()
    package_spec = f"{package}=={version}" if version else package

    # Find and update existing entry, or append
    found = False
    for i, line in enumerate(lines):
        # Parse package name from line (handles ==, >=, etc.)
        line_package = (
            line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
        )
        if line_package.lower() == package_lower:
            lines[i] = package_spec
            found = True
            break

    if not found:
        lines.append(package_spec)

    # Filter empty lines and ensure trailing newline
    lines = [line for line in lines if line.strip()]
    return ("\n".join(lines) + "\n" if lines else "", found)


def remove_package_from_requirements(current: str, package: str) -> tuple[str, bool]:
    """Remove a package from requirements.txt content.

    Returns (updated content, was_present).
    """
    lines = current.strip().split("\n") if current.strip() else []
    package_lower = package.lower()
    kept: list[str] = []
    found = False
    for line in lines:
        line_package = (
            line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
        )
        if line_package.lower() == package_lower:
            found = True
            continue
        if line.strip():
            kept.append(line)
    return ("\n".join(kept) + "\n" if kept else "", found)
