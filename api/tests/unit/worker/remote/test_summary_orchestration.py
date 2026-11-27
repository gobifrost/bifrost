"""Summary recovery retires CPU before settling the exact claimed attempt."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.worker.remote import agent_orchestration, summary_orchestration
from src.worker.remote.fencing import LeaseRef


@pytest.mark.asyncio
async def test_summary_recovery_releases_before_settling_its_exact_claim(monkeypatch):
    worker_id, run_id, claim_id = uuid4(), uuid4(), uuid4()
    stage = {
        "stage_id": str(uuid4()), "route_id": str(uuid4()), "run_id": str(run_id),
        "dispatch_id": str(uuid4()), "input_sha256": "a" * 64,
        "compute_kind": "summary", "summary_claim_id": str(claim_id),
    }
    lease = SimpleNamespace(worker_id=worker_id)
    bridge = SimpleNamespace(
        lease=lease, lease_ref=LeaseRef(lease),
        inventory={"agent_compute_stages": [stage]}, agent_compute_runtimes={},
    )
    order = []

    async def release(_bridge, binding):
        assert binding["stage_id"] == stage["stage_id"]
        order.append("physical stop")

    async def fail(**kwargs):
        assert order == ["physical stop"]
        assert kwargs["worker_id"] == worker_id
        assert kwargs["run_id"] == run_id
        assert kwargs["claim_id"] == claim_id
        order.append("canonical settlement")

    monkeypatch.setattr(agent_orchestration, "_release_orphan_stage", release)
    monkeypatch.setattr(summary_orchestration, "fail_remote_summary", fail)
    # A summary's original completed agent run must not be failed by the agent
    # reconciler; the summary claim is its separate authority boundary.
    no_agent_settlement = AsyncMock()
    monkeypatch.setattr(agent_orchestration, "announce_ended_agent_run", no_agent_settlement)
    await agent_orchestration.reconcile_agent_computes(bridge)
    no_agent_settlement.assert_not_awaited()
    await summary_orchestration.reconcile_summary_computes(bridge)
    assert order == ["physical stop", "canonical settlement"]


@pytest.mark.asyncio
async def test_api_summary_retains_canonical_deadline_and_exact_failure_claim(monkeypatch):
    import asyncio

    worker_id, run_id, claim_id, dispatch_id = (uuid4() for _ in range(4))
    lease = SimpleNamespace(worker_id=worker_id)
    bridge = SimpleNamespace(
        lease=lease, lease_ref=LeaseRef(lease),
        memory_request_bytes=lambda _dispatch: 1024, agent_compute_runtimes={},
    )
    unfinished = asyncio.Event()

    async def blocked(_body, *, worker_id):
        await unfinished.wait()

    failed = AsyncMock()
    monkeypatch.setattr(summary_orchestration, "handle_summarize_message", blocked)
    monkeypatch.setattr(summary_orchestration, "run_timeout_seconds", AsyncMock(return_value=0))
    monkeypatch.setattr(summary_orchestration, "DEADLINE_GRACE_SECONDS", 0)
    monkeypatch.setattr(summary_orchestration, "fail_remote_summary", failed)
    consumer = summary_orchestration.ApiSummaryComputeConsumer(bridge, worker_id=worker_id)
    await consumer.process_message({"run_id": str(run_id), "dispatch_id": str(dispatch_id), "claim_id": str(claim_id)})
    failed.assert_awaited_once()
    assert failed.await_args.kwargs["claim_id"] == claim_id
    assert failed.await_args.kwargs["worker_id"] == worker_id
    assert not consumer.active
