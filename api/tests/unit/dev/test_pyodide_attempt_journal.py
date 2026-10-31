"""Real PostgreSQL fencing; physical launch is a deterministic local callback.

No cloud invocation and no claim of canonical workflow result persistence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.repositories.executions import update_execution
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
                                    resource_class="standard", slots=1))
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
        await task

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
        await task
    assert await journal.reconcile_terminal(lease, "route", accept)
    await journal.retire()
