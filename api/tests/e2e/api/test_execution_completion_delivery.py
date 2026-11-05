"""Durable completion reaches app transports before bookkeeping. Zero jobs."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from websockets.asyncio.client import connect

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.models.enums import ExecutionStatus
from src.models.orm import Execution


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_app_can_fetch_committed_result_while_completion_metrics_are_blocked(
    async_session_factory,
    e2e_client,
    e2e_ws_url,
    platform_admin,
    human_minter,
    monkeypatch,
):
    """Exercise the consumer -> Redis -> API WebSocket -> result GET boundary."""
    execution_id = uuid4()
    expected = {"message": "ready before bookkeeping"}
    metrics_started = asyncio.Event()
    allow_metrics = asyncio.Event()
    completion = None

    async def blocked_metrics(**_kwargs):
        metrics_started.set()
        await allow_metrics.wait()

    consumer = WorkflowExecutionConsumer(pool=MagicMock())
    monkeypatch.setattr(consumer, "_record_completion_metrics", blocked_metrics)
    monkeypatch.setattr(
        "src.core.database.get_session_factory", lambda: async_session_factory
    )
    # This test creates no event dispatch or worker execution. The canonical
    # completion transaction and both app-facing transports remain real.
    monkeypatch.setattr(
        "src.services.events.builtins.emit_workflow_completed_event", AsyncMock()
    )

    async with async_session_factory() as session:
        session.add(Execution(
            id=execution_id,
            workflow_name="completion-delivery",
            status=ExecutionStatus.RUNNING,
            executed_by=UUID(str(platform_admin.user_id)),
            executed_by_name="Completion delivery test",
        ))
        await session.commit()

    try:
        human_minter(platform_admin)
        async with connect(
            f"{e2e_ws_url}/ws/connect",
            additional_headers=platform_admin.headers,
        ) as ws:
            connected = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert connected["type"] == "connected"
            channel = f"execution:{execution_id}"
            await ws.send(json.dumps({"type": "subscribe", "channels": [channel]}))
            subscribed = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert subscribed["type"] == "subscribed", subscribed

            completion = asyncio.create_task(consumer._process_success(
                str(execution_id),
                {"sync": False, "status": "Success", "result": expected, "duration_ms": 1},
            ))
            await asyncio.wait_for(metrics_started.wait(), timeout=5)
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert frame["type"] == "execution_update", frame
            assert frame["status"] == "Success", frame
            assert "result" not in frame
            assert not completion.done()

            response = await asyncio.to_thread(
                e2e_client.get,
                f"/api/executions/{execution_id}",
                headers=platform_admin.headers,
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "Success"
            assert response.json()["result"] == expected
            assert not completion.done()
    finally:
        allow_metrics.set()
        try:
            if completion is not None:
                await asyncio.gather(completion)
        finally:
            async with async_session_factory() as session:
                await session.execute(delete(Execution).where(Execution.id == execution_id))
                await session.commit()
