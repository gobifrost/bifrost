"""Work an earlier release left in RabbitMQ becomes durable dispatches."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.redis_client import pending_execution_record
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch
from src.services import legacy_broker_drain as drain
from src.services import work_dispatch


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture
def world(async_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch):
    import json

    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session

    redis = _Redis()

    @asynccontextmanager
    async def get_redis():  # type: ignore[no-untyped-def]
        yield redis

    monkeypatch.setattr(drain, "get_dispatch_db_context", context)
    monkeypatch.setattr(drain, "get_redis", get_redis)
    wake = AsyncMock()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    monkeypatch.setattr("src.services.execution.queue_tracker.add_to_queue", AsyncMock())

    class World:
        sessions = async_session_factory
        wakes = wake

        def put(self, key: str, value: dict[str, Any]) -> None:
            redis.values[key] = json.dumps(value)

        def has(self, key: str) -> bool:
            return key in redis.values

        async def dispatch(self, subject: UUID) -> WorkDispatch | None:
            async with async_session_factory() as session:
                return await session.scalar(
                    select(WorkDispatch).where(WorkDispatch.subject_id == subject)
                )

    yield World()


@pytest.fixture(autouse=True)
async def _clean(async_session_factory: async_sessionmaker[AsyncSession]):
    yield
    async with async_session_factory() as session:
        await session.execute(delete(WorkDispatch))
        await session.commit()


def _pending(execution_id: str, **extra: Any) -> dict[str, Any]:
    return {
        **pending_execution_record(
            execution_id=execution_id,
            workflow_id=None,
            script_name="queued_before_upgrade",
            parameters={"n": 1},
            org_id=None,
            user_id="00000000-0000-0000-0000-000000000001",
            user_name="Dev",
            user_email="dev@example.com",
            lineage=None,
        ),
        "cancelled": False,
        **extra,
    }


async def test_a_queued_run_without_a_row_is_accepted_as_today(world) -> None:  # type: ignore[no-untyped-def]
    execution_id = str(uuid4())
    world.put(drain._workflow_pending_key(execution_id), _pending(execution_id))

    await drain.recover_workflow_run(
        {"execution_id": execution_id, "code": "eA==", "script_name": "queued_before_upgrade", "sync": False}
    )

    async with world.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
    assert execution is not None and execution.status == ExecutionStatus.PENDING
    dispatch = await world.dispatch(UUID(execution_id))
    assert dispatch is not None and dispatch.payload["parameters"] == {"n": 1}
    assert dispatch.message["code"] == "eA=="
    assert not world.has(drain._workflow_pending_key(execution_id))
    world.wakes.assert_awaited()


async def test_a_promoted_scheduled_run_gets_its_dispatch(world) -> None:  # type: ignore[no-untyped-def]
    execution_id = uuid4()
    async with world.sessions() as session:
        session.add(
            Execution(
                id=execution_id, workflow_name="scheduled", status=ExecutionStatus.PENDING,
                parameters={}, executed_by_name="Dev",
            )
        )
        await session.commit()
    world.put(drain._workflow_pending_key(str(execution_id)), _pending(str(execution_id)))

    await drain.recover_workflow_run(
        {"execution_id": str(execution_id), "sync": False, "execution_record_exists": True}
    )

    assert await world.dispatch(execution_id) is not None


async def test_a_promoted_run_whose_context_expired_fails_visibly(world) -> None:  # type: ignore[no-untyped-def]
    execution_id = uuid4()
    async with world.sessions() as session:
        session.add(
            Execution(
                id=execution_id, workflow_name="scheduled", status=ExecutionStatus.PENDING,
                parameters={}, executed_by_name="Dev",
            )
        )
        await session.commit()

    await drain.recover_workflow_run(
        {"execution_id": str(execution_id), "sync": False, "execution_record_exists": True}
    )

    async with world.sessions() as session:
        execution = await session.get(Execution, execution_id)
    assert execution.status == ExecutionStatus.FAILED
    assert execution.error_message == drain.LOST_IN_UPGRADE
    assert await world.dispatch(execution_id) is None


async def test_a_message_that_names_a_dispatch_is_only_acknowledged(world) -> None:  # type: ignore[no-untyped-def]
    execution_id = str(uuid4())
    world.put(drain._workflow_pending_key(execution_id), _pending(execution_id))

    await drain.recover_workflow_run({"execution_id": execution_id, "dispatch_id": str(uuid4())})

    assert await world.dispatch(UUID(execution_id)) is None


async def _queued_agent_run(world, **values: Any) -> UUID:  # type: ignore[no-untyped-def]
    run_id = uuid4()
    async with world.sessions() as session:
        session.add(
            AgentRun(
                id=run_id, trigger_type="sdk", status="queued", iterations_used=0,
                tokens_used=0, **values,
            )
        )
        await session.commit()
    return run_id


async def test_a_queued_agent_run_gets_its_dispatch(world) -> None:  # type: ignore[no-untyped-def]
    run_id = await _queued_agent_run(world)
    world.put(drain._agent_context_key(str(run_id)), {"run_id": str(run_id), "input": {"task": "t"}, "cancelled": False})

    await drain.recover_agent_run({"run_id": str(run_id), "agent_id": None, "trigger_type": "sdk", "sync": False})

    dispatch = await world.dispatch(run_id)
    assert dispatch is not None and dispatch.payload["input"] == {"task": "t"}
    assert "cancelled" not in dispatch.payload


async def test_an_agent_run_whose_context_expired_fails_visibly(world) -> None:  # type: ignore[no-untyped-def]
    run_id = await _queued_agent_run(world)

    await drain.recover_agent_run({"run_id": str(run_id), "agent_id": None, "trigger_type": "sdk", "sync": False})

    async with world.sessions() as session:
        run = await session.get(AgentRun, run_id)
    assert (run.status, run.error) == ("failed", drain.LOST_IN_UPGRADE)


async def test_a_queued_summary_is_requested_again(world) -> None:  # type: ignore[no-untyped-def]
    run_id = uuid4()
    async with world.sessions() as session:
        session.add(
            AgentRun(
                id=run_id, trigger_type="sdk", status="completed", iterations_used=1,
                tokens_used=1, summary_status="pending",
            )
        )
        await session.commit()

    await drain.recover_backfill_summary({"run_id": str(run_id), "backfill_job_id": str(uuid4())})

    dispatch = await world.dispatch(run_id)
    assert dispatch is not None and dispatch.handler == "agent.summarize_backfill"


async def test_no_configured_broker_does_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    monkeypatch.setattr(drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url=None))
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock(side_effect=AssertionError))
    connect = AsyncMock()
    monkeypatch.setattr(drain.aio_pika, "connect", connect)

    await drain.drain_legacy_broker()

    connect.assert_not_awaited()


async def test_an_unreachable_broker_is_tried_a_few_times_then_left(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    monkeypatch.setattr(
        drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url="amqp://nobody@127.0.0.1:1/")
    )
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock())
    monkeypatch.setattr(drain, "CONNECT_RETRY_SECONDS", 0)
    connect = AsyncMock(side_effect=ConnectionError)
    monkeypatch.setattr(drain.aio_pika, "connect", connect)

    await drain.drain_legacy_broker()

    assert connect.await_count == drain.CONNECT_ATTEMPTS


async def test_a_reachable_broker_is_drained_once_and_disconnected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    class _Connection:
        closed = False

        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_exc: object) -> None:
            self.closed = True

    connection = _Connection()
    monkeypatch.setattr(drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url="amqp://broker/"))
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock())
    monkeypatch.setattr(drain.aio_pika, "connect", AsyncMock(side_effect=[ConnectionError, connection]))
    monkeypatch.setattr(drain, "CONNECT_RETRY_SECONDS", 0)
    drained = AsyncMock(return_value=0)
    monkeypatch.setattr(drain, "_drain_queue", drained)

    await drain.drain_legacy_broker()

    assert [c.args[1] for c in drained.await_args_list] == list(drain.HANDLERS)
    assert connection.closed


class _Message:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.acked = self.nacked = False

    async def ack(self) -> None:
        self.acked = True

    async def nack(self, requeue: bool) -> None:
        assert requeue
        self.nacked = True


async def test_a_failed_recovery_leaves_its_message_queued(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    good, bad, later = _Message(b'{"n": 1}'), _Message(b'{"n": 2}'), _Message(b'{"n": 3}')
    pending = [good, bad, later]

    class _Queue:
        async def get(self, **_kwargs):  # type: ignore[no-untyped-def]
            return pending.pop(0) if pending else None

    class _Channel:
        async def declare_queue(self, name, passive):  # type: ignore[no-untyped-def]
            assert passive
            return _Queue()

        async def close(self) -> None:
            return None

    class _Connection:
        async def channel(self):  # type: ignore[no-untyped-def]
            return _Channel()

    async def handler(body: dict[str, Any]) -> None:
        if body["n"] == 2:
            raise RuntimeError("database down")

    monkeypatch.setitem(drain.HANDLERS, "workflow-executions", handler)

    assert await drain._drain_queue(_Connection(), "workflow-executions") == 1  # type: ignore[arg-type]
    assert (good.acked, bad.nacked, later.acked) == (True, True, False)


async def test_a_queue_drains_until_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    messages = [_Message(b'{"n": %d}' % n) for n in range(500)]
    pending = list(messages)

    class _Queue:
        async def get(self, **_kwargs):  # type: ignore[no-untyped-def]
            return pending.pop(0) if pending else None

    class _Channel:
        async def declare_queue(self, name, passive):  # type: ignore[no-untyped-def]
            return _Queue()

        async def close(self) -> None:
            return None

    class _Connection:
        async def channel(self):  # type: ignore[no-untyped-def]
            return _Channel()

    monkeypatch.setitem(drain.HANDLERS, "agent-runs", AsyncMock())

    assert await drain._drain_queue(_Connection(), "agent-runs") == 500  # type: ignore[arg-type]
    assert all(m.acked for m in messages)
