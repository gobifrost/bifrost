"""API authority, replay, and stream contracts for remote agent compute."""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from uuid import uuid4
from decimal import Decimal

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    PartStartEvent,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage, RunUsage

from shared.agent_model_compute import serialize_model_request
from src.services.agent_runtime.compute import AgentRuntimePlan
from src.worker.remote.agent_compute import encode_runtime_event
from src.worker.remote.agent_compute_host import (
    AgentComputeBinding,
    RemoteAgentRuntime,
    RemoteAgentRuntimeError,
    RuntimeComputeResultEvent,
)


@asynccontextmanager
async def permit():
    yield


class Bridge:
    def __init__(self, binding: AgentComputeBinding, *, lose_start_reply: bool = False):
        self.binding = binding
        self.agent_compute_runtimes = {}
        self.connected = True
        self.reconciliation_ready = asyncio.Event()
        self.reconciliation_ready.set()
        self.transport_changed = asyncio.Event()
        self.status = {
            **binding.to_dict(),
            "state": "unknown",
            "memory_request_bytes": 0,
            "pid": None,
            "pending_rpc_count": 0,
        }
        self.replies: list[dict] = []
        self.calls: list[str] = []
        self.lose_start_reply = lose_start_reply

    async def request(self, action, payload, *, authorization):
        async with authorization:
            self.calls.append(action)
            if action == "agent_compute_status":
                return {**self.status, **payload}
            if action == "agent_compute_start":
                self.status = {
                    **self.status,
                    **{key: payload[key] for key in self.binding.to_dict()},
                    "input_sha256": payload["input_sha256"],
                    "state": "running",
                    "memory_request_bytes": payload["memory_request_bytes"],
                }
                if self.lose_start_reply:
                    self.lose_start_reply = False
                    raise TimeoutError("accepted but reply was lost")
                return dict(self.status)
            if action == "agent_compute_reply":
                self.replies.append(payload)
                return {
                    **{key: payload[key] for key in self.binding.to_dict()},
                    "input_sha256": payload["input_sha256"],
                    "request_id": payload["request_id"],
                    "accepted": True,
                }
            if action == "agent_compute_release":
                self.status["state"] = "released"
                return {**self.binding.to_dict(), "released": True}
        raise AssertionError(action)


def plan(*, surface="autonomous", max_total_tokens=None):
    return AgentRuntimePlan(
        surface=surface,
        prompt="Do one thing.",
        definitions=(),
        composition_snapshot=None,
        max_requests=4,
        max_total_tokens=max_total_tokens,
        warning_threshold=0.7,
        initial_requests=0,
        initial_total_tokens=0,
        context_window=None,
        automatic_code_mode=False,
        runtime_id=str(uuid4()),
        toolset_id="host-test",
        empty_output_guard=False,
    )


def binding_for(plan, model, *, prompt="go", history=None):
    del plan, model, prompt, history
    return AgentComputeBinding(
        str(uuid4()),
        "route-1",
        str(uuid4()),
        str(uuid4()),
    )


def model_payload():
    return serialize_model_request(
        [ModelRequest(parts=[UserPromptPart("go")])], ModelRequestParameters()
    )


def rpc(binding, request_id, op, payload, *, stream=False):
    identity = binding if isinstance(binding, dict) else binding.to_dict()
    return {
        "event": "agent_compute_rpc",
        **identity,
        "request_id": request_id,
        "op": op,
        "payload": payload,
        "stream": stream,
    }


def runtime(plan, bridge, binding, model, usage=None, guard=None):
    return RemoteAgentRuntime(
        plan,
        bridge,
        binding,
        trusted_model=model,
        trusted_model_settings=None,
        execute_tool=lambda *_: asyncio.sleep(0, result={"ok": True}),
        tool_event=None,
        capability_load=lambda _: asyncio.sleep(0),
        usage=usage or RunUsage(),
        empty_output_guard=guard,
        authorize_rpc=lambda: asyncio.sleep(0),
        authorize_send=permit,
        memory_request_bytes=1024,
    )


