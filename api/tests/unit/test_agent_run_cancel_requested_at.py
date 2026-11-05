"""Both cancel paths record when cancellation was requested.

The stale-run sweeper measures the cancelling window from
``AgentRun.cancel_requested_at``, so every writer of ``status="cancelling"``
must stamp it, and the migration must stamp rows already in cancelling.
"""

from __future__ import annotations

import asyncio
import importlib.util
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis import exceptions as redis_exceptions
from sqlalchemy import select, text

from src.core.principal import UserPrincipal
from src.jobs.consumers.agent_run import AgentRunConsumer
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Conversation
from src.routers import agent_runs as agent_runs_router
from src.services import chat_runs
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.execution.agent_run_service import CancelRequest, request_agent_run_cancel

pytestmark = pytest.mark.asyncio

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20261004_agent_run_cancel_req_at.py"
)


def _principal(user) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_superuser=True,
    )


def _caller(user) -> Caller:
    principal = _principal(user)
    return Caller(
        principal,
        AuthorizationContext(
            user_id=user.id,
            home_organization_id=user.organization_id,
            base_role_id=user.base_role_id,
            is_external=False,
            base_permissions=frozenset(),
        ),
    )


def _running_run(**kwargs) -> AgentRun:
    started = datetime.now(timezone.utc) - timedelta(minutes=20)
    return AgentRun(
        id=uuid4(),
        agent_id=None,
        status="running",
        iterations_used=1,
        tokens_used=25,
        created_at=started,
        started_at=started,
        **kwargs,
    )


async def _load_run(async_session_factory, run_id) -> AgentRun:
    async with async_session_factory() as db:
        return (await db.execute(select(AgentRun).where(AgentRun.id == run_id))).scalar_one()


