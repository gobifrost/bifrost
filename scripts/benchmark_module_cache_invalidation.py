"""Compare actual baseline and current module writers against isolated Redis.

Run inside the worktree API container. Supply the baseline module_cache.py and
JSON list of package-relative Python paths. Uses only empty Redis DB 15 on the
Compose service named redis; refuses a nonempty database and cleans owned keys.
This measures cache publication, not builds, S3, database writes or finalization.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

from redis.asyncio import Redis

from src.core import module_cache
from src.core.module_cache_contract import module_resolution_generation_key


class MeasuredRedis(Redis):
    scan_calls: int = 0
    scan_seconds: float = 0

    async def scan(self, *args, **kwargs):
        start = time.perf_counter()
        result = await super().scan(*args, **kwargs)
        self.scan_calls += 1
        self.scan_seconds += time.perf_counter() - start
        return result

    async def _get_redis(self):
        return self


def baseline_writer(source: str, redis: MeasuredRedis):
    # Compile the actual baseline functions, using the current unchanged imports.
    selected = {
        "_scan_keys",
        "_resolver_names_for_path",
        "invalidate_module_resolution_cache",
        "set_module",
    }
    tree = ast.parse(source)
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in selected
    ]
    assert len(tree.body) == len(selected)
    namespace = dict(vars(module_cache), get_redis_client=lambda: redis)
    exec(compile(tree, "<baseline module_cache.py>", "exec"), namespace)
    return namespace["set_module"]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--paths", type=Path, required=True)
    parser.add_argument("--keys", type=int, default=90_000)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--baseline-rounds", type=int, default=1)
    args = parser.parse_args()
    assert args.keys > 0 and args.rounds > 0
    assert 0 <= args.baseline_rounds <= args.rounds
    paths = [
        f"_solutions/benchmark/{path}" for path in json.loads(args.paths.read_text())
    ]
    names = set().union(
        *(module_cache._resolver_names_for_path(path) for path in paths)
    )
    resolver_keys = [
        f"{module_cache.MODULE_RESOLUTION_KEY_PREFIX}{scope}:{name}"
        for scope in ["-:0", "-:1", "benchmark:0", "another-solution:1"]
        for name in sorted(names | {"preserved.neighbor"})
    ]
    expected_survivors = {
        key for key in resolver_keys if key.endswith(":preserved.neighbor")
    }
    unrelated = [f"module-cache-benchmark:{i}" for i in range(args.keys)]
    sources = [f"{module_cache.MODULE_KEY_PREFIX}{path}" for path in paths]
    generation_keys = [
        module_resolution_generation_key(key) for key in resolver_keys
    ]
    owned = unrelated + resolver_keys + generation_keys + sources + [module_cache.MODULE_INDEX_KEY]
    redis = MeasuredRedis(host="redis", port=6379, db=15, decode_responses=True)
    if await redis.dbsize():
        await redis.aclose()
        raise RuntimeError("Benchmark requires empty Compose Redis DB 15")
    old = baseline_writer(args.baseline_source.read_text(), redis)
    try:
        for offset in range(0, len(unrelated), 1000):
            async with redis.pipeline(transaction=False) as pipeline:
                for key in unrelated[offset : offset + 1000]:
                    pipeline.set(key, "{}", ex=3600)
                await pipeline.execute()
        # Source/index writes must not change the keyspace between measurements.
        async with redis.pipeline(transaction=False) as pipeline:
            for key in sources:
                pipeline.set(key, "{}", ex=3600)
            pipeline.sadd(module_cache.MODULE_INDEX_KEY, *paths)
            pipeline.expire(module_cache.MODULE_INDEX_KEY, 3600)
            await pipeline.execute()
        print(
            json.dumps(
                {
                    "event": "setup",
                    "python_files": len(paths),
                    "affected_name_updates": sum(
                        len(module_cache._resolver_names_for_path(p)) for p in paths
                    ),
                    "new_traversals": 0,
                    "unrelated_keys": args.keys,
                    "resolver_keys": len(resolver_keys),
                }
            ),
            flush=True,
        )
        for round_number in range(args.rounds):
            # Alternate order to reduce timing bias across repeated rounds.
            writers = [("new", module_cache.set_module)]
            if round_number < args.baseline_rounds:
                writers.append(("old", old))
            if round_number % 2:
                writers.reverse()
            for label, writer in writers:
                with patch.object(module_cache, "get_redis_client", return_value=redis):
                    for key in resolver_keys:
                        cache_key = key.removeprefix(module_cache.MODULE_RESOLUTION_KEY_PREFIX)
                        generation = await module_cache.get_module_resolution_generation(cache_key)
                        await module_cache.set_module_resolution_cache(
                            cache_key, {"kind": "namespace", "path": "benchmark"},
                            generation=generation,
                        )
                redis.scan_calls = 0
                redis.scan_seconds = 0
                start = time.perf_counter()
                with patch.object(module_cache, "get_redis_client", return_value=redis):
                    for i, path in enumerate(paths, 1):
                        await writer(path, "VALUE = 2", "benchmark-hash")
                        if i % 10 == 0:
                            print(
                                json.dumps(
                                    {
                                        "event": "progress",
                                        "version": label,
                                        "files": i,
                                        "elapsed_seconds": round(
                                            time.perf_counter() - start, 3
                                        ),
                                    }
                                ),
                                flush=True,
                            )
                elapsed = time.perf_counter() - start
                workload_calls = redis.scan_calls
                scan_seconds = redis.scan_seconds
                # Generation invalidation leaves expiring values physically
                # present; correctness is the shared reader's visible result.
                survivors = set()
                with patch.object(module_cache, "get_redis_client", return_value=redis):
                    for key in resolver_keys:
                        cache_key = key.removeprefix(module_cache.MODULE_RESOLUTION_KEY_PREFIX)
                        if await module_cache.get_module_resolution_cache(cache_key) is not None:
                            survivors.add(key)
                assert survivors == expected_survivors
                assert await redis.get(unrelated[0]) == "{}"
                assert await redis.get(unrelated[-1]) == "{}"
                assert await redis.get(sources[-1]) is not None
                print(
                    json.dumps(
                        {
                            "event": "result",
                            "version": label,
                            "round": round_number + 1,
                            "seconds": round(elapsed, 4),
                            "scan_calls": workload_calls,
                            "scan_seconds": round(scan_seconds, 4),
                            "correctness": "passed",
                        }
                    ),
                    flush=True,
                )
    finally:
        for offset in range(0, len(owned), 1000):
            await redis.delete(*owned[offset : offset + 1000])
        remaining = await redis.dbsize()
        await redis.aclose()
        print(json.dumps({"event": "cleanup", "remaining_keys": remaining}), flush=True)
        assert remaining == 0


if __name__ == "__main__":
    asyncio.run(main())
