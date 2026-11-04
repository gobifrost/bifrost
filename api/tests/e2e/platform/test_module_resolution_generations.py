"""Real Redis contracts for resolver generations and synchronous import readers."""
import asyncio
import json
from typing import Awaitable, cast
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


@pytest.mark.parametrize("solution", [False, True], ids=["workspace", "solution"])
@pytest.mark.parametrize("mutation", ["create", "update", "delete"])
async def test_first_import_observes_source_mutation_after_shared_cache_warmup(solution, mutation):
    """Real storage/cache mutations after a shared resolver entry was warmed."""
    import importlib
    import sys

    import httpx

    from shared.sdk_modules import ModuleSourceScope, resolve_module_name
    from src.core import module_cache_sync as sync
    from src.services.execution.virtual_import import VirtualModuleFinder
    from src.services.repo_storage import RepoStorage
    from src.services.solutions.storage import SolutionStorage

    solution_id = str(uuid4()) if solution else None
    storage = SolutionStorage(solution_id) if solution_id else RepoStorage()
    name = f"fresh_import_{uuid4().hex}"
    path = f"{name}.py"
    storage_path = storage._key(path) if solution else path
    scope = ModuleSourceScope(solution_id=solution_id, global_repo_access=False)
    loop = asyncio.get_running_loop()

    async def write_source(value):
        source = f"VALUE = {value}\n"
        digest = await storage.write(path, source.encode())
        await module_cache.set_module(storage_path, source, digest)

    async def request(method, route, **kwargs):
        return httpx.Response(200, json=await resolve_module_name(kwargs["params"]["name"], scope=scope))

    client = MagicMock()
    client.engine_request_sync.side_effect = lambda *args, **kwargs: asyncio.run_coroutine_threadsafe(
        request(*args, **kwargs), loop,
    ).result()

    async def mutate():
        if mutation == "delete":
            await storage.delete(path)
            await module_cache.invalidate_module(storage_path)
        else:
            await write_source(2)

    def execute():
        if solution_id:
            sync.set_solution_context(solution_id, False)
        else:
            sync.clear_solution_context()
        finder = VirtualModuleFinder()
        sys.meta_path.insert(0, finder)
        try:
            with patch.object(sync, "_get_engine_client", return_value=client):
                client.engine_request_sync("GET", "/api/sdk/modules-resolve", params={"name": name})
                asyncio.run_coroutine_threadsafe(mutate(), loop).result()
                if mutation == "delete":
                    with pytest.raises(ModuleNotFoundError):
                        importlib.import_module(name)
                else:
                    assert importlib.import_module(name).VALUE == 2
            assert [call.args[0] for call in client.engine_request_sync.call_args_list] == ["GET", "GET"]
        finally:
            sys.meta_path.remove(finder)
            sys.modules.pop(name, None)
            sync.clear_solution_context()

    try:
        if mutation != "create":
            await write_source(1)
        await asyncio.to_thread(execute)
    finally:
        await storage.delete(path)
        await module_cache.invalidate_module(storage_path)


async def test_rolling_upgrade_writers_share_the_generation_namespace():
    """Pre-upgrade writes invalidate new misses, and new writes fence old hits."""
    name = f"rolling_generation_{uuid4().hex}"
    key = f"-:0:{name}"
    legacy_generation = f"bifrost:module:resolution:generation:{name}"
    legacy_result = f"bifrost:module:resolution:v2:{key}"
    current_result = f"{MODULE_RESOLUTION_KEY_PREFIX}{key}"
    redis = await get_redis_client()._get_redis()
    try:
        generation = await module_cache.get_module_resolution_generation(key)
        await module_cache.set_module_resolution_cache(
            key, {"kind": "not_found", "path": name}, generation=generation,
        )
        # This is the exact invalidation operation used by the prior release.
        rotated = uuid4().hex
        await redis.set(legacy_generation, rotated, ex=86400)
        assert await module_cache.get_module_resolution_cache(key) is None

        await redis.set(legacy_result, json.dumps({
            "generation": rotated, "result": {"kind": "namespace", "path": name},
        }), ex=86400)
        await module_cache.invalidate_module_resolution_cache(f"{name}.py")
        from src.core.module_cache_contract import MODULE_RESOLUTION_READ_SCRIPT
        assert await cast(Awaitable[object], redis.eval(
            MODULE_RESOLUTION_READ_SCRIPT, 2, legacy_generation, legacy_result,
        )) is None
    finally:
        await redis.delete(legacy_generation, legacy_result, current_result)
        await redis.zrem(MODULE_RESOLUTION_GENERATION_INDEX_KEY, legacy_generation)
        await redis.zrem(MODULE_RESOLUTION_INDEX_KEY, current_result)
