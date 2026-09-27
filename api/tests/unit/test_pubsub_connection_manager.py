from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.auth import UserPrincipal
from src.core.pubsub import ConnectionManager, service_channel_for_log
from src.routers import websocket as ws_mod


@pytest.mark.asyncio
async def test_publish_to_redis_uses_per_call_connections():
    manager = ConnectionManager()
    clients = []

    def fake_get_redis():
        client = AsyncMock()
        clients.append(client)

        @asynccontextmanager
        async def context():
            yield client

        return context()

    with patch("src.core.pubsub.get_redis", side_effect=fake_get_redis):
        assert await manager._publish_to_redis("execution:one", {"type": "one"})
        assert await manager._publish_to_redis("execution:two", {"type": "two"})

    assert len(clients) == 2
    assert clients[0] is not clients[1]
    clients[0].publish.assert_awaited_once()
    clients[1].publish.assert_awaited_once()


def _principal(is_superuser: bool) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="tester@example.com",
        organization_id=uuid4(),
        is_superuser=is_superuser,
    )


def test_service_channel_for_log_bridges_attempt_streams():
    service_id = str(uuid4())
    attempt_id = str(uuid4())
    assert (
        service_channel_for_log(
            f"service-logs:{attempt_id}",
            {
                "type": "service_log",
                "service_id": service_id,
                "attempt_id": attempt_id,
                "level": "INFO",
                "message": "hi",
            },
        )
        == f"service:{service_id}"
    )


def test_service_channel_for_log_ignores_everything_else():
    assert service_channel_for_log("execution:abc", {"type": "x"}) is None
    assert service_channel_for_log("service:abc", {"type": "x"}) is None
    assert (
        service_channel_for_log("service-logs:abc", {"type": "service_log"})
        is None
    )
    assert service_channel_for_log("service-logs:abc", None) is None
    assert service_channel_for_log("service-logs:abc", "junk") is None


@pytest.mark.asyncio
async def test_can_access_service_is_superuser_plus_uuid():
    assert await ws_mod.can_access_service(_principal(True), str(uuid4())) is True
    assert await ws_mod.can_access_service(_principal(False), str(uuid4())) is False
    assert await ws_mod.can_access_service(_principal(True), "not-a-uuid") is False
    assert await ws_mod.can_access_service(_principal(False), "not-a-uuid") is False


@pytest.mark.asyncio
async def test_bridge_delivers_locally_without_republish():
    """One instance fans out to its own subscribers exactly once.

    Re-publishing the bridge (broadcast) would duplicate every line once
    per API instance; the original pubsub message already reaches every
    instance, so local delivery is sufficient and exact.
    """
    from unittest.mock import AsyncMock

    manager = ConnectionManager()
    manager._send_local = AsyncMock()  # type: ignore[method-assign]
    with patch.object(
        ConnectionManager, "broadcast", new=AsyncMock()
    ) as broadcast:
        await manager._bridge_service_log(
            "service-logs:att-1",
            {"type": "service_log", "service_id": "svc-1"},
        )
        manager._send_local.assert_awaited_once_with(
            "service:svc-1",
            {"type": "service_log", "service_id": "svc-1"},
        )
        broadcast.assert_not_called()

        await manager._bridge_service_log("execution:abc", {"type": "x"})
        assert manager._send_local.await_count == 1
