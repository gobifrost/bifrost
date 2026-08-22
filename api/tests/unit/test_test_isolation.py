from unittest.mock import AsyncMock, call

import pytest

from tests.helpers.isolation import clear_redis_module_cache


@pytest.mark.asyncio
async def test_clear_redis_module_cache_scans_all_pages_and_deletes_individually():
    redis = AsyncMock()
    redis.scan.side_effect = [
        (12, ["bifrost:module:first", "bifrost:module:second"]),
        (0, ["bifrost:module:third"]),
    ]

    await clear_redis_module_cache(redis)

    assert redis.scan.await_args_list == [
        call(0, match="bifrost:module:*", count=100),
        call(12, match="bifrost:module:*", count=100),
    ]
    assert redis.delete.await_args_list == [
        call("bifrost:module:first"),
        call("bifrost:module:second"),
        call("bifrost:module:third"),
    ]
