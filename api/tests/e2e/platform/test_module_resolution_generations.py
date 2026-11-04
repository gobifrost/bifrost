"""Real Redis contracts for resolver generations and synchronous import readers."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import src.core.module_cache as module_cache
from src.core.module_cache_contract import (
    MODULE_RESOLUTION_GENERATION_INDEX_KEY,
    MODULE_RESOLUTION_KEY_PREFIX,
    MODULE_RESOLUTION_INDEX_KEY,
    module_resolution_generation_key,
)
from src.core.redis_client import get_redis_client

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_generation_fences_inflight_write_and_expired_token():
    name = f"generation_test_{uuid4().hex}"
    key = f"-:0:{name}"
    redis = await get_redis_client()._get_redis()
    result_key = f"{MODULE_RESOLUTION_KEY_PREFIX}{key}"
    generation_key = module_resolution_generation_key(key)
    result = {"kind": "namespace", "path": name}
    try:
        old = await module_cache.get_module_resolution_generation(key)
        await module_cache.invalidate_module_resolution_cache(f"{name}.py")
        await module_cache.set_module_resolution_cache(key, result, generation=old)
        assert await redis.get(result_key) is None

        current = await module_cache.get_module_resolution_generation(key)
        assert current != old
        await module_cache.set_module_resolution_cache(key, result, generation=current)
        assert await module_cache.get_module_resolution_cache(key) == result
        assert 0 < await redis.ttl(result_key) <= 86400
        assert 0 < await redis.ttl(generation_key) <= 86400

        # Token expiration must never make an old result current again (ABA).
        await redis.delete(generation_key)
        assert await module_cache.get_module_resolution_cache(key) is None
        renewed = await module_cache.get_module_resolution_generation(key)
        assert renewed not in {old, current}
        assert await module_cache.get_module_resolution_cache(key) is None
        await module_cache.set_module_resolution_cache(key, result, generation=current)
        assert json.loads(await redis.get(result_key))["generation"] == current
        assert await module_cache.get_module_resolution_cache(key) is None

        await module_cache.set_module_resolution_cache(
            key, {"kind": "not_found", "path": name}, generation=renewed,
        )
        assert 0 < await redis.ttl(result_key) <= 86400
    finally:
        await redis.delete(result_key, generation_key)


async def test_bounded_metadata_evicts_old_generation_without_resurrecting_its_result(monkeypatch):
    """Real Redis eviction keeps stale in-flight probes from repopulating metadata."""

    monkeypatch.setattr(module_cache, "MODULE_RESOLUTION_MAX_ENTRIES", 2)
    names = [f"bounded_generation_{uuid4().hex}" for _ in range(3)]
    keys = [f"-:0:{name}" for name in names]
    generation_keys = [module_resolution_generation_key(key) for key in keys]
    result_keys = [f"{MODULE_RESOLUTION_KEY_PREFIX}{key}" for key in keys]
    redis = await get_redis_client()._get_redis()
    stale_result = {"kind": "not_found", "path": names[0]}
    try:
        stale_generation = await module_cache.get_module_resolution_generation(keys[0])
        await module_cache.get_module_resolution_generation(keys[1])
        await module_cache.get_module_resolution_generation(keys[2])

        assert await redis.zcard(MODULE_RESOLUTION_GENERATION_INDEX_KEY) <= 2
        assert await redis.get(generation_keys[0]) is None

        # A probe that started before eviction has no valid generation to write
        # against. It cannot bring a 24-hour miss back from the dead.
        await module_cache.set_module_resolution_cache(
            keys[0], stale_result, generation=stale_generation,
        )
        assert await redis.get(result_keys[0]) is None

        current_generation = await module_cache.get_module_resolution_generation(keys[0])
        await module_cache.set_module_resolution_cache(
            keys[0], stale_result, generation=current_generation,
        )
        assert await module_cache.get_module_resolution_cache(keys[0]) == stale_result
        assert await redis.zcard(MODULE_RESOLUTION_INDEX_KEY) <= 2
        assert 0 < await redis.ttl(result_keys[0]) <= 86400
    finally:
        await redis.delete(*generation_keys, *result_keys)
        await redis.zrem(MODULE_RESOLUTION_GENERATION_INDEX_KEY, *generation_keys)
        await redis.zrem(MODULE_RESOLUTION_INDEX_KEY, *result_keys)


async def test_cold_source_read_cannot_recache_after_invalidation_and_generation_eviction():
    """An old S3 response may finish, but it cannot restore executable source."""

    path = f"race_source_{uuid4().hex}.py"
    key = f"bifrost:module:{path}"
    cache_key = f"-:0:{path.removesuffix('.py')}"
    generation_key = module_resolution_generation_key(cache_key)
    redis = await get_redis_client()._get_redis()
    read_started = asyncio.Event()
    allow_read = asyncio.Event()

    async def delayed_read(_path):
        read_started.set()
        await allow_read.wait()
        return b"VALUE = 'old'\n"

    storage = MagicMock()
    storage.read = AsyncMock(side_effect=delayed_read)
    try:
        with patch("src.core.module_cache.RepoStorage", return_value=storage):
            read_task = asyncio.create_task(module_cache.get_module(path))
            await read_started.wait()
            await module_cache.invalidate_module(path)
            # The cardinality cap may evict the new token too. UUID fencing
            # still rejects the pre-invalidation writer rather than accepting
            # an absent token as equivalent.
            await redis.delete(generation_key)
            allow_read.set()
            assert (await read_task)["content"] == "VALUE = 'old'\n"

        assert await redis.get(key) is None
    finally:
        await redis.delete(key, generation_key)
        await redis.zrem(MODULE_RESOLUTION_GENERATION_INDEX_KEY, generation_key)


async def test_sync_reader_observes_cross_scope_generation_rotation():
    from src.core import module_cache_sync as sync

    name = f"generation_test_{uuid4().hex}"
    key = f"solution-test:1:{name}"
    redis = await get_redis_client()._get_redis()
    sync.set_solution_context("solution-test", global_repo_access=True)
    try:
        generation = await module_cache.get_module_resolution_generation(key)
        await module_cache.set_module_resolution_cache(
            key, {"kind": "namespace", "path": name}, generation=generation,
        )
        assert sync._get_cached_module_resolution(name).kind == "namespace"
        with patch.object(redis, "scan", side_effect=AssertionError("routine scan")):
            await module_cache.invalidate_module_resolution_cache(f"_solutions/other/{name}/__init__.py")
        assert sync._get_cached_module_resolution(name) is None
    finally:
        sync.clear_solution_context()
        sync.reset_sync_redis()
        await redis.delete(f"{MODULE_RESOLUTION_KEY_PREFIX}{key}", module_resolution_generation_key(key))
