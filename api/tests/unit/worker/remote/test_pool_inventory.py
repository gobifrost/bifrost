"""A physical route can survive an API process without becoming a new run."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.models.enums import ExecutionStatus
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.models.orm.executions import Execution
from src.worker.remote.remote_pool import ThinRemoteProcessPool


@pytest.mark.asyncio
async def test_adopts_live_and_unacknowledged_workflow_routes_with_original_metadata(
    async_session_factory, monkeypatch,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr("src.core.database.get_db_context", context)
    worker_id = uuid4()
    executions = [uuid4(), uuid4()]
    async with async_session_factory() as session:
        session.add(ExecutionWorker(
            id=worker_id, form="static", state="ready", resource_class="standard", slots=0,
        ))
        session.add_all([Execution(
            id=subject, workflow_name="recovery", executed_by_name="test",
            status=ExecutionStatus.RUNNING,
        ) for subject in executions])
        await session.flush()
        session.add_all([ExecutionWorkerClaim(
            worker_id=worker_id, family="workflow_execution", subject_id=subject,
        ) for subject in executions])
        await session.commit()
    transport = AsyncMock()
    pool = ThinRemoteProcessPool(transport, worker_id=str(worker_id))
    pool._write_active_execution_lease = AsyncMock()
    entries = [{
        "route_id": "workflow:" + str(subject), "kind": "workflow", "subject_id": str(subject),
        "pid": 12345, "memory_request_bytes": 128 * 2**20,
        "started_at": datetime.now(timezone.utc).isoformat(), "timeout_seconds": 300,
        "active_execution": {"execution_id": str(subject), "workflow_id": None,
                             "workflow_name": "recovery", "org_id": None, "user_id": None,
                             "user_name": "test", "user_email": None, "sync": True,
                             "event": {"delivery_id": "keep-original-event"}},
    } for subject in executions]
    try:
        snapshot = await pool.reconcile_inventory({
            "version": 1, "routes": entries[:1], "terminal_routes": entries[1:],
        })
        assert snapshot["workflow_requests"] == {str(subject): 128 * 2**20 for subject in executions}
        assert len(pool.processes) == 2
        for handle in pool.processes.values():
            assert handle.pid is None
            assert handle.process.remote_pid == 12345
            assert handle.current_execution.active_execution["sync"] is True
            assert handle.current_execution.active_execution["event"] == {"delivery_id": "keep-original-event"}
        transport.request.assert_not_awaited()
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await session.execute(delete(Execution).where(Execution.id.in_(executions)))
            await session.commit()


@pytest.mark.asyncio
async def test_inventory_cannot_adopt_a_workflow_claimed_by_another_worker(
    async_session_factory, monkeypatch,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr("src.core.database.get_db_context", context)
    transport = AsyncMock()
    pool = ThinRemoteProcessPool(transport, worker_id=str(uuid4()))
    route_id = "stale-route"
    snapshot = await pool.reconcile_inventory({"version": 1, "routes": [{
        "route_id": route_id, "kind": "workflow", "subject_id": str(uuid4()),
        "memory_request_bytes": 128 * 2**20,
    }], "terminal_routes": []})
    assert snapshot["workflow_requests"] == {}
    assert pool.processes == {}
    transport.request.assert_awaited_once()
    assert transport.request.await_args.args[0] == "terminate"
    assert transport.request.await_args.args[1]["route_id"] == route_id