def test_constructor_accepts_only_the_four_stable_binding_fields():
    model = FunctionModel(
        lambda *_: ModelResponse(parts=[TextPart("done")]), model_name="api-model"
    )
    runtime_plan = plan()
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)

    host = runtime(runtime_plan, bridge, stage_binding.to_dict(), model)

    assert bridge.agent_compute_runtimes[stage_binding.stage_id] is host


@pytest.mark.asyncio
async def test_release_before_start_unregisters_without_physical_command():
    model = FunctionModel(
        lambda *_: ModelResponse(parts=[TextPart("done")]), model_name="api-model"
    )
    runtime_plan = plan()
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    host = runtime(runtime_plan, bridge, stage_binding, model)

    await host.release()

    assert bridge.calls == []
    assert stage_binding.stage_id not in bridge.agent_compute_runtimes


@pytest.mark.asyncio
async def test_model_rpc_is_authoritative_and_duplicate_request_replays_receipt():
    calls = 0

    def respond(_messages, _info):
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[TextPart("done")],
            usage=RequestUsage(input_tokens=3, output_tokens=2),
        )

    model = FunctionModel(respond, model_name="api-model")
    runtime_plan = plan()
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    host = runtime(runtime_plan, bridge, stage_binding, model)
    await host._start("go", [])
    request_id = str(uuid4())
    event = rpc(
        host._bound_binding(),
        request_id,
        "model_request",
        {"payload_b64": base64.b64encode(model_payload()).decode()},
    )

    await host.handle_rpc(event)
    await host.handle_rpc(event)

    assert calls == 1
    assert host.usage.requests == 1
    assert host.usage.total_tokens == 5
    assert len(bridge.replies) == 2
    assert bridge.replies[0]["reply_frame"] == bridge.replies[1]["reply_frame"]


@pytest.mark.asyncio
async def test_start_reads_back_lost_reply_without_second_physical_launch():
    model = FunctionModel(
        lambda *_: ModelResponse(parts=[TextPart("done")]), model_name="api-model"
    )
    runtime_plan = plan()
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding, lose_start_reply=True)
    host = runtime(runtime_plan, bridge, stage_binding, model)

    await host._start("go", [])

    assert bridge.calls.count("agent_compute_start") == 1
    assert bridge.status["state"] == "running"


@pytest.mark.asyncio
async def test_second_run_invocation_is_refused_after_terminal_result():
    model = FunctionModel(
        lambda *_: ModelResponse(parts=[TextPart("done")]), model_name="api-model"
    )
    runtime_plan = plan()
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    bridge.status["terminal"] = {
        "ok": True,
        "result": {
            "output": "done",
            "usage": asdict(RunUsage()),
            "empty_output_guard": None,
        },
    }
    host = runtime(runtime_plan, bridge, stage_binding, model)

    assert (await host.run("go")).output == "done"
    with pytest.raises(RemoteAgentRuntimeError, match="only once"):
        await host.run("different prompt")


@pytest.mark.asyncio
async def test_wrong_binding_is_refused_before_model_execution():
    calls = 0

    def respond(*_):
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("done")])

    model = FunctionModel(respond, model_name="api-model")
    runtime_plan = plan()
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    host = runtime(runtime_plan, bridge, stage_binding, model)
    await host._start("go", [])
    event = rpc(
        host._bound_binding(),
        str(uuid4()),
        "model_request",
        {"payload_b64": base64.b64encode(model_payload()).decode()},
    )
    event["run_id"] = str(uuid4())

    with pytest.raises(RemoteAgentRuntimeError, match="binding changed"):
        await host.handle_rpc(event)
    assert calls == 0


