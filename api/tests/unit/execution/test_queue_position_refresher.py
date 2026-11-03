"""Advisory queue work is bounded and cannot hold up durable workflow paths."""

import asyncio
from contextvars import ContextVar
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.pubsub import ConnectionManager
from src.services.execution import queue_tracker
from src.services.execution.queue_tracker import QueuePositionRefresher, refresh_queue_positions


@pytest.mark.asyncio
async def test_burst_requests_coalesce_without_doing_io_in_the_caller() -> None:
    manager = ConnectionManager()
    manager.broadcast = AsyncMock()
    refresher = QueuePositionRefresher(manager)
    with (
        patch.object(refresher, "_wait_interval", new=AsyncMock()),
        patch.object(queue_tracker, "refresh_queue_positions", new=AsyncMock()) as refresh,
    ):
        for _ in range(1000):
            refresher.request(announce=True)
        manager.broadcast.assert_not_awaited()
        refresh.assert_not_awaited()
        assert refresher._task is not None
        await refresher._task

    manager.broadcast.assert_awaited_once_with(
        "queue:positions", {"type": "queue_positions_changed"},
    )
    refresh.assert_awaited_once_with(manager)


@pytest.mark.asyncio
async def test_a_change_during_refresh_keeps_one_latest_refresh_pending() -> None:
    manager = ConnectionManager()
    manager.broadcast = AsyncMock()
    refresher = QueuePositionRefresher(manager)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def refresh(_manager: ConnectionManager) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()

    with (
        patch.object(refresher, "_wait_interval", new=AsyncMock()),
        patch.object(queue_tracker, "refresh_queue_positions", side_effect=refresh),
    ):
        try:
            refresher.request(announce=True)
            await started.wait()
            for _ in range(1000):
                refresher.request(announce=True)
            release.set()
            assert refresher._task is not None
            await refresher._task
        finally:
            await refresher.close()

    assert calls == 2
    assert manager.broadcast.await_count == 2


@pytest.mark.asyncio
async def test_local_refresh_never_reannounces_or_inherits_execution_context() -> None:
    marker = ContextVar("test_execution_credential", default=None)
    marker.set("caller-only")
    manager = ConnectionManager()
    manager.broadcast = AsyncMock()
    refresher = QueuePositionRefresher(manager)
    observed = []

    async def refresh(_manager: ConnectionManager) -> None:
        observed.append(marker.get())

    with (
        patch.object(refresher, "_wait_interval", new=AsyncMock()),
        patch.object(queue_tracker, "refresh_queue_positions", side_effect=refresh),
    ):
        refresher.request()
        assert refresher._task is not None
        await refresher._task

    assert observed == [None]
    manager.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_local_execution_viewers_means_no_queue_query() -> None:
    manager = ConnectionManager()
    manager.connections["user:one"] = {AsyncMock()}
    with patch.object(queue_tracker, "get_db_context") as context:
        await refresh_queue_positions(manager)
        manager.request_queue_position_refresh()
    context.assert_not_called()
    assert manager._queue_refresher is None


@pytest.mark.asyncio
async def test_manager_shutdown_cancels_a_refresh_waiting_on_io() -> None:
    manager = ConnectionManager()
    started, finished = asyncio.Event(), asyncio.Event()

    async def refresh(_manager: ConnectionManager) -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            finished.set()

    with (
        patch.object(QueuePositionRefresher, "_wait_interval", new=AsyncMock()),
        patch.object(queue_tracker, "refresh_queue_positions", side_effect=refresh),
    ):
        manager.connections[f"execution:{uuid4()}"] = {AsyncMock()}
        manager.request_queue_position_refresh()
        await started.wait()
        await manager.close()

    assert finished.is_set()
    assert manager._queue_refresher is None


@pytest.mark.asyncio
async def test_failed_refresh_does_not_poison_the_next_queue_change() -> None:
    manager = ConnectionManager()
    manager.broadcast = AsyncMock()
    refresher = QueuePositionRefresher(manager)
    with (
        patch.object(refresher, "_wait_interval", new=AsyncMock()),
        patch.object(queue_tracker, "refresh_queue_positions", new=AsyncMock(
            side_effect=[ConnectionError("database unavailable"), None],
        )) as refresh,
    ):
        for _ in range(2):
            refresher.request(announce=True)
            assert refresher._task is not None
            await refresher._task

    assert refresh.await_count == 2


@pytest.mark.asyncio
async def test_dispatcher_may_remove_its_subscription_during_delivery() -> None:
    manager = ConnectionManager()
    channel = f"execution:{uuid4()}"
    viewer = AsyncMock()

    async def revoke(_channel: str, _message: dict) -> None:
        manager.disconnect(viewer)

    viewer._delivery_dispatcher = revoke
    manager.connections[channel] = {viewer}
    await manager._send_local(channel, {"type": "execution_queue_update"})
    assert channel not in manager.connections
