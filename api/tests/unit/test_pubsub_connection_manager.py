from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.core.pubsub import ConnectionManager


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
