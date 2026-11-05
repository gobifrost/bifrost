"""An agent run whose process stopped without an outcome is ended once, loudly."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core.cache.redis_client import get_redis
from src.models.orm.agent_runs import AgentRun
from src.services.execution import agent_run_service
from src.services.execution.agent_run_service import announce_ended_agent_run


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    chat, update = AsyncMock(), AsyncMock()
    monkeypatch.setattr(agent_run_service, "publish_chat_run_event", chat)
    monkeypatch.setattr(agent_run_service, "publish_agent_run_update", update)
    return SimpleNamespace(chat=chat, update=update)


async def _sync_result(run_id) -> dict | None:  # type: ignore[no-untyped-def]
    async with get_redis() as redis:
        raw = await redis.lpop(f"bifrost:agent_run:{run_id}:result")  # pyright: ignore[reportGeneralTypeIssues]
    return json.loads(raw) if raw else None


async def test_a_running_run_fails_and_its_sync_caller_hears_at_once(
    async_session_factory, monkeypatch, published,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr(agent_run_service, "get_db_context", context)
    run_id = uuid4()
    async with async_session_factory() as session:
        session.add(AgentRun(id=run_id, trigger_type="sdk", status="running"))
        await session.commit()
    try:
        assert await announce_ended_agent_run(run_id, "child crashed") is True

        async with async_session_factory() as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            assert (run.status, run.error) == ("failed", "child crashed")
            assert run.completed_at is not None
        assert await _sync_result(run_id) == {
            "output": None, "status": "failed", "error": "child crashed",
        }
        published.update.assert_awaited_once()
        published.chat.assert_not_awaited()

        # Already ended: nobody is woken twice.
        assert await announce_ended_agent_run(run_id, "again") is False
        assert await _sync_result(run_id) is None
        published.update.assert_awaited_once()
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await session.commit()


class _Session:
    def __init__(self, run: AgentRun | None) -> None:
        self.run = run
        self.commit = AsyncMock()

    async def get(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return self.run


def _with_run(monkeypatch: pytest.MonkeyPatch, run: AgentRun) -> _Session:
    session = _Session(run)

    @asynccontextmanager
    async def context():
        yield session

    monkeypatch.setattr(agent_run_service, "get_db_context", context)
    return session


@pytest.mark.parametrize(
    "status,ended,kind", [("running", "failed", "error"), ("cancelling", "cancelled", "cancelled")]
)
async def test_a_chat_run_ends_its_stream(monkeypatch, published, status, ended, kind) -> None:
    conversation_id = uuid4()
    run = AgentRun(
        id=uuid4(), trigger_type="chat", status=status, conversation_id=conversation_id,
    )
    session = _with_run(monkeypatch, run)

    assert await announce_ended_agent_run(run.id, "worker lost") is True

    session.commit.assert_awaited_once()
    assert run.status == ended
    published.chat.assert_awaited_once()
    call = published.chat.await_args.kwargs
    assert (call["conversation_id"], call["run_id"], call["kind"], call["status"]) == (
        conversation_id, run.id, kind, ended,
    )
    assert call["payload"].type == kind
    assert await _sync_result(run.id) == {"output": None, "status": ended, "error": "worker lost"}


async def test_a_finished_run_is_left_alone(monkeypatch, published) -> None:
    run = AgentRun(id=uuid4(), trigger_type="sdk", status="completed")
    session = _with_run(monkeypatch, run)

    assert await announce_ended_agent_run(run.id, "late exit") is False

    session.commit.assert_not_awaited()
    assert run.status == "completed"
    published.update.assert_not_awaited()
    assert await _sync_result(run.id) is None
