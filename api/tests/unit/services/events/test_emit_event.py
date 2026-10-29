"""Transaction-order tests for the public topic-event emitter."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@asynccontextmanager
async def _savepoint():
    yield


def _session(order: list[str]) -> AsyncMock:
    db = AsyncMock()
    db.begin_nested = MagicMock(side_effect=_savepoint)
    db.commit.side_effect = lambda: order.append("commit")
    return db


async def _emit(db: AsyncMock, processor: AsyncMock):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def session_context():
        yield db

    with (
        patch("src.core.database.get_session_factory", return_value=session_context),
        patch("src.services.events.EventProcessor", return_value=processor),
    ):
        from src.services.events import emit_event

        return await emit_event("test.topic", {"value": 1})


@pytest.mark.asyncio
async def test_emit_event_commits_runs_with_their_deliveries_then_hands_them_off():
    """One commit holds the event, its deliveries and their runs."""
    order: list[str] = []
    db = _session(order)
    event_id = uuid4()
    processor = AsyncMock()
    processor.emit_topic.return_value = (event_id, 1)
    queued = MagicMock(hand_off=AsyncMock(side_effect=lambda: order.append("hand_off")))
    processor.queue_event_deliveries.side_effect = lambda _id: order.append("queue") or queued

    result = await _emit(db, processor)

    assert result == (event_id, 1)
    processor.queue_event_deliveries.assert_awaited_once_with(event_id)
    assert order == ["queue", "commit", "hand_off"]


@pytest.mark.asyncio
async def test_emit_event_records_the_event_when_queueing_fails_outright():
    order: list[str] = []
    db = _session(order)
    processor = AsyncMock()
    processor.emit_topic.return_value = (uuid4(), 1)
    processor.queue_event_deliveries.side_effect = RuntimeError("database went away")

    with pytest.raises(RuntimeError):
        await _emit(db, processor)

    assert order == ["commit"]