@pytest.mark.asyncio
async def test_chat_event_ack_waits_for_consumer_advance_then_returns_terminal_result():
    model = FunctionModel(
        lambda *_: ModelResponse(parts=[TextPart("done")]), model_name="api-model"
    )
    runtime_plan = plan(surface="chat")
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    host = runtime(runtime_plan, bridge, stage_binding, model)

    async with host.run_stream_events("go") as events:
        pending = asyncio.create_task(host._publish_runtime_event(
            PartStartEvent(index=0, part=TextPart("first"))
        ))
        received = await anext(events)
        assert isinstance(received, PartStartEvent)
        assert not pending.done()
        bridge.status["terminal"] = {
            "ok": True,
            "result": {
                "output": "done",
                "usage": asdict(RunUsage()),
                "empty_output_guard": None,
            },
        }
        host.handle_state({"event": "agent_compute_state", **host._bound_binding()})
        final = await anext(events)
        assert isinstance(final, RuntimeComputeResultEvent)
        assert final.result.output == "done"
        await pending

    assert not bridge.replies


@pytest.mark.asyncio
async def test_api_host_completes_against_real_launcher_and_compute_child(monkeypatch):
    """Exercise the shared wire lifecycle instead of a mocked stage machine."""
    from unittest.mock import AsyncMock

    from src.worker.remote.identity import LauncherIdentity
    from src.worker.remote.launcher import Launcher

    model = FunctionModel(
        lambda *_: ModelResponse(
            parts=[TextPart("real compute completed")],
            usage=RequestUsage(input_tokens=3, output_tokens=2, cost=Decimal("0.0002")),
        ),
        model_name="api-model",
    )
    runtime_plan = plan()
    binding = binding_for(runtime_plan, model)
    launcher = Launcher(
        AsyncMock(),
        identity=LauncherIdentity(
            launcher_id=uuid4(), incarnation_id=uuid4(), job_uid=None,
            pod_uid=None, hostname="contract-test", form="static",
        ),
        secret="test-control-secret",
    )
    launcher.pressure_threshold = 0.9
    launcher.dangerous_memory_ratio = 0.95

    class PhysicalBridge(Bridge):
        async def request(self, action, payload, *, authorization):
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

    bridge = PhysicalBridge(binding)
    usage = RunUsage(cost=Decimal("0.0001"))
    proxy = runtime(runtime_plan, bridge, binding, model, usage)

    async def receive(event):
        if event["event"] == "agent_compute_rpc":
            await proxy.handle_rpc(event)
        elif event["event"] == "agent_compute_state":
            proxy.handle_state(event)
        else:
            raise AssertionError(event["event"])

    monkeypatch.setattr(launcher, "send", receive)
    try:
        await launcher.start_agent_compute_template()
        async with asyncio.timeout(30):
            result = await proxy.run("go")
        assert result.output == "real compute completed"
        assert usage.input_tokens == 3
        assert usage.output_tokens == 2
        assert usage.cost == Decimal("0.0003")
        status = launcher.agent_compute_status(proxy._bound_binding())
        assert status["state"] == "terminal"
        assert status["terminal"]["result"]["usage"]["cost"] == "0.0003"
        await proxy.release()
        assert launcher.agent_compute_status(proxy._bound_binding())["state"] == "released"
        assert not launcher.agent_compute_stages
    finally:
        await launcher.stop()


@pytest.mark.asyncio
async def test_delegated_effect_preserves_decimal_cost_in_usage_delta():
    from shared.agent_model_compute import deserialize_run_usage

    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("unused")]), model_name="api-model")
    runtime_plan = plan()
    binding = binding_for(runtime_plan, model)
    bridge = Bridge(binding)
    usage = RunUsage(cost=Decimal("0.0001"))
    proxy = runtime(runtime_plan, bridge, binding, model, usage)

    async def delegated(*_args):
        usage.incr(RunUsage(requests=1, input_tokens=4, cost=Decimal("0.0002")))
        return {"done": True}

    proxy.execute_tool = delegated
    result = await proxy._rpc_result("effect", {"name": "delegate", "arguments": {}, "tool_call_id": "call-1"})
    delta = deserialize_run_usage(json.loads(json.dumps(result))["usage_delta"])
    assert delta.cost == Decimal("0.0002")
    assert delta.requests == 1
    assert delta.input_tokens == 4


