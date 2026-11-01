"""Real PostgreSQL fencing; physical launch is a deterministic local callback.

No cloud invocation or claim of physical Worker termination.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, ExecutionLog
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.models.orm.work_dispatches import WorkDispatch
from src.repositories.executions import update_execution
from src.services.execution.result_envelope import execution_result_envelope
from src.worker.remote import ownership
from src.worker.remote.fencing import fence_remote_owner
from src.worker.remote.protocol import CredentialRouteChanged

path = Path("/app/pyodide-prototype/attempt_journal.py")
spec = importlib.util.spec_from_file_location("pyodide_attempt_journal", path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
AttemptJournal = module.AttemptJournal

@pytest.fixture
async def owner(async_session_factory, monkeypatch):
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(ownership, "get_liveness_db_context", context)
    worker_id, owner_id, launcher_id = uuid4(), uuid4(), uuid4()
    async with async_session_factory() as session:
        session.add(ExecutionWorker(id=worker_id, form="static", state="ready",
                                    resource_class="standard", slots=1,
                                    supported_handlers=["workflow.execute"]))
        await session.commit()

    async def acquire(sequence):
        lease = await ownership.acquire_remote_owner(
            worker_id=worker_id, owner_id=owner_id, launcher_id=launcher_id,
            nonce_digest=f"{sequence:064x}", hello_sequence=sequence, credential_protocol=2,
        )
        assert lease is not None
        return lease

    try:
        yield acquire
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await session.commit()


@pytest.mark.asyncio
async def test_duplicate_dispatch_and_reconnect_never_repeat_physical_launch(owner):
    lease = await owner(1)
    journal = AttemptJournal(capacity=1)
    launch = AsyncMock(return_value={"status": "Success", "result": 70})
    tasks = await asyncio.gather(*[
        journal.dispatch(lease, "route", "execution", launch) for _ in range(2)
    ])
    assert tasks[0] is tasks[1]
    assert await tasks[0] == {"status": "Success", "result": 70}
    replacement = await owner(2)
    with pytest.raises(CredentialRouteChanged):
        await journal.dispatch(lease, "new-route", "execution", launch)
    assert await journal.dispatch(replacement, "route", "execution", launch) is tasks[0]
    launch.assert_awaited_once()
    with pytest.raises(ValueError, match="identity conflict"):
        await journal.dispatch(replacement, "route", "another-execution", launch)
    with pytest.raises(RuntimeError, match="journal full"):
        await journal.dispatch(replacement, "new-route", "another-execution", launch)


@pytest.mark.asyncio
async def test_terminal_replay_requires_acceptance_and_preserves_tombstone(owner):
    lease = await owner(1)
    journal = AttemptJournal()
    launch = AsyncMock(return_value={"status": "Success"})
    await (await journal.dispatch(lease, "route", "execution", launch))
    accept = AsyncMock(side_effect=[ConnectionError("commit response lost"), False, True])
    with pytest.raises(ConnectionError):
        await journal.reconcile_terminal(lease, "route", accept)
    with pytest.raises(RuntimeError, match="unacknowledged"):
        await journal.retire()
    replacement = await owner(2)
    with pytest.raises(CredentialRouteChanged):
        await journal.reconcile_terminal(lease, "route", accept)
    assert not await journal.reconcile_terminal(replacement, "route", accept)
    assert await journal.reconcile_terminal(replacement, "route", accept)
    assert await journal.reconcile_terminal(replacement, "route", accept)
    assert accept.await_count == 3
    assert await journal.dispatch(replacement, "route", "execution", launch)
    launch.assert_awaited_once()
    assert journal.inventory() == [{"route_id": "route", "execution_id": "execution",
                                    "terminal": True, "acknowledged": True}]
    await journal.retire()
    assert journal.inventory() == []
    with pytest.raises(RuntimeError, match="launcher retired"):
        await journal.dispatch(replacement, "route", "execution", launch)


@pytest.mark.asyncio
async def test_owner_rotation_between_preflight_and_commit_prevents_ack(owner, async_session_factory):
    lease = await owner(1)
    journal = AttemptJournal()
    await (await journal.dispatch(lease, "route", "execution", AsyncMock(return_value={"status": "Success"})))

    async def accept(_route, _execution, _result):
        # Deterministically expose the race: preflight was valid, commit is stale.
        await owner(2)
        async with async_session_factory() as session:
            return await fence_remote_owner(session)

    assert not await journal.reconcile_terminal(lease, "route", accept)
    assert not journal.inventory()[0]["acknowledged"]
    with pytest.raises(RuntimeError, match="unacknowledged"):
        await journal.retire()


@pytest.mark.asyncio
async def test_owner_rotation_after_acceptance_retains_result_for_new_owner(owner):
    lease = await owner(1)
    journal = AttemptJournal()
    launch = AsyncMock(return_value={"status": "Success"})
    await (await journal.dispatch(lease, "route", "execution", launch))

    async def accept(_route, _execution, _result):
        await owner(2)
        return True

    with pytest.raises(CredentialRouteChanged, match="before terminal ACK"):
        await journal.reconcile_terminal(lease, "route", accept)
    assert not journal.inventory()[0]["acknowledged"]
    current = await owner(3)
    accepted = AsyncMock(return_value=True)
    assert await journal.reconcile_terminal(current, "route", accepted)
    launch.assert_awaited_once()
    await journal.retire()


@pytest.mark.asyncio
async def test_real_canonical_result_write_is_claim_fenced_and_cannot_be_overwritten(owner, async_session_factory):
    lease = await owner(1)
    execution_id = uuid4()
    async with async_session_factory() as session:
        session.add(Execution(id=execution_id, workflow_name="pyodide-contract",
                              executed_by_name="test", status=ExecutionStatus.RUNNING))
        await session.flush()
        session.add(ExecutionWorkerClaim(worker_id=lease.worker_id,
                                        family="workflow_execution", subject_id=execution_id))
        await session.commit()
    journal = AttemptJournal()
    try:
        task = await journal.dispatch(lease, "route", str(execution_id),
                                      AsyncMock(return_value={"value": 70}))
        assert await task == {"value": 70}

        async def accept(_route, subject, result):
            async with async_session_factory() as session:
                written = await update_execution(subject, ExecutionStatus.SUCCESS,
                                                 result=result, session=session,
                                                 owner_worker_id=lease.worker_id)
                await session.commit()
                return written

        # Wrong claim owner cannot commit even with a valid remote session.
        async with async_session_factory() as session:
            assert not await update_execution(str(execution_id), ExecutionStatus.SUCCESS,
                                              result={"value": -1}, session=session,
                                              owner_worker_id=uuid4())
            await session.rollback()
        assert await journal.reconcile_terminal(lease, "route", accept)
        async with async_session_factory() as session:
            assert not await update_execution(str(execution_id), ExecutionStatus.SUCCESS,
                                              result={"value": -1}, session=session,
                                              owner_worker_id=lease.worker_id)
            await session.rollback()
            row = await session.get(Execution, execution_id)
            assert row is not None and row.result == {"value": 70}
        await journal.retire()
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorkerClaim).where(
                ExecutionWorkerClaim.subject_id == execution_id))
            await session.execute(delete(Execution).where(Execution.id == execution_id))
            await session.commit()


@pytest.mark.asyncio
async def test_running_route_cannot_be_acknowledged_or_cleaned_up(owner):
    lease = await owner(1)
    journal = AttemptJournal()
    release = asyncio.Event()

    async def launch():
        await release.wait()
        return {"status": "Success"}

    task = await journal.dispatch(lease, "route", "execution", launch)
    accept = AsyncMock(return_value=True)
    try:
        assert not await journal.reconcile_terminal(lease, "route", accept)
        accept.assert_not_awaited()
        with pytest.raises(RuntimeError, match="unacknowledged"):
            await journal.retire()
    finally:
        release.set()
        assert await task == {"status": "Success"}
    assert await journal.reconcile_terminal(lease, "route", accept)
    await journal.retire()


@pytest.mark.asyncio
async def test_full_consumer_commit_lost_ack_reconciles_without_relaunch_or_duplicate_logs(
    owner, async_session_factory, monkeypatch,
):
    from bifrost._logging import append_log_to_stream_async
    from src.core.cache import execution_logs_stream_key, get_redis
    from src.core.cache.keys import active_execution_key
    from src.core.redis_client import ActiveExecution, RESULT_KEY_PREFIX
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
    from src.services.execution.process_pool import ExecutionInfo

    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)
    lease = await owner(1)
    subject = uuid4()
    execution_id = str(subject)
    metadata = ActiveExecution(execution_id=execution_id, workflow_id=None,
                               workflow_name="pyodide-consumer-contract", org_id=None,
                               user_id=None, user_email=None, user_name="test", sync=False, event=None)
    execution = ExecutionInfo(execution_id, datetime.now(timezone.utc), 30, metadata)
    consumer = WorkflowExecutionConsumer(pool=SimpleNamespace(on_result=None))
    consumer.owner_worker_id = lease.worker_id
    metrics = AsyncMock(wraps=consumer._record_completion_metrics)
    monkeypatch.setattr(consumer, "_record_completion_metrics", metrics)
    original = consumer._handle_result

    async def lose_ack(result):
        await original(result)
        raise ConnectionError("transport lost after canonical completion")

    journal = AttemptJournal()
    envelope = execution_result_envelope(execution_id, str(lease.worker_id),
        {"status": "Success", "result": {"value": 70}}, duration_ms=1, metrics={})
    envelope["sync"] = True  # An untrusted field cannot override parent metadata.
    launch = AsyncMock(return_value=envelope)
    async with async_session_factory() as session:
        session.add(Execution(id=subject, workflow_name="pyodide-consumer-contract",
                              executed_by_name="test", status=ExecutionStatus.RUNNING,
                              started_at=datetime.now(timezone.utc)))
        await session.flush()
        session.add(ExecutionWorkerClaim(worker_id=lease.worker_id,
                                        family="workflow_execution", subject_id=subject))
        await session.commit()
    try:
        async with get_redis() as redis:
            await redis.set(active_execution_key(execution_id), json.dumps(metadata), ex=60)
        assert await append_log_to_stream_async(execution_id, "INFO", "canonical consumer proof")
        task = await journal.dispatch(lease, "route", execution_id, launch)
        assert (await task)["result"] == {"value": 70}
        monkeypatch.setattr(consumer, "_handle_result", lose_ack)
        with pytest.raises(ConnectionError, match="after canonical completion"):
            await journal.reconcile_workflow(lease, "route", consumer, execution)
        assert not journal.inventory()[0]["acknowledged"]
        async with async_session_factory() as session:
            row = await session.get(Execution, subject)
            assert row is not None and row.status == ExecutionStatus.SUCCESS
            assert row.result == {"value": 70}
        async with get_redis() as redis:
            assert not await redis.exists(active_execution_key(execution_id))
            assert not await redis.exists(RESULT_KEY_PREFIX + execution_id)
            assert await redis.xlen(execution_logs_stream_key(execution_id)) == 0

        # Same physical route survives reconnect. Upstream consumes its stale
        # terminal replay normally, which permits ACK without rewriting the run.
        replacement = await owner(2)
        monkeypatch.setattr(consumer, "_handle_result", original)
        assert await journal.dispatch(replacement, "route", execution_id, launch) is task
        assert await journal.reconcile_workflow(replacement, "route", consumer, execution)
        launch.assert_awaited_once()
        metrics.assert_awaited_once()
        async with async_session_factory() as session:
            logs = (await session.scalars(select(ExecutionLog).where(
                ExecutionLog.execution_id == subject))).all()
            assert [log.message for log in logs] == ["canonical consumer proof"]
        await journal.retire()
    finally:
        async with get_redis() as redis:
            await redis.delete(active_execution_key(execution_id), execution_logs_stream_key(execution_id),
                               RESULT_KEY_PREFIX + execution_id)
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionLog).where(ExecutionLog.execution_id == subject))
            await session.execute(delete(ExecutionWorkerClaim).where(ExecutionWorkerClaim.subject_id == subject))
            await session.execute(delete(Execution).where(Execution.id == subject))
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [
    {"execution_id": "another-execution", "success": True},
    {"execution_id": "execution", "success": "true"},
])
async def test_workflow_adapter_rejects_guest_identity_or_noncanonical_envelope(owner, result):
    lease = await owner(1)
    journal = AttemptJournal()
    task = await journal.dispatch(lease, "route", "execution", AsyncMock(return_value=result))
    assert await task == result
    consumer = SimpleNamespace(owner_worker_id=lease.worker_id, _handle_result=AsyncMock())
    execution = SimpleNamespace(execution_id="execution")
    with pytest.raises(ValueError):
        await journal.reconcile_workflow(lease, "route", consumer, execution)
    consumer._handle_result.assert_not_awaited()
    assert not journal.inventory()[0]["acknowledged"]


@pytest.mark.asyncio
async def test_workflow_adapter_rejects_another_workers_consumer_before_effects(owner):
    lease = await owner(1)
    journal = AttemptJournal()
    consumer = SimpleNamespace(owner_worker_id=uuid4(), _handle_result=AsyncMock())
    with pytest.raises(ValueError, match="another worker"):
        await journal.reconcile_workflow(lease, "route", consumer, SimpleNamespace())
    consumer._handle_result.assert_not_awaited()


@pytest.fixture
async def waiting_dispatch(owner, async_session_factory, monkeypatch):
    from src.core.redis_client import pending_execution_record
    from src.services import work_dispatch
    from src.services.execution import workflow_dispatch
    from src.services.worker_pool import claims

    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(claims, "get_db_context", context)
    monkeypatch.setattr(workflow_dispatch, "get_db_context", context)
    lease = await owner(1)
    subject = uuid4()
    payload = pending_execution_record(execution_id=str(subject), workflow_id=None,
        parameters={"proof": 70}, org_id=None, user_id=str(uuid4()), user_name="fixture",
        user_email="fixture@example.test", sync=True, lineage=None)
    async with async_session_factory() as session:
        session.add(Execution(id=subject, workflow_name="pyodide-dispatch-fixture",
                              executed_by_name="fixture", status=ExecutionStatus.PENDING))
        await session.flush()
        dispatch = work_dispatch.stage_dispatch(session, family="workflow_execution", subject_id=subject,
            handler="workflow.execute", handler_version=1, message={"execution_id": str(subject), "sync": True},
            payload=dict(payload), memory_request_bytes=128 * 2**20)
        await session.commit()
        queued_at = dispatch.created_at
    try:
        yield SimpleNamespace(lease=lease, subject=subject, payload=payload, queued_at=queued_at)
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(WorkDispatch).where(WorkDispatch.subject_id == subject))
            await session.execute(delete(ExecutionWorkerClaim).where(ExecutionWorkerClaim.subject_id == subject))
            await session.execute(delete(Execution).where(Execution.id == subject))
            await session.commit()


@pytest.mark.asyncio
async def test_canonical_claim_duplicate_and_prelaunch_handback(waiting_dispatch, async_session_factory):
    from src.services.execution.workflow_dispatch import release_workflow_run
    from src.services.worker_pool.claims import claim_next
    from src.worker.remote.fencing import LeaseRef, remote_owner_context

    fixture = waiting_dispatch
    with remote_owner_context(LeaseRef(fixture.lease)):
        claim = await claim_next(fixture.lease.worker_id, fits_bytes=2**30, budget_bytes=2**30)
        assert claim is not None and claim.subject_id == fixture.subject
        assert claim.claimed is not None and claim.claimed["parameters"] == {"proof": 70}
        assert await claim_next(fixture.lease.worker_id, fits_bytes=2**30, budget_bytes=2**30) is None
        await release_workflow_run(worker_id=fixture.lease.worker_id, message=claim.message,
            payload=claim.claimed, memory_request_bytes=claim.memory_request_bytes, queued_at=claim.queued_at)
    async with async_session_factory() as session:
        row = await session.get(Execution, fixture.subject)
        assert row is not None and row.status == ExecutionStatus.PENDING and row.started_at is None
        held = await session.scalar(select(ExecutionWorkerClaim).where(
            ExecutionWorkerClaim.subject_id == fixture.subject))
        assert held is None
        dispatch = await session.scalar(select(WorkDispatch).where(WorkDispatch.subject_id == fixture.subject))
        assert dispatch is not None and dispatch.created_at == fixture.queued_at
    with remote_owner_context(LeaseRef(fixture.lease)):
        again = await claim_next(fixture.lease.worker_id, fits_bytes=2**30, budget_bytes=2**30)
        assert again is not None and again.claimed == claim.claimed


@pytest.mark.asyncio
async def test_prelaunch_reservation_failure_rolls_back_canonical_claim(waiting_dispatch, async_session_factory):
    from src.services.worker_pool.claims import claim_next
    from src.worker.remote.fencing import LeaseRef, remote_owner_context

    def reject(_claim):
        raise MemoryError("fixture admission rejected before launch")

    fixture = waiting_dispatch
    with remote_owner_context(LeaseRef(fixture.lease)):
        with pytest.raises(MemoryError, match="before launch"):
            await claim_next(fixture.lease.worker_id, fits_bytes=2**30, budget_bytes=2**30,
                             reserve_claim=reject)
    async with async_session_factory() as session:
        row = await session.get(Execution, fixture.subject)
        assert row is not None and row.status == ExecutionStatus.PENDING
        assert await session.scalar(select(ExecutionWorkerClaim).where(
            ExecutionWorkerClaim.subject_id == fixture.subject)) is None
        dispatch = await session.scalar(select(WorkDispatch).where(WorkDispatch.subject_id == fixture.subject))
        assert dispatch is not None and dispatch.assigned_worker_id is None
