"""Loop-scoped sharing for the API Redis cache client."""

import asyncio
import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as redis

from src.core.cache import redis_client


@pytest.fixture(autouse=True)
async def reset_shared_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setattr(redis_client, "_shared_client", None)
    monkeypatch.setattr(redis_client, "_shared_loop", None, raising=False)
    monkeypatch.setattr(redis_client, "_shared_pid", None, raising=False)
    monkeypatch.setattr(redis_client, "_shared_initialization_lock", None, raising=False)
    monkeypatch.setattr(redis_client, "_shared_initialization_loop", None, raising=False)
    monkeypatch.setattr(redis_client, "_shared_initialization_pid", None, raising=False)
    yield
    await redis_client.close_shared_redis()


def redis_mock() -> MagicMock:
    client = MagicMock()
    client.ping = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_api_loop_borrowers_share_initialized_client_without_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = redis_mock()
    from_url = MagicMock(return_value=client)
    monkeypatch.setattr(redis_client.redis, "from_url", from_url)

    await redis_client.initialize_shared_redis()

    async def borrow() -> object:
        async with redis_client.get_redis() as borrowed:
            return borrowed

    borrowers = await asyncio.gather(*(borrow() for _ in range(8)))

    assert all(borrowed is client for borrowed in borrowers)
    from_url.assert_called_once()
    client.ping.assert_awaited_once()
    client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_shared_initialization_creates_one_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = redis_mock()
    ping_started = asyncio.Event()
    allow_ping = asyncio.Event()

    async def ping() -> None:
        ping_started.set()
        await allow_ping.wait()

    client.ping.side_effect = ping
    from_url = MagicMock(return_value=client)
    monkeypatch.setattr(redis_client.redis, "from_url", from_url)

    first = asyncio.create_task(redis_client.initialize_shared_redis())
    await ping_started.wait()
    second = asyncio.create_task(redis_client.initialize_shared_redis())
    # Give the second initializer a chance to enter while the first ping is blocked.
    await asyncio.sleep(0)
    allow_ping.set()
    initialized = await asyncio.gather(first, second)

    assert all(initialized_client is client for initialized_client in initialized)
    from_url.assert_called_once()
    client.ping.assert_awaited_once()


@pytest.mark.asyncio
async def test_borrowed_client_translates_redis_errors_without_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = redis_mock()
    client.get = AsyncMock(side_effect=redis.ConnectionError("lost connection"))
    monkeypatch.setattr(redis_client.redis, "from_url", MagicMock(return_value=client))
    await redis_client.initialize_shared_redis()

    with pytest.raises(redis_client.CacheConnectionError):
        async with redis_client.get_redis() as borrowed:
            await borrowed.get("connection-error-check")

    client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_shared_setup_closes_candidate_without_binding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = redis_mock()
    candidate.ping.side_effect = redis.ConnectionError("unavailable")
    monkeypatch.setattr(redis_client.redis, "from_url", MagicMock(return_value=candidate))

    with pytest.raises(redis_client.CacheConnectionError):
        await redis_client.initialize_shared_redis()

    candidate.aclose.assert_awaited_once()
    assert redis_client._shared_client is None
    assert redis_client._shared_loop is None
    assert redis_client._shared_pid is None


@pytest.mark.asyncio
async def test_cancelled_shared_setup_closes_candidate_without_binding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = redis_mock()
    candidate.ping.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(redis_client.redis, "from_url", MagicMock(return_value=candidate))

    with pytest.raises(asyncio.CancelledError):
        await redis_client.initialize_shared_redis()

    candidate.aclose.assert_awaited_once()
    assert redis_client._shared_client is None


@pytest.mark.asyncio
async def test_unexpected_shared_setup_failure_closes_candidate_without_binding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = redis_mock()
    candidate.ping.side_effect = RuntimeError("unexpected setup failure")
    monkeypatch.setattr(redis_client.redis, "from_url", MagicMock(return_value=candidate))

    with pytest.raises(RuntimeError, match="unexpected setup failure"):
        await redis_client.initialize_shared_redis()

    candidate.aclose.assert_awaited_once()
    assert redis_client._shared_client is None


@pytest.mark.asyncio
async def test_shared_shutdown_resets_ownership_for_the_next_api_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = redis_mock()
    second = redis_mock()
    monkeypatch.setattr(redis_client.redis, "from_url", MagicMock(side_effect=[first, second]))

    await redis_client.initialize_shared_redis()
    await redis_client.close_shared_redis()
    replacement = await redis_client.initialize_shared_redis()

    assert replacement is second
    first.aclose.assert_awaited_once()
    second.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_thread_loop_uses_and_closes_its_own_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_client = redis_mock()
    foreign_client = redis_mock()
    monkeypatch.setattr(
        redis_client.redis,
        "from_url",
        MagicMock(side_effect=[api_client, foreign_client]),
    )
    await redis_client.initialize_shared_redis()

    async def borrow_and_close_shared() -> object:
        async with redis_client.get_redis() as client:
            borrowed = client
        await redis_client.close_shared_redis()
        return borrowed

    borrowed = await asyncio.to_thread(lambda: asyncio.run(borrow_and_close_shared()))

    assert borrowed is foreign_client
    api_client.aclose.assert_not_awaited()
    foreign_client.ping.assert_awaited_once()
    foreign_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_forked_pid_does_not_borrow_the_parent_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_client = redis_mock()
    child_client = redis_mock()
    monkeypatch.setattr(
        redis_client.redis,
        "from_url",
        MagicMock(side_effect=[parent_client, child_client]),
    )
    await redis_client.initialize_shared_redis()

    parent_pid = os.getpid()
    with monkeypatch.context() as child:
        child.setattr(redis_client.os, "getpid", lambda: parent_pid + 1)
        async with redis_client.get_redis() as borrowed:
            assert borrowed is child_client

    parent_client.aclose.assert_not_awaited()
    child_client.aclose.assert_awaited_once()
