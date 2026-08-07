"""Build-plane heartbeat + availability gate (WP3 Task 1).

The build plane (coordinator process) is only "available" while it is actively
heartbeating into Redis. Server-side builds must refuse to run (rather than
silently queue forever) when no coordinator is alive to claim them.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import src.core.redis_client as redis_client
from src.services.builder.build_plane import (
    HEARTBEAT_KEY,
    build_plane_available,
    cancel_key,
    record_builder_heartbeat,
)


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    redis_client._redis_client = None
    yield
    redis_client._redis_client = None


async def _clear_heartbeat() -> None:
    redis = await redis_client.get_redis_client()._get_redis()
    await redis.delete(HEARTBEAT_KEY)


class TestBuildPlaneAvailability:
    async def test_build_plane_available_false_without_heartbeat(self) -> None:
        await _clear_heartbeat()
        assert await build_plane_available() is False

    async def test_build_plane_available_true_after_heartbeat(self) -> None:
        await _clear_heartbeat()
        await record_builder_heartbeat()
        assert await build_plane_available() is True

    async def test_heartbeat_expires(self) -> None:
        await _clear_heartbeat()
        redis = await redis_client.get_redis_client()._get_redis()
        # Set the real heartbeat key directly with a 1s TTL to observe expiry
        # without waiting on the production TTL.
        await redis.set(HEARTBEAT_KEY, "2026-01-01T00:00:00+00:00", ex=1)
        assert await build_plane_available() is True
        await asyncio.sleep(1.2)
        assert await build_plane_available() is False


class TestCancelKey:
    def test_cancel_key_format(self) -> None:
        build_job_id = uuid4()
        assert cancel_key(build_job_id) == f"bifrost:build_job:{build_job_id}:cancel"
        # Also accepts a plain string id.
        assert cancel_key(str(build_job_id)) == f"bifrost:build_job:{build_job_id}:cancel"