def test_failed_compute_restores_guard_from_retained_terminal_result():
    from pydantic_ai.exceptions import UsageLimitExceeded
    from src.services.agent_runtime.empty_output import EmptyOutputCircuitBreaker

    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("unused")]), model_name="api-model")
    runtime_plan = replace(plan(), empty_output_guard=True)
    binding = binding_for(runtime_plan, model)
    guard = EmptyOutputCircuitBreaker()
    proxy = runtime(runtime_plan, Bridge(binding), binding, model, guard=guard)
    with pytest.raises(UsageLimitExceeded):
        proxy._terminal_result({
            "ok": False, "error_type": "UsageLimitExceeded", "error": "limit reached",
            "result": {"output": "", "usage": {}, "empty_output_guard": {
                "handoff_triggered": True, "handoff_reason": "empty output", "fallbacks_used": 1,
                "saw_empty_response": True,
            }},
        })
    assert guard.handoff_triggered
    assert guard.fallbacks_used == 1


@pytest.mark.asyncio
async def test_state_notification_wakes_only_its_bound_stage():
    """A busy sibling cannot cause status/authorization reads or steal a wake."""
    from collections import Counter

    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("done")]))
    runtime_plan = plan()
    first_binding = binding_for(runtime_plan, model)
    bridge = Bridge(first_binding)
    first = runtime(runtime_plan, bridge, first_binding, model)
    second = runtime(runtime_plan, bridge, binding_for(runtime_plan, model), model)
    calls = Counter()
    observed = {host.binding.stage_id: asyncio.Queue() for host in (first, second)}
    terminal = {}

    async def request(action, payload, *, authorization):
        assert action == "agent_compute_status"
        async with authorization:
            stage = payload["stage_id"]
            calls[stage] += 1
            observed[stage].put_nowait(None)
            return {
                **payload, "state": "terminal" if stage in terminal else "running",
                "terminal": terminal.get(stage),
            }

    bridge.request = request
    for host in (first, second):
        host._input_sha256 = "a" * 64
    tasks = [asyncio.create_task(host._wait_terminal()) for host in (first, second)]
    try:
        async with asyncio.timeout(5):
            for queue in observed.values():
                await queue.get()
            first.handle_state({"event": "agent_compute_state", **first._bound_binding()})
            await observed[first.binding.stage_id].get()
            assert calls[first.binding.stage_id] == 2
            assert calls[second.binding.stage_id] == 1
            # Both notifications remain pending independently.
            for host in (first, second):
                terminal[host.binding.stage_id] = {"ok": True}
                host.handle_state({"event": "agent_compute_state", **host._bound_binding()})
            assert await asyncio.gather(*tasks) == [{"ok": True}, {"ok": True}]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_chat_stream_keeps_one_terminal_watch_across_ui_events():
    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("done")]))
    runtime_plan = plan(surface="chat")
    bridge = Bridge(binding_for(runtime_plan, model))
    host = runtime(runtime_plan, bridge, bridge.binding, model)
    callbacks = []
    try:
        async with asyncio.timeout(5), host.run_stream_events("go") as events:
            for index in range(3):
                callbacks.append(asyncio.create_task(host._publish_runtime_event(
                    PartStartEvent(index=index, part=TextPart(str(index)))
                )))
                assert isinstance(await anext(events), PartStartEvent)
                if index == 0:
                    status_reads = bridge.calls.count("agent_compute_status")
                else:
                    assert bridge.calls.count("agent_compute_status") == status_reads
            bridge.status["terminal"] = {
                "ok": True,
                "result": {"output": "done", "usage": asdict(RunUsage()), "empty_output_guard": None},
            }
            host.handle_state({"event": "agent_compute_state", **host._bound_binding()})
            assert isinstance(await anext(events), RuntimeComputeResultEvent)
            await asyncio.gather(*callbacks)
        assert bridge.calls.count("agent_compute_status") == status_reads + 1
    finally:
        for task in callbacks:
            task.cancel()
        await asyncio.gather(*callbacks, return_exceptions=True)