async def test_agent_run_cancel_route_records_cancel_request(
    db_session, async_session_factory, seed_user, monkeypatch
) -> None:
    redis_client = SimpleNamespace(set_agent_run_cancel_flag=AsyncMock())
    monkeypatch.setattr(agent_runs_router, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr("src.core.pubsub.publish_agent_run_update", AsyncMock())
    run = _running_run(trigger_type="api", caller_user_id=str(seed_user.id))
    db_session.add(run)
    await db_session.commit()

    before = datetime.now(timezone.utc)
    response = await agent_runs_router.cancel_agent_run(
        run_id=run.id, db=db_session, user=_principal(seed_user)
    )

    assert response == {"run_id": str(run.id), "status": "cancelling"}
    reloaded = await _load_run(async_session_factory, run.id)
    assert reloaded.status == "cancelling"
    assert reloaded.cancel_requested_at is not None
    assert reloaded.cancel_requested_at >= before


async def test_chat_run_cancel_records_cancel_request(
    db_session, async_session_factory, seed_user, monkeypatch
) -> None:
    redis = SimpleNamespace(setex=AsyncMock())

    @asynccontextmanager
    async def _get_redis():
        yield redis

    monkeypatch.setattr(chat_runs, "get_redis", _get_redis)
    monkeypatch.setattr(chat_runs, "publish_chat_run_event", AsyncMock())
    conversation = Conversation(id=uuid4(), user_id=seed_user.id, title="Cancel me")
    run = _running_run(trigger_type="chat", conversation_id=conversation.id)
    db_session.add_all([conversation, run])
    await db_session.commit()

    before = datetime.now(timezone.utc)
    response = await chat_runs.cancel_chat_run(db_session, _caller(seed_user), run.id)

    assert response.status == "cancelling"
    reloaded = await _load_run(async_session_factory, run.id)
    assert reloaded.status == "cancelling"
    assert reloaded.cancel_requested_at is not None
    assert reloaded.cancel_requested_at >= before


class FlakyRedis:
    """Redis whose first write fails; ``setex`` and ``get`` as in redis.asyncio."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.failures = 1

    async def setex(self, name: str, time: int, value: str) -> bool:
        if self.failures:
            self.failures -= 1
            raise redis_exceptions.ConnectionError("Connection reset by peer")
        self.values[name] = value
        return True

    async def get(self, name: str) -> str | None:
        return self.values.get(name)


class FlakyRedisClient:
    """``RedisClient.set_agent_run_cancel_flag`` over :class:`FlakyRedis`."""

    def __init__(self, redis: FlakyRedis) -> None:
        self._redis = redis

    async def set_agent_run_cancel_flag(self, run_id: str) -> None:
        await self._redis.setex(f"bifrost:agent_run:{run_id}:cancel", 3600, "1")


async def _watcher_stops_the_run(run_id, redis: FlakyRedis) -> None:
    """The consumer's cancel watcher, watching a stand-in for the run's task."""
    work = asyncio.create_task(asyncio.Event().wait())
    await AgentRunConsumer._cancel_watcher(str(run_id), work, redis)
    with pytest.raises(asyncio.CancelledError):
        await work


async def test_agent_run_cancel_signals_again_after_a_failed_signal(
    db_session, async_session_factory, seed_user, monkeypatch
) -> None:
    redis = FlakyRedis()
    monkeypatch.setattr(agent_runs_router, "get_redis_client", lambda: FlakyRedisClient(redis))
    monkeypatch.setattr("src.core.pubsub.publish_agent_run_update", AsyncMock())
    run = _running_run(trigger_type="api", caller_user_id=str(seed_user.id))
    db_session.add(run)
    await db_session.commit()

    with pytest.raises(redis_exceptions.ConnectionError):
        await agent_runs_router.cancel_agent_run(
            run_id=run.id, db=db_session, user=_principal(seed_user)
        )
    first = await _load_run(async_session_factory, run.id)
    assert first.status == "cancelling"
    assert redis.values == {}

    async with async_session_factory() as retry_db:
        response = await agent_runs_router.cancel_agent_run(
            run_id=run.id, db=retry_db, user=_principal(seed_user)
        )

    assert response == {"run_id": str(run.id), "status": "cancelling"}
    again = await _load_run(async_session_factory, run.id)
    assert again.cancel_requested_at == first.cancel_requested_at
    await _watcher_stops_the_run(run.id, redis)


async def test_chat_run_cancel_signals_again_after_a_failed_signal(
    db_session, async_session_factory, seed_user, monkeypatch
) -> None:
    redis = FlakyRedis()

    @asynccontextmanager
    async def _get_redis():
        yield redis

    monkeypatch.setattr(chat_runs, "get_redis", _get_redis)
    monkeypatch.setattr(chat_runs, "publish_chat_run_event", AsyncMock())
    conversation = Conversation(id=uuid4(), user_id=seed_user.id, title="Cancel me")
    run = _running_run(trigger_type="chat", conversation_id=conversation.id)
    db_session.add_all([conversation, run])
    await db_session.commit()

    with pytest.raises(redis_exceptions.ConnectionError):
        await chat_runs.cancel_chat_run(db_session, _caller(seed_user), run.id)
    first = await _load_run(async_session_factory, run.id)
    assert first.status == "cancelling"
    assert redis.values == {}

    async with async_session_factory() as retry_db:
        response = await chat_runs.cancel_chat_run(retry_db, _caller(seed_user), run.id)

    assert response.status == "cancelling"
    again = await _load_run(async_session_factory, run.id)
    assert again.cancel_requested_at == first.cancel_requested_at
    await _watcher_stops_the_run(run.id, redis)


async def test_a_cancel_never_revives_a_run_that_ended(db_session, async_session_factory) -> None:
    run = _running_run(trigger_type="api")
    run.status = "completed"
    db_session.add(run)
    await db_session.commit()

    async with async_session_factory() as db:
        cancel = await request_agent_run_cancel(db, run.id)

    assert cancel == CancelRequest(status="completed", accepted=False)
    reloaded = await _load_run(async_session_factory, run.id)
    assert reloaded.status == "completed"
    assert reloaded.cancel_requested_at is None


async def test_migration_backfills_only_cancelling_runs(db_session) -> None:
    spec = importlib.util.spec_from_file_location("agent_run_cancel_req_at", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    stuck = _running_run(trigger_type="api")
    stuck.status = "cancelling"
    never_started = AgentRun(
        id=uuid4(),
        agent_id=None,
        trigger_type="api",
        status="cancelling",
        iterations_used=0,
        tokens_used=0,
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    running = _running_run(trigger_type="api")
    db_session.add_all([stuck, never_started, running])
    await db_session.flush()

    await db_session.execute(text(migration.BACKFILL_SQL))

    rows = {
        run.id: run
        for run in (
            await db_session.execute(
                select(AgentRun)
                .where(AgentRun.id.in_([stuck.id, never_started.id, running.id]))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert rows[stuck.id].cancel_requested_at == rows[stuck.id].started_at
    assert rows[never_started.id].cancel_requested_at == rows[never_started.id].created_at
    assert rows[running.id].cancel_requested_at is None
