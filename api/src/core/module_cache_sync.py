"""
Synchronous Redis client for import hook.

Python's import system runs synchronously - we need sync Redis access
for the MetaPathFinder to fetch modules during import.

This module provides synchronous versions of the cache functions
specifically for use in virtual_import.py's MetaPathFinder.

When a cache miss occurs, we fall back to object storage to fetch the module
and re-cache it in Redis. This provides self-healing behavior when:
- Redis cache entries expire (24hr TTL)
- Redis restarts or evicts keys
- Cache warming at startup was incomplete
"""

import hashlib
import json
import logging
import os
from functools import lru_cache
from typing import Any

import redis

from src.core.module_cache import MODULE_INDEX_KEY, MODULE_KEY_PREFIX, CachedModule

logger = logging.getLogger(__name__)

# TTL for cached modules (24 hours)
MODULE_CACHE_TTL = 86400

REPO_PREFIX = "_repo/"

# Cached object storage clients — reused across calls to avoid repeated setup
_s3_client: Any = None
_s3_available: bool | None = None
_blob_container_client: Any = None
_blob_available: bool | None = None


def _object_storage_provider() -> str:
    return os.environ.get("BIFROST_OBJECT_STORAGE_PROVIDER", "s3").lower()


@lru_cache(maxsize=1)
def _get_sync_redis() -> Any:
    """
    Get synchronous Redis client.

    Uses lru_cache to reuse connection across imports.
    """
    return redis.Redis.from_url(
        os.environ.get("BIFROST_REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def _get_s3_client() -> Any:
    """
    Get or create a sync S3 client using botocore (always available via aiobotocore).
    """
    global _s3_client, _s3_available

    if _s3_available is False:
        return None

    if _s3_client is not None:
        return _s3_client

    endpoint_url = os.environ.get("BIFROST_S3_ENDPOINT_URL")
    access_key = os.environ.get("BIFROST_S3_ACCESS_KEY")
    secret_key = os.environ.get("BIFROST_S3_SECRET_KEY")
    region = os.environ.get("BIFROST_S3_REGION", "us-east-1")

    if not all([access_key, secret_key]):
        logger.debug("S3 not configured, skipping S3 fallback")
        _s3_available = False
        return None

    try:
        import botocore.session  # type: ignore[import-untyped]

        session = botocore.session.get_session()
        client = session.create_client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        _s3_client = client
        _s3_available = True
        return client
    except Exception:
        _s3_available = False
        logger.debug("botocore not available, S3 fallback disabled")
        return None


def _get_s3_module(path: str) -> bytes | None:
    """
    Fetch a module from S3 _repo/ prefix (synchronous).

    Uses botocore sync client since this runs in worker subprocesses.
    Returns raw bytes or None if not found.
    """
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return None

    client = _get_s3_client()
    if client is None:
        return None

    try:
        key = f"{REPO_PREFIX}{path}"
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    except Exception as e:
        # Check for S3 NoSuchKey specifically
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            code = resp.get("Error", {}).get("Code", "")
            if code == "NoSuchKey":
                logger.debug(f"Module not found in S3: {path}")
                return None
        logger.warning(f"S3 fallback error for {path}: {e}")
        return None


def _get_blob_container_client() -> Any:
    """
    Get or create a sync Azure Blob container client.

    The import hook runs in synchronous worker subprocesses, so this cannot use
    the async RepoStorage adapter.
    """
    global _blob_container_client, _blob_available

    if _blob_available is False:
        return None

    if _blob_container_client is not None:
        return _blob_container_client

    account_url = os.environ.get("BIFROST_AZURE_BLOB_ACCOUNT_URL")
    container = os.environ.get("BIFROST_AZURE_BLOB_CONTAINER")
    auth = os.environ.get("BIFROST_AZURE_BLOB_AUTH", "default_credential")
    account_key = os.environ.get("BIFROST_AZURE_BLOB_ACCOUNT_KEY")

    if not account_url or not container:
        logger.debug("Azure Blob not configured, skipping Blob fallback")
        _blob_available = False
        return None

    try:
        from azure.storage.blob import BlobServiceClient

        credential: Any
        if auth == "account_key":
            if not account_key:
                logger.debug("Azure Blob account key missing, skipping Blob fallback")
                _blob_available = False
                return None
            credential = account_key
        elif auth == "default_credential":
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        else:
            logger.warning(f"Unsupported Azure Blob auth mode: {auth}")
            _blob_available = False
            return None

        service_client = BlobServiceClient(account_url, credential=credential)
        _blob_container_client = service_client.get_container_client(container)
        _blob_available = True
        return _blob_container_client
    except Exception as e:
        _blob_available = False
        logger.warning(f"Azure Blob fallback unavailable: {e}")
        return None


def _get_blob_module(path: str) -> bytes | None:
    """
    Fetch a module from Azure Blob _repo/ prefix (synchronous).
    """
    client = _get_blob_container_client()
    if client is None:
        return None

    key = f"{REPO_PREFIX}{path}"
    try:
        return client.download_blob(key).readall()
    except Exception as e:
        error_code = getattr(e, "error_code", "")
        if error_code == "BlobNotFound":
            logger.debug(f"Module not found in Azure Blob: {path}")
            return None
        logger.warning(f"Azure Blob fallback error for {path}: {e}")
        return None


def _get_object_storage_module(path: str) -> bytes | None:
    if _object_storage_provider() == "azure_blob":
        return _get_blob_module(path)
    return _get_s3_module(path)


def get_module_sync(path: str) -> CachedModule | None:
    """
    Fetch a single module from cache (synchronous).

    Called by VirtualModuleFinder.find_spec() during import resolution.

    Lookup order:
    1. Redis cache (fast path)
    2. Object storage _repo/ (fallback, re-caches to Redis)
    3. None (module not found)
    """
    try:
        client = _get_sync_redis()
        key = f"{MODULE_KEY_PREFIX}{path}"
        data = client.get(key)
        if data:
            return json.loads(data)

        # Redis miss — try object storage fallback
        storage_content = _get_object_storage_module(path)
        if storage_content is not None:
            try:
                content_str = storage_content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(
                    f"Could not decode object storage module as UTF-8: {path}"
                )
                return None

            content_hash = hashlib.sha256(storage_content).hexdigest()
            module: CachedModule = {
                "content": content_str,
                "path": path,
                "hash": content_hash,
            }

            # Cache back to Redis
            try:
                client.setex(key, MODULE_CACHE_TTL, json.dumps(module))
                # Also add to module index
                client.sadd(MODULE_INDEX_KEY, path)
            except redis.RedisError as e:
                logger.warning(f"Failed to cache S3 module to Redis: {e}")

            return module

        logger.debug(f"Module not in cache or object storage: {path}")
        return None

    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module {path}: {e}")
        return None


def _list_s3_modules() -> set[str]:
    """
    List all Python module paths in S3 _repo/ (synchronous).

    Used as a fallback when the Redis module index is empty, which can happen
    after Redis restarts or cache eviction. Returns paths relative to _repo/
    (e.g. "features/spotify_journal/services/spotify_api.py").
    """
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return set()

    client = _get_s3_client()
    if client is None:
        return set()

    paths: set[str] = set()
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=REPO_PREFIX):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                if key.endswith(".py"):
                    # Strip the _repo/ prefix to get the relative path
                    paths.add(key[len(REPO_PREFIX) :])
    except Exception as e:
        logger.warning(f"S3 list error when rebuilding module index: {e}")

    return paths


def _list_blob_modules() -> set[str]:
    """
    List all Python module paths in Azure Blob _repo/ (synchronous).
    """
    client = _get_blob_container_client()
    if client is None:
        return set()

    paths: set[str] = set()
    try:
        for blob in client.list_blobs(name_starts_with=REPO_PREFIX):
            key: str = blob.name
            if key.endswith(".py"):
                paths.add(key[len(REPO_PREFIX) :])
    except Exception as e:
        logger.warning(f"Azure Blob list error when rebuilding module index: {e}")

    return paths


def _list_object_storage_modules() -> set[str]:
    if _object_storage_provider() == "azure_blob":
        return _list_blob_modules()
    return _list_s3_modules()


def get_module_index_sync() -> set[str]:
    """
    Get all cached module paths (synchronous).

    When the Redis index is empty (cold cache after restart or eviction),
    falls back to listing S3 and repopulates Redis so subsequent calls are fast.
    """
    try:
        client = _get_sync_redis()
        paths = client.smembers(MODULE_INDEX_KEY)
        if paths:
            return {p if isinstance(p, str) else p.decode() for p in paths}

        # Redis index is empty — could be cold cache. Try object storage.
        logger.debug(
            "Module index empty in Redis, falling back to object storage listing"
        )
        storage_paths = _list_object_storage_modules()
        if storage_paths:
            # Repopulate Redis index so subsequent calls are fast
            try:
                client.sadd(MODULE_INDEX_KEY, *storage_paths)
                client.expire(MODULE_INDEX_KEY, MODULE_CACHE_TTL)
            except redis.RedisError as e:
                logger.warning(f"Failed to repopulate module index in Redis: {e}")
            return storage_paths

        return set()
    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module index: {e}")
        return set()


def reset_sync_redis() -> None:
    """Reset the sync Redis client."""
    _get_sync_redis.cache_clear()


def reset_s3_client() -> None:
    """Reset the cached S3 client. Used for testing."""
    global _s3_client, _s3_available
    _s3_client = None
    _s3_available = None


def reset_blob_client() -> None:
    """Reset the cached Azure Blob client. Used for testing."""
    global _blob_container_client, _blob_available
    _blob_container_client = None
    _blob_available = None
