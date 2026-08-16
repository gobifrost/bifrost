"""Small, testable primitives used by the E2E isolation fixtures."""

from typing import Protocol


class ScannableRedis(Protocol):
    async def scan(
        self, cursor: int, *, match: str, count: int
    ) -> tuple[int, list[str]]:
        raise NotImplementedError

    async def delete(self, key: str) -> int:
        raise NotImplementedError


async def clear_redis_module_cache(redis: ScannableRedis) -> None:
    """Delete every cached virtual module without assuming multi-key delete support."""
    cursor = 0
    while True:
        cursor, keys = await redis.scan(
            cursor,
            match="bifrost:module:*",
            count=100,
        )
        for key in keys:
            await redis.delete(key)
        if cursor == 0:
            return
