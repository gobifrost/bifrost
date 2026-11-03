"""A viewer receives durable queue positions across the shared Redis transport."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from websockets.asyncio.client import connect

from src.core.pubsub import ConnectionManager
from src.models.enums import ExecutionStatus
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch
from src.services.execution.workflow_dispatch import FAMILY


async def _receive(ws, kind: str) -> dict:
    async with asyncio.timeout(5):
        while True:
            message = json.loads(await ws.recv())
            if message.get("type") == kind:
                return message


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_viewer_receives_positions_after_subscribe_and_remote_queue_change(
    async_session_factory, e2e_ws_url, platform_admin, human_minter,
) -> None:
    """No workflow job is launched: only durable queue and UI transport are needed."""
    first, watched = uuid4(), uuid4()
    holding_worker = uuid4()
    base = datetime(2000, 1, 1, tzinfo=timezone.utc)
    producer = ConnectionManager()
    try:
        async with async_session_factory() as db:
            # A live assigned executor keeps the real pool/reconciler from
            # consuming these queue fixtures. No launcher or job is started.
            db.add(ExecutionWorker(
                id=holding_worker, form="static", state="ready", slots=0,
                supported_handlers=[], heartbeat_at=datetime.now(timezone.utc),
            ))
            for index, execution_id in enumerate((first, watched)):
                db.add(Execution(
                    id=execution_id, workflow_name="queue-position-live-test",
                    status=ExecutionStatus.PENDING,
                    executed_by=platform_admin.user_id, executed_by_name="Queue Viewer",
                ))
                db.add(WorkDispatch(
                    family=FAMILY, subject_id=execution_id,
                    handler="workflow.execute", handler_version=1,
                    message={}, payload={}, memory_request_bytes=1,
                    created_at=base + timedelta(seconds=index),
                    assigned_worker_id=holding_worker,
                ))
            await db.commit()

        human_minter(platform_admin)
        async with connect(
            f"{e2e_ws_url}/ws/connect",
            additional_headers=platform_admin.headers,
        ) as ws:
            await _receive(ws, "connected")
            await ws.send(json.dumps({"type": "subscribe", "channels": [f"execution:{watched}"]}))
            await _receive(ws, "subscribed")
            initial = await _receive(ws, "execution_queue_update")
            assert initial == {
                "type": "execution_queue_update", "executionId": str(watched),
                "queuePosition": 2, "waitReason": "queued",
            }

            async with async_session_factory() as db:
                await db.execute(update(Execution).where(Execution.id == first).values(
                    status=ExecutionStatus.CANCELLED,
                ))
                await db.execute(delete(WorkDispatch).where(WorkDispatch.subject_id == first))
                await db.commit()
            # This producer runs in the test process. The receiving API must
            # observe its coalesced Redis invalidation, query and deliver locally.
            producer.request_queue_position_refresh(announce=True)
            advanced = await _receive(ws, "execution_queue_update")
            assert advanced["executionId"] == str(watched)
            assert advanced["queuePosition"] == 1
            assert "status" not in advanced
    finally:
        await producer.close()
        async with async_session_factory() as db:
            await db.execute(delete(WorkDispatch).where(WorkDispatch.subject_id.in_((first, watched))))
            await db.execute(delete(Execution).where(Execution.id.in_((first, watched))))
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == holding_worker))
            await db.commit()