@pytest.mark.asyncio
async def test_leaving_stream_context_cancels_and_joins_terminal_watcher(monkeypatch):
    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("unused")]))
    runtime_plan = plan(surface="chat")
    bridge = Bridge(binding_for(runtime_plan, model))
    host = runtime(runtime_plan, bridge, bridge.binding, model)
    entered, exited = asyncio.Event(), asyncio.Event()

    async def watch():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            exited.set()

    monkeypatch.setattr(host, "_wait_terminal", watch)
    async with asyncio.timeout(5):
        async with host.run_stream_events("go"):
            await entered.wait()
        assert exited.is_set()
        assert not host._run_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["stage_id", "route_id", "run_id", "dispatch_id", "input_sha256"])
async def test_unbound_state_notification_cannot_wake_stage(field):
    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("unused")]))
    runtime_plan = plan()
    bridge = Bridge(binding_for(runtime_plan, model))
    host = runtime(runtime_plan, bridge, bridge.binding, model)
    await host._start("go", [])
    notification = {"event": "agent_compute_state", **host._bound_binding()}
    notification[field] = "b" * 64 if field == "input_sha256" else str(uuid4())

    with pytest.raises(RemoteAgentRuntimeError, match="notification is invalid"):
        host.handle_state(notification)
    assert not host.state_changed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_reply", [False, True])
async def test_confirmed_start_uses_its_status_and_uncertain_start_reads_back(lost_reply):
    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("unused")]))
    runtime_plan = plan()
    bridge = Bridge(binding_for(runtime_plan, model), lose_start_reply=lost_reply)
    host = runtime(runtime_plan, bridge, bridge.binding, model)

    await host._start("go", [])

    assert host._started
    assert bridge.calls.count("agent_compute_start") == 1
    assert bridge.calls.count("agent_compute_status") == (2 if lost_reply else 1)


@pytest.mark.asyncio
async def test_invalid_confirmed_start_reply_fails_without_status_fallback():
    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("unused")]))
    runtime_plan = plan()
    bridge = Bridge(binding_for(runtime_plan, model))
    host = runtime(runtime_plan, bridge, bridge.binding, model)
    original = bridge.request

    async def request(action, payload, *, authorization):
        reply = await original(action, payload, authorization=authorization)
        return {**reply, "run_id": str(uuid4())} if action == "agent_compute_start" else reply

    bridge.request = request
    with pytest.raises(RemoteAgentRuntimeError, match="binding changed"):
        await host._start("go", [])
    assert not host._started
    assert bridge.calls == ["agent_compute_status", "agent_compute_start"]


@pytest.mark.asyncio
async def test_model_text_streams_once_from_api_without_token_callback_journal_entries():
    async def fragments(_messages, _info):
        for _ in range(200):
            yield "part-"

    model = FunctionModel(stream_function=fragments, model_name="api-model")
    runtime_plan = plan(surface="chat")
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    host = runtime(runtime_plan, bridge, stage_binding, model)
    requests = []

    async def produce():
        event = rpc(
            host._bound_binding(), str(uuid4()), "model_stream",
            {"payload_b64": base64.b64encode(model_payload()).decode()}, stream=True,
        )
        requests.append(event)
        await host.handle_rpc(event)
        bridge.status["terminal"] = {
            "ok": True,
            "result": {"output": "part-" * 200, "usage": asdict(host.usage), "empty_output_guard": None},
        }
        host.handle_state({"event": "agent_compute_state", **host._bound_binding()})

    from pydantic_ai.messages import PartDeltaEvent
    text = []
    async with asyncio.timeout(5), host.run_stream_events("go") as events:
        producer = asyncio.create_task(produce())
        try:
            async for event in events:
                if isinstance(event, PartStartEvent):
                    text.append(event.part.content)
                elif isinstance(event, PartDeltaEvent):
                    text.append(event.delta.content_delta)
            await producer
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
    assert "".join(text) == "part-" * 200
    assert len(host._receipts) == 1
    assert len(bridge.replies) >= 200
    delivered = len(bridge.replies)
    await host.handle_rpc(requests[0])
    assert len(bridge.replies) == 2 * delivered
    assert host._events.empty(), "Reply replay must not republish UI text"
    assert host.usage.requests == 1


