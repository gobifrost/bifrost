"""Package mutations stay canonical while physical pip runs in the launcher."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.worker.remote.host import PackageProxy


async def test_package_proxy_recycles_the_remote_template_before_publishing_inventory() -> None:
    redis = SimpleNamespace(hset=AsyncMock())
    pool = SimpleNamespace(
        _started=True,
        worker_id="thin-worker",
        drain_and_restart_template=AsyncMock(),
        _get_redis=AsyncMock(return_value=redis),
    )
    bridge = SimpleNamespace(
        pool=pool,
        request=AsyncMock(return_value={"packages": [{"name": "demo", "version": "1.0"}]}),
    )
    proxy = PackageProxy(bridge)

    await proxy._recycle_workers()
    await proxy._update_pool_packages()

    pool.drain_and_restart_template.assert_awaited_once_with()
    bridge.request.assert_awaited_once_with("package_tool", {"operation": "inventory"})
    redis.hset.assert_awaited_once_with(
        "bifrost:pool:thin-worker",
        "packages",
        json.dumps([{"name": "demo", "version": "1.0"}]),
    )
