"""Real Redis contracts for resolver generations and synchronous import readers."""
import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.core.module_cache import (
    get_module_resolution_cache,
    get_module_resolution_generation,
    invalidate_module_resolution_cache,
    set_module_resolution_cache,
)
from src.core.module_cache_contract import (
    MODULE_RESOLUTION_KEY_PREFIX,
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
        old = await get_module_resolution_generation(key)
        await invalidate_module_resolution_cache(f"{name}.py")
        await set_module_resolution_cache(key, result, generation=old)
        assert await redis.get(result_key) is None

        current = await get_module_resolution_generation(key)
        assert current != old
        await set_module_resolution_cache(key, result, generation=current)
        assert await get_module_resolution_cache(key) == result
        assert 0 < await redis.ttl(result_key) <= 86400
        assert 0 < await redis.ttl(generation_key) <= 86400

        # Token expiration must never make an old result current again (ABA).
        await redis.delete(generation_key)
        assert await get_module_resolution_cache(key) is None
        renewed = await get_module_resolution_generation(key)
        assert renewed not in {old, current}
        assert await get_module_resolution_cache(key) is None
        await set_module_resolution_cache(key, result, generation=current)
        assert json.loads(await redis.get(result_key))["generation"] == current
        assert await get_module_resolution_cache(key) is None

        await set_module_resolution_cache(
            key, {"kind": "not_found", "path": name}, generation=renewed,
        )
        assert 0 < await redis.ttl(result_key) <= 30
    finally:
        await redis.delete(result_key, generation_key)


async def test_sync_reader_observes_cross_scope_generation_rotation():
    from src.core import module_cache_sync as sync

    name = f"generation_test_{uuid4().hex}"
    key = f"solution-test:1:{name}"
    redis = await get_redis_client()._get_redis()
    sync.set_solution_context("solution-test", global_repo_access=True)
    try:
        generation = await get_module_resolution_generation(key)
        await set_module_resolution_cache(
            key, {"kind": "namespace", "path": name}, generation=generation,
        )
        assert sync._get_cached_module_resolution(name).kind == "namespace"
        with patch.object(redis, "scan", side_effect=AssertionError("routine scan")):
            await invalidate_module_resolution_cache(f"_solutions/other/{name}/__init__.py")
        assert sync._get_cached_module_resolution(name) is None
    finally:
        sync.clear_solution_context()
        sync.reset_sync_redis()
        await redis.delete(f"{MODULE_RESOLUTION_KEY_PREFIX}{key}", module_resolution_generation_key(key))