def test_worker_cannot_echo_api_model_text_as_a_runtime_callback():
    from src.worker.remote.agent_compute import AgentComputeError
    with pytest.raises(AgentComputeError, match="unsupported runtime event"):
        encode_runtime_event(PartStartEvent(index=0, part=TextPart("owned by API")))


@pytest.mark.asyncio
async def test_full_callback_journal_delivers_bounded_refusal_without_another_effect():
    calls = 0
    def respond(*_):
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("done")])

    model = FunctionModel(respond)
    runtime_plan = replace(plan(), max_requests=256)
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    host = runtime(runtime_plan, bridge, stage_binding, model)
    await host._start("go", [])
    for _ in range(128):
        await host.handle_rpc(rpc(
            host._bound_binding(), str(uuid4()), "model_request",
            {"payload_b64": base64.b64encode(model_payload()).decode()},
        ))
    assert calls == 128
    retained = len(host._receipts)
    for _ in range(2):
        request_id = str(uuid4())
        await host.handle_rpc(rpc(
            host._bound_binding(), request_id, "model_request",
            {"payload_b64": base64.b64encode(model_payload()).decode()},
        ))
        assert bridge.replies[-1]["request_id"] == request_id
        assert bridge.replies[-1]["reply_frame"] == {
            "error_type": "RemoteAgentRuntimeError", "error": "Agent compute RPC journal is full",
        }
    assert len(host._receipts) == retained
    assert calls == 128

    request_id = str(uuid4())
    await host.handle_rpc(rpc(
        host._bound_binding(), request_id, "model_stream",
        {"payload_b64": base64.b64encode(model_payload()).decode()}, stream=True,
    ))
    assert bridge.replies[-1]["reply_frame"] == {
        "error_type": "RemoteAgentRuntimeError", "error": "Agent compute RPC journal is full",
        "sequence": 0, "final": True,
    }
    assert calls == 128


@pytest.mark.asyncio
async def test_model_stream_failure_is_a_sequenced_terminal_reply():
    async def failing_stream(_messages, _info):
        yield "partial"
        raise RuntimeError("fixture provider failed")

    model = FunctionModel(stream_function=failing_stream)
    runtime_plan = plan()
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    host = runtime(runtime_plan, bridge, stage_binding, model)
    await host._start("go", [])
    await host.handle_rpc(rpc(
        host._bound_binding(), str(uuid4()), "model_stream",
        {"payload_b64": base64.b64encode(model_payload()).decode()}, stream=True,
    ))
    frames = [row["reply_frame"] for row in bridge.replies]
    assert len(frames) > 1
    assert [frame["sequence"] for frame in frames] == list(range(len(frames)))
    assert frames[-1] == {
        "error_type": "RuntimeError", "error": "fixture provider failed",
        "sequence": len(frames) - 1, "final": True,
    }


@pytest.mark.asyncio
async def test_consumed_response_budget_keeps_space_for_a_terminal_error():
    from src.worker.remote.compute_stage_host import (
        MAX_AGENT_COMPUTE_ERROR_RESERVE_BYTES, MAX_AGENT_COMPUTE_JOURNAL_BYTES,
    )
    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("done")]))
    runtime_plan = plan()
    stage_binding = binding_for(runtime_plan, model)
    bridge = Bridge(stage_binding)
    host = runtime(runtime_plan, bridge, stage_binding, model)
    await host._start("go", [])
    # Model previously retained responses, without allocating64MiB in a unit test.
    host._journal_bytes = MAX_AGENT_COMPUTE_JOURNAL_BYTES - MAX_AGENT_COMPUTE_ERROR_RESERVE_BYTES
    await host.handle_rpc(rpc(
        host._bound_binding(), str(uuid4()), "model_request",
        {"payload_b64": base64.b64encode(model_payload()).decode()},
    ))
    assert bridge.replies[-1]["reply_frame"] == {
        "error_type": "RemoteAgentRuntimeError",
        "error": "Agent compute RPC journal byte limit is reached",
    }
    assert host._journal_bytes <= MAX_AGENT_COMPUTE_JOURNAL_BYTES
    assert next(iter(host._receipts.values())).complete
