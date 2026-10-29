"""Work an earlier release left in RabbitMQ becomes durable dispatches."""

from __future__ import annotations

import asyncio
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
from src.models.orm.config import SystemConfig
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
    monkeypatch.setattr("src.services.execution.queue_tracker.publish_queue_positions", AsyncMock())

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


async def _forget_drain(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        await session.execute(
            delete(SystemConfig).where(
                SystemConfig.category == drain.DRAINED_CATEGORY,
                SystemConfig.key == drain.DRAINED_KEY,
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean(async_session_factory: async_sessionmaker[AsyncSession]):
    await _forget_drain(async_session_factory)
    yield
    async with async_session_factory() as session:
        await session.execute(delete(WorkDispatch))
        await session.commit()
    await _forget_drain(async_session_factory)


async def _person(world) -> tuple[UUID, UUID]:  # type: ignore[no-untyped-def]
    """An organization and a person in it."""
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User

    async with world.sessions() as session:
        org = Organization(name=f"drain-{uuid4().hex[:8]}", is_active=True, created_by="t")
        session.add(org)
        await session.flush()
        user = User(email=f"drain-{uuid4().hex[:8]}@example.com", organization_id=org.id, name="Person")
        session.add(user)
        await session.commit()
        return org.id, user.id


def _pending(execution_id: str, user_id: UUID | str, org_id: UUID | None = None, **extra: Any) -> dict[str, Any]:
    """A pending record as v1.4.1 wrote it: no lineage."""
    record = pending_execution_record(
        execution_id=execution_id,
        workflow_id=None,
        script_name="queued_before_upgrade",
        parameters={"n": 1},
        org_id=None if org_id is None else str(org_id),
        user_id=str(user_id),
            user_name="Dev",
            user_email="dev@example.com",
            lineage=None,
    )
    record.pop("lineage", None)
    return {**record, "cancelled": False, **extra}


async def test_a_queued_run_without_a_row_is_accepted_for_its_recorded_user(world) -> None:  # type: ignore[no-untyped-def]
    org_id, person = await _person(world)
    execution_id = str(uuid4())
    world.put(drain._workflow_pending_key(execution_id), _pending(execution_id, person, org_id))

    await drain.recover_workflow_run(
        {"execution_id": execution_id, "code": "eA==", "script_name": "queued_before_upgrade", "sync": False}
    )

    async with world.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
    assert execution is not None and execution.status == ExecutionStatus.PENDING
    assert (execution.run_user_id, execution.started_by_user_id, execution.root_execution_id) == (
        person, person, UUID(execution_id),
    )
    dispatch = await world.dispatch(UUID(execution_id))
    assert dispatch is not None and dispatch.payload["parameters"] == {"n": 1}
    assert dispatch.message["code"] == "eA=="
    assert not world.has(drain._workflow_pending_key(execution_id))
    world.wakes.assert_awaited()


async def test_a_promoted_scheduled_run_gets_its_dispatch_and_lineage(world) -> None:  # type: ignore[no-untyped-def]
    org_id, person = await _person(world)
    execution_id = uuid4()
    async with world.sessions() as session:
        session.add(
            Execution(
                id=execution_id, workflow_name="scheduled", status=ExecutionStatus.PENDING,
                parameters={}, executed_by=person, executed_by_name="Person", organization_id=org_id,
            )
        )
        await session.commit()
    world.put(drain._workflow_pending_key(str(execution_id)), _pending(str(execution_id), person, org_id))

    await drain.recover_workflow_run(
        {"execution_id": str(execution_id), "sync": False, "execution_record_exists": True}
    )

    assert await world.dispatch(execution_id) is not None
    async with world.sessions() as session:
        execution = await session.get(Execution, execution_id)
    assert (execution.run_user_id, execution.root_execution_id) == (person, execution_id)


async def test_an_unattended_queued_run_runs_as_its_organizations_identity(world) -> None:  # type: ignore[no-untyped-def]
    from shared.identities import ensure_default_identity
    from src.core.constants import SYSTEM_USER_UUID
    from src.models.orm.organizations import Organization

    org_id, _ = await _person(world)
    async with world.sessions() as session:
        identity = await ensure_default_identity(session, await session.get(Organization, org_id))
        await session.commit()
        identity_id = identity.id
    execution_id = str(uuid4())
    world.put(drain._workflow_pending_key(execution_id), _pending(execution_id, SYSTEM_USER_UUID, org_id))

    await drain.recover_workflow_run({"execution_id": execution_id, "sync": False})

    async with world.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
    assert (execution.run_user_id, execution.started_by_user_id) == (identity_id, identity_id)


async def test_a_run_cancelled_while_queued_is_recorded_as_cancelled(world) -> None:  # type: ignore[no-untyped-def]
    org_id, person = await _person(world)
    execution_id = str(uuid4())
    world.put(
        drain._workflow_pending_key(execution_id), _pending(execution_id, person, org_id, cancelled=True)
    )

    await drain.recover_workflow_run({"execution_id": execution_id, "sync": False})

    async with world.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
    assert execution is not None and execution.status == ExecutionStatus.CANCELLED
    assert execution.error_message == drain.CANCELLED_BEFORE_START
    assert await world.dispatch(UUID(execution_id)) is None
    assert not world.has(drain._workflow_pending_key(execution_id))


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
    world.put(drain._workflow_pending_key(execution_id), _pending(execution_id, uuid4()))

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


async def test_a_queued_agent_run_gets_its_dispatch_and_runs_for_its_caller(world) -> None:  # type: ignore[no-untyped-def]
    org_id, person = await _person(world)
    run_id = await _queued_agent_run(world, org_id=org_id, caller_user_id=str(person))
    world.put(drain._agent_context_key(str(run_id)), {"run_id": str(run_id), "input": {"task": "t"}, "cancelled": False})

    await drain.recover_agent_run({"run_id": str(run_id), "agent_id": None, "trigger_type": "sdk", "sync": False})

    dispatch = await world.dispatch(run_id)
    assert dispatch is not None and dispatch.payload["input"] == {"task": "t"}
    assert "cancelled" not in dispatch.payload
    async with world.sessions() as session:
        run = await session.get(AgentRun, run_id)
    assert (run.run_user_id, run.acting_user_id) == (person, person)


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
    monkeypatch.setattr(drain, "QUIET_SECONDS", 0)
    drained = AsyncMock(return_value=(0, True))
    monkeypatch.setattr(drain, "_drain_queue", drained)

    await drain.drain_legacy_broker()

    assert [c.args[1] for c in drained.await_args_list] == list(drain.HANDLERS)
    assert connection.closed


async def test_work_queued_after_the_first_pass_is_drained_before_disconnecting(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An older replica can still queue work during a rolling upgrade."""
    from types import SimpleNamespace

    class _Connection:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    queued_later = {"pass": 0}

    async def drain_queue(_connection, name):  # type: ignore[no-untyped-def]
        if name == "workflow-executions":
            queued_later["pass"] += 1
            return (1 if queued_later["pass"] == 2 else 0), True
        return 0, True

    monkeypatch.setattr(drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url="amqp://broker/"))
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock())
    monkeypatch.setattr(drain.aio_pika, "connect", AsyncMock(return_value=_Connection()))
    monkeypatch.setattr(drain, "QUIET_SECONDS", 0.05)
    monkeypatch.setattr(drain, "PASS_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(drain, "_drain_queue", drain_queue)

    await drain.drain_legacy_broker()

    assert queued_later["pass"] > 2


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

    assert await drain._drain_queue(_Connection(), "workflow-executions") == (1, False)  # type: ignore[arg-type]
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

    assert await drain._drain_queue(_Connection(), "agent-runs") == (500, True)  # type: ignore[arg-type]
    assert all(m.acked for m in messages)


class _OpenConnection:
    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def _drained(sessions: async_sessionmaker[AsyncSession]) -> bool:
    async with sessions() as session:
        return await drain.legacy_broker_drained(session)


async def test_a_finished_drain_is_recorded_and_later_starts_never_connect(
    monkeypatch: pytest.MonkeyPatch, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url="amqp://broker/"))
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock())
    connect = AsyncMock(return_value=_OpenConnection())
    monkeypatch.setattr(drain.aio_pika, "connect", connect)
    monkeypatch.setattr(drain, "QUIET_SECONDS", 0)
    monkeypatch.setattr(drain, "_drain_queue", AsyncMock(return_value=(0, True)))

    await drain.drain_legacy_broker()
    assert await _drained(async_session_factory)

    await drain.drain_legacy_broker()
    assert connect.await_count == 1


async def test_a_drain_that_leaves_a_message_queued_is_not_recorded(
    monkeypatch: pytest.MonkeyPatch, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from types import SimpleNamespace

    async def drain_queue(_connection, name):  # type: ignore[no-untyped-def]
        return 0, name != "agent-runs"

    monkeypatch.setattr(drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url="amqp://broker/"))
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock())
    monkeypatch.setattr(drain.aio_pika, "connect", AsyncMock(return_value=_OpenConnection()))
    monkeypatch.setattr(drain, "QUIET_SECONDS", 0)
    monkeypatch.setattr(drain, "_drain_queue", drain_queue)

    await drain.drain_legacy_broker()

    assert not await _drained(async_session_factory)


async def test_an_unreachable_broker_is_not_recorded_as_drained(
    monkeypatch: pytest.MonkeyPatch, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url="amqp://broker/"))
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock())
    monkeypatch.setattr(drain, "CONNECT_RETRY_SECONDS", 0)
    monkeypatch.setattr(drain.aio_pika, "connect", AsyncMock(side_effect=ConnectionError))

    await drain.drain_legacy_broker()

    assert not await _drained(async_session_factory)


async def _a_drain_waits_for_the_lock(sessions: async_sessionmaker[AsyncSession]) -> None:
    from sqlalchemy import text

    while True:
        async with sessions() as session:
            waiting = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND NOT granted AND objid::bigint = (hashtext(:key)::bigint & 4294967295)"
                ),
                {"key": drain.DRAIN_LOCK},
            )
        if waiting:
            return
        await asyncio.sleep(0.02)


def _blocking_first_drain(release: asyncio.Event, *, then_raise: bool):  # type: ignore[no-untyped-def]
    """The first drain holds a message until released; later drains find nothing."""
    holding = asyncio.Event()

    async def drain_queue(_connection, _name):  # type: ignore[no-untyped-def]
        if not holding.is_set():
            holding.set()
            await release.wait()
            if then_raise:
                raise RuntimeError("replica died while recovering")
        return 0, True

    return holding, drain_queue


async def test_a_replica_waits_for_a_drain_in_progress_and_then_finds_it_recorded(
    monkeypatch: pytest.MonkeyPatch, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A second replica must not record the drain while the first still holds a message."""
    from types import SimpleNamespace

    release = asyncio.Event()
    holding, drain_queue = _blocking_first_drain(release, then_raise=False)
    connect = AsyncMock(return_value=_OpenConnection())
    monkeypatch.setattr(drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url="amqp://broker/"))
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock())
    monkeypatch.setattr(drain.aio_pika, "connect", connect)
    monkeypatch.setattr(drain, "QUIET_SECONDS", 0)
    monkeypatch.setattr(drain, "_drain_queue", drain_queue)

    first = asyncio.create_task(drain.drain_legacy_broker())
    await asyncio.wait_for(holding.wait(), 10)
    second = asyncio.create_task(drain.drain_legacy_broker())
    await asyncio.wait_for(_a_drain_waits_for_the_lock(async_session_factory), 10)
    assert not await _drained(async_session_factory)

    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 10)

    assert connect.await_count == 1
    assert await _drained(async_session_factory)


async def test_a_replica_that_waited_behind_a_failed_drain_drains_itself(
    monkeypatch: pytest.MonkeyPatch, async_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from types import SimpleNamespace

    release = asyncio.Event()
    holding, drain_queue = _blocking_first_drain(release, then_raise=True)
    connect = AsyncMock(return_value=_OpenConnection())
    monkeypatch.setattr(drain, "get_settings", lambda: SimpleNamespace(rabbitmq_url="amqp://broker/"))
    monkeypatch.setattr(drain, "wait_for_schema", AsyncMock())
    monkeypatch.setattr(drain.aio_pika, "connect", connect)
    monkeypatch.setattr(drain, "QUIET_SECONDS", 0)
    monkeypatch.setattr(drain, "_drain_queue", drain_queue)

    first = asyncio.create_task(drain.drain_legacy_broker())
    await asyncio.wait_for(holding.wait(), 10)
    second = asyncio.create_task(drain.drain_legacy_broker())
    await asyncio.wait_for(_a_drain_waits_for_the_lock(async_session_factory), 10)

    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 10)

    assert connect.await_count == 2
    assert await _drained(async_session_factory)
