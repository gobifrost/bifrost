"""Both cancel paths record when cancellation was requested.

The stale-run sweeper measures the cancelling window from
``AgentRun.cancel_requested_at``, so every writer of ``status="cancelling"``
must stamp it, and the migration must stamp rows already in cancelling.
"""

from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Conversation
from src.routers import agent_runs as agent_runs_router
from src.services import chat_runs

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
    run = _running_run(trigger_type="api")
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
    response = await chat_runs.cancel_chat_run(db_session, _principal(seed_user), run.id)

    assert response.status == "cancelling"
    reloaded = await _load_run(async_session_factory, run.id)
    assert reloaded.status == "cancelling"
    assert reloaded.cancel_requested_at is not None
    assert reloaded.cancel_requested_at >= before


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
