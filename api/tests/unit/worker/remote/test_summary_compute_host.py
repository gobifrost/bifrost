"""A real physical summary child uses only the API-owned completion callback."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from shared.agent_summary_compute import SummaryCompletion, SummaryComputeInput
from src.worker.remote.compute_stage_host import AgentComputeBinding
from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.launcher import Launcher
from src.worker.remote.summary_compute_host import RemoteSummaryRuntime


@asynccontextmanager
async def _permit():
    yield


@pytest.mark.asyncio
async def test_summary_api_callback_and_real_compute_child_share_one_completion():
    launcher = Launcher(
        AsyncMock(), identity=LauncherIdentity(
            launcher_id=uuid4(), incarnation_id=uuid4(), job_uid=None,
            pod_uid=None, hostname="summary-compute-test", form="external", resource_uid="test-resource",
        ), secret="scoped-test-key",
    )
    launcher.pressure_threshold, launcher.dangerous_memory_ratio = .9, .95
    binding = AgentComputeBinding(*(str(uuid4()) for _ in range(4)))
    bridge = SimpleNamespace(
        connected=True, reconciliation_ready=asyncio.Event(),
        transport_changed=asyncio.Event(),
        agent_compute_runtimes={},
    )
    bridge.reconciliation_ready.set()

    async def request(action, payload, *, authorization):
        async with authorization:
            if action == "agent_compute_status":
                return launcher.agent_compute_status(payload)
            if action == "agent_compute_start":
                return await launcher.agent_compute_start(payload)
            if action == "agent_compute_reply":
                return await launcher.agent_compute_reply(payload)
            if action == "agent_compute_release":
                return await launcher.agent_compute_release(payload)
            raise AssertionError(action)

    bridge.request = request
    completion = SummaryCompletion(
        content='{"asked":"Test the boundary","did":"Used the API callback.","answered":"Passed"}',
        model="api-summary-model", input_tokens=13, output_tokens=7, provider_cost="0.0002",
    )
    complete = AsyncMock(return_value=completion)
    claim_id = uuid4()
    runtime = RemoteSummaryRuntime(
        SummaryComputeInput("Fixture", "Summarize this run.", {"job": 1400}, {"ok": True}, "api-summary-model"),
        complete, bridge, binding, claim_id=claim_id,
        authorize_rpc=AsyncMock(), authorize_send=_permit, memory_request_bytes=1024,
    )
    events = []

    async def receive(event):
        events.append(event)
        if event["event"] == "agent_compute_rpc":
            await runtime.handle_rpc(event)
        elif event["event"] == "agent_compute_state":
            runtime.handle_state(event)
        else:
            raise AssertionError(event["event"])

    launcher.send = receive
    try:
        await launcher.start_agent_compute_template()
        assert launcher.agent_template is None
        async with asyncio.timeout(30):
            result = await runtime.run()
        assert result.fields is not None and result.fields.answered == "Passed"
        assert result.completion == completion
        complete.assert_awaited_once()
        callback = next(event for event in events if event["event"] == "agent_compute_rpc")
        await runtime.handle_rpc(callback)
        complete.assert_awaited_once()
        inventory = launcher.inventory()["agent_compute_stages"][0]
        assert inventory["compute_kind"] == "summary"
        assert inventory["summary_claim_id"] == str(claim_id)
        await runtime.release()
        assert not launcher.agent_compute_stages
    finally:
        await launcher.stop()
