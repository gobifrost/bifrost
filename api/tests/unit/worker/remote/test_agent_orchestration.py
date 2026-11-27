"""API ownership and recovery contracts for physical agent compute."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from src.services.agent_runtime.compute import AgentRuntimePlan
from src.worker.remote import agent_orchestration as orchestration
from src.worker.remote.agent_compute_host import AgentComputeBinding
from src.worker.remote.fencing import LeaseRef, RemoteOwnerLost


@asynccontextmanager
async def _permit():
    yield


def _plan(*, surface: str, runtime_id: UUID) -> AgentRuntimePlan:
    return AgentRuntimePlan(
        surface=surface,  # type: ignore[arg-type]
        prompt="Do one thing.",
        definitions=(),
        composition_snapshot=None,
        max_requests=4,
        max_total_tokens=None,
        warning_threshold=0.7,
        initial_requests=0,
        initial_total_tokens=0,
        context_window=None,
        automatic_code_mode=False,
        runtime_id=str(runtime_id),
        toolset_id="agent-orchestration-test",
        empty_output_guard=False,
    )


class _Bridge:
    def __init__(self) -> None:
        self.lease: Any = SimpleNamespace(worker_id=uuid4())
        self.lease_ref = LeaseRef(self.lease)
        self.agent_compute_runtimes: dict[str, object] = {}
        self.calls: list[tuple[str, dict]] = []
        self.inventory: dict = {}

    def memory_request_bytes(self, dispatch_id: str) -> int | None:
        del dispatch_id
        return 1024

    async def request(self, action, payload, *, authorization):
        async with authorization:
            self.calls.append((action, payload))
            return dict(payload)


def _callbacks(model):
    async def execute_tool(*_args):
        return {"ok": True}

    return {
        "model": model,
        "executor": execute_tool,
        "event_handler": None,
        "model_settings": None,
    }


def test_builder_uses_root_for_chat_and_plan_runtime_for_autonomous_stages():
    bridge = _Bridge()
    root, delegated, dispatch = uuid4(), uuid4(), uuid4()
    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("done")]))
    builder = orchestration.ApiAgentRuntimeBuilder(
        bridge, root_run_id=root, dispatch_id=dispatch, memory_request_bytes=1024
    )

    chat = builder(_plan(surface="chat", runtime_id=uuid4()), **_callbacks(model))
    autonomous = builder(
        _plan(surface="autonomous", runtime_id=delegated), **_callbacks(model)
    )

    assert chat.runtime.binding.run_id == str(root)
    assert autonomous.runtime.binding.run_id == str(delegated)
    assert chat.runtime.binding.dispatch_id == str(dispatch)
    assert UUID(chat.runtime.binding.route_id)
    assert chat.runtime.binding.route_id != str(bridge.lease.worker_id)
    assert chat.runtime.binding.route_id != autonomous.runtime.binding.route_id
    assert builder.active_run_ids == {root, delegated}


@pytest.mark.asyncio
async def test_rpc_authorization_refuses_revoked_agent_before_provider_work(monkeypatch):
    bridge = _Bridge()
    root, agent = uuid4(), uuid4()
    run = SimpleNamespace(
        id=root, status="running", parent_run_id=None, agent_id=agent, trigger_type="event"
    )
    db = SimpleNamespace(get=AsyncMock(return_value=run), scalar=AsyncMock(return_value=uuid4()))

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(orchestration, "get_db_context", database)
    monkeypatch.setattr(orchestration, "require_remote_owner", AsyncMock())
    revoked = AsyncMock(side_effect=RemoteOwnerLost("agent permission revoked"))
    monkeypatch.setattr(orchestration, "authorize_agent_run_continuation", revoked)

    with pytest.raises(RemoteOwnerLost, match="revoked"):
        await orchestration._authorize_rpc(
            bridge, root_run_id=root, active_run_id=root
        )

    revoked.assert_awaited_once_with(db, root)


@pytest.mark.asyncio
async def test_reconciliation_stops_untracked_stage_without_ending_unmatched_run(monkeypatch):
    bridge = _Bridge()
    binding = AgentComputeBinding(
        str(uuid4()), str(bridge.lease.worker_id), str(uuid4()), str(uuid4())
    )
    digest = "a" * 64
    bridge.inventory = {
        "agent_compute_stages": [
            {**binding.to_dict(), "input_sha256": digest, "state": "running"}
        ]
    }
    bridge.request = AsyncMock(side_effect=[
        {**binding.to_dict(), "input_sha256": digest, "state": "running"},
        {**binding.to_dict(), "input_sha256": digest, "released": True},
    ])
    ended = AsyncMock()
    monkeypatch.setattr(orchestration, "_owner_send_authorization", lambda _bridge: _permit())
    monkeypatch.setattr(orchestration, "_claimed_orphan_lineage", AsyncMock(return_value=None))
    monkeypatch.setattr(orchestration, "announce_ended_agent_run", ended)

    await orchestration.reconcile_agent_computes(bridge)

    assert [call.args[0] for call in bridge.request.await_args_list] == [
        "agent_compute_status", "agent_compute_release"
    ]
    ended.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_releases_orphan_before_ending_its_claimed_lineage(monkeypatch):
    bridge = _Bridge()
    root, child = uuid4(), uuid4()
    binding = AgentComputeBinding(
        str(uuid4()), str(bridge.lease.worker_id), str(child), str(uuid4())
    )
    digest = "c" * 64
    bridge.inventory = {
        "agent_compute_stages": [
            {**binding.to_dict(), "input_sha256": digest, "state": "running"}
        ]
    }
    timeline: list[str] = []

    async def request(action, payload, *, authorization):
        async with authorization:
            timeline.append(action)
            if action == "agent_compute_status":
                return {**binding.to_dict(), "input_sha256": digest, "state": "running"}
            return {**binding.to_dict(), "input_sha256": digest, "released": True}

    async def ended(run_id, error):
        assert error.startswith("The API agent orchestrator")
        timeline.append(f"ended:{run_id}")
        return True

    bridge.request = request
    monkeypatch.setattr(orchestration, "_owner_send_authorization", lambda _bridge: _permit())
    monkeypatch.setattr(orchestration, "_claimed_orphan_lineage", AsyncMock(return_value=(child, root)))
    monkeypatch.setattr(orchestration, "announce_ended_agent_run", ended)

    await orchestration.reconcile_agent_computes(bridge)

    assert timeline == [
        "agent_compute_status", "agent_compute_release", f"ended:{root}", f"ended:{child}"
    ]


@pytest.mark.asyncio
async def test_reconciliation_retries_tracked_release_before_claims_open():
    bridge = _Bridge()
    binding = AgentComputeBinding(
        str(uuid4()), str(bridge.lease.worker_id), str(uuid4()), str(uuid4())
    )
    digest = "b" * 64
    local = SimpleNamespace(
        binding=binding,
        _input_sha256=digest,
        release_requested=True,
        release=AsyncMock(),
    )
    bridge.agent_compute_runtimes[binding.stage_id] = local
    bridge.inventory = {
        "agent_compute_stages": [
            {**binding.to_dict(), "input_sha256": digest, "state": "running"}
        ]
    }

    await orchestration.reconcile_agent_computes(bridge)

    local.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_runs_canonical_handler_under_builder_and_keeps_legacy_routes_empty(monkeypatch):
    bridge = _Bridge()
    root, dispatch = uuid4(), uuid4()
    observed: dict[str, object] = {}

    class Canonical:
        async def process_message(self, body):
            from src.services.agent_runtime.compute_execution import build_agent_runtime

            model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("done")]))
            observed["runtime"] = build_agent_runtime(
                _plan(surface="autonomous", runtime_id=UUID(body["run_id"])),
                **_callbacks(model),
            ).runtime

    monkeypatch.setattr(orchestration, "AgentRunConsumer", Canonical)
    consumer = orchestration.ApiComputeAgentConsumer(bridge, worker_id=bridge.lease.worker_id)

    await consumer.process_message({"run_id": str(root), "dispatch_id": str(dispatch)})

    runtime = cast(Any, observed["runtime"])
    assert runtime.binding.run_id == str(root)
    assert consumer.routes == {}
    assert consumer.active == {}
    assert bridge.agent_compute_runtimes == {}


@pytest.mark.asyncio
async def test_cancelled_waiter_keeps_api_orchestration_tracked_until_the_run_exits(monkeypatch):
    bridge = _Bridge()
    root, dispatch = uuid4(), uuid4()
    started, finish = asyncio.Event(), asyncio.Event()

    class Canonical:
        async def process_message(self, _body):
            started.set()
            await finish.wait()

    monkeypatch.setattr(orchestration, "AgentRunConsumer", Canonical)
    consumer = orchestration.ApiComputeAgentConsumer(bridge, worker_id=bridge.lease.worker_id)
    waiter = asyncio.create_task(
        consumer.process_message({"run_id": str(root), "dispatch_id": str(dispatch)})
    )
    await started.wait()

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert root in consumer.active

    orchestration_task = consumer.active[root].task
    finish.set()
    await orchestration_task
    assert root not in consumer.active


@pytest.mark.asyncio
async def test_handoff_abandons_api_callbacks_without_a_worker_command():
    bridge = _Bridge()
    root = uuid4()
    consumer = orchestration.ApiComputeAgentConsumer(bridge, worker_id=bridge.lease.worker_id)
    never = asyncio.Event()
    task = asyncio.create_task(never.wait())
    builder = SimpleNamespace(abandon_all=AsyncMock())
    consumer.active[root] = orchestration._ActiveOrchestration(root, builder, task)

    await consumer.abandon_orchestrations()

    builder.abandon_all.assert_awaited_once_with()
    assert consumer.active == {}
    assert bridge.calls == []
