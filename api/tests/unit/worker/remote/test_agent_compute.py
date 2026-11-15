"""Pipe correlation contracts for physical agent compute children."""

import asyncio
import base64
import multiprocessing
import time
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from shared.agent_model_compute import serialize_model_profile, serve_model_request
from src.services.execution.template_process import TemplateProcess
from src.worker.remote.agent_compute import (
    AgentComputeChannel,
    AgentComputeError,
    _validate_init,
    agent_compute_input_sha256,
)


_STAGE_ID = "00000000-0000-0000-0000-000000000001"


def _channel():
    work_recv, work_send = multiprocessing.Pipe(duplex=False)
    result_recv, result_send = multiprocessing.Pipe(duplex=False)
    return AgentComputeChannel(
        work_recv, result_send, stage_id=_STAGE_ID
    ), work_send, result_recv


def _reply(request: dict, **fields: object) -> dict:
    return {"kind": "rpc_reply", "request_id": request["request_id"], **fields}


@pytest.mark.asyncio
async def test_callback_reply_keeps_the_child_generated_request_identity():
    channel, work, result = _channel()
    await channel.start()
    try:
        pending = asyncio.create_task(channel.request("effect", {"name": "read"}))
        request = await asyncio.to_thread(result.recv)
        work.send(_reply(request, result={"ok": True}))
        assert await pending == {"ok": True}
    finally:
        await channel.close()


@pytest.mark.asyncio
async def test_stream_replies_require_monotonic_sequences():
    channel, work, result = _channel()
    await channel.start()
    try:
        stream = await channel.request("model_stream", {"payload_b64": ""}, stream=True)
        request = await asyncio.to_thread(result.recv)
        work.send(_reply(request, sequence=0, payload_b64="YQ==", final=False))
        work.send(_reply(request, sequence=1, final=True))
        assert [item async for item in stream] == [b"a"]
    finally:
        await channel.close()


@pytest.mark.asyncio
async def test_stream_sequence_gap_fails_closed_without_a_second_callback():
    channel, work, result = _channel()
    await channel.start()
    try:
        stream = await channel.request("model_stream", {"payload_b64": ""}, stream=True)
        request = await asyncio.to_thread(result.recv)
        work.send(_reply(request, sequence=2, final=True))
        with pytest.raises(AgentComputeError, match="sequence changed"):
            await anext(stream)
        assert not result.poll()
    finally:
        await channel.close()


def _init_frame(route_id: str) -> dict[str, object]:
    frame: dict[str, object] = {
        "kind": "init",
        "stage_id": str(uuid4()),
        "route_id": route_id,
        "run_id": str(uuid4()),
        "dispatch_id": str(uuid4()),
        "input_sha256": "",
        "plan": {
            "surface": "autonomous",
            "prompt": "Answer the user.",
            "definitions": [],
            "composition_snapshot": None,
            "max_requests": 1,
            "max_total_tokens": None,
            "warning_threshold": 0.7,
            "initial_requests": 0,
            "initial_total_tokens": 0,
            "context_window": None,
            "automatic_code_mode": False,
            "runtime_id": "fixture-runtime",
            "toolset_id": "fixture-toolset",
            "empty_output_guard": False,
        },
        "model_name": "fixture-model",
        "model_profile_payload_b64": "",
        "usage_initial": {},
        "user_prompt": "hello",
        "history": [],
    }
    frame["input_sha256"] = agent_compute_input_sha256(frame)
    return frame


def _wait_for_exit(template: TemplateProcess, pid: int, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = template.collect_child_exit_statuses()
        if pid in status:
            return status[pid]
        time.sleep(0.05)
    raise AssertionError("agent compute child did not exit")


def test_agent_compute_template_child_runs_model_request_and_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real prewarmed child completes one model RPC and closes its reader."""
    monkeypatch.setenv("BIFROST_SECRET_KEY", "must-not-reach-compute-child")
    monkeypatch.setenv("BIFROST_AZURE_BLOB_ACCOUNT_KEY", "also-must-not-reach")
    template = TemplateProcess(profile="agent_compute")
    template.start()
    route_id = "agent-compute-fixture"
    api_model = FunctionModel(
        lambda *_: ModelResponse(parts=[TextPart("done")]), model_name="fixture-model"
    )
    try:
        assert template.pid is not None
        with open(f"/proc/{template.pid}/environ", "rb") as environment:
            child_env = environment.read()
        assert b"must-not-reach-compute-child" not in child_env
        assert b"also-must-not-reach" not in child_env
        pid, work, result = template.fork(worker_id=route_id)
        try:
            frame = _init_frame(route_id)
            frame["model_profile_payload_b64"] = base64.b64encode(
                serialize_model_profile(api_model.profile)
            ).decode()
            frame["input_sha256"] = agent_compute_input_sha256(frame)
            work.put(frame)
            request = result.get(timeout=30)
            assert request["kind"] == "rpc", request
            assert request["op"] == "model_request"
            response = asyncio.run(serve_model_request(
                api_model, None, base64.b64decode(request["payload"]["payload_b64"])
            ))
            work.put(_reply(request, result={
                "payload_b64": base64.b64encode(response).decode(),
            }))
            terminal = result.get(timeout=30)
            assert terminal["kind"] == "terminal"
            assert terminal["ok"] is True
            assert terminal["result"]["output"] == "done"
            assert terminal["result"]["usage"]["requests"] == 1
            assert _wait_for_exit(template, pid) == 0
        finally:
            work.close()
            result.close()
    finally:
        template.shutdown()


def test_init_rejects_a_tampered_immutable_payload() -> None:
    frame = _init_frame("agent-compute-fixture")
    frame["user_prompt"] = "tampered"

    with pytest.raises(AgentComputeError, match="digest"):
        _validate_init(frame, "agent-compute-fixture")


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("error_type", ["UsageLimitExceeded", "AgentRunCancelled"])
async def test_callback_preserves_native_budget_and_cancellation_control_flow(streaming, error_type):
    from pydantic_ai.exceptions import UsageLimitExceeded
    from src.services.agent_runtime.errors import AgentRunCancelled

    expected = UsageLimitExceeded if error_type == "UsageLimitExceeded" else AgentRunCancelled
    channel, work, result = _channel()
    await channel.start()
    try:
        if streaming:
            replies = await channel.request("model_stream", {}, stream=True)
            request = await asyncio.to_thread(result.recv)
            work.send(_reply(request, error_type=error_type, error="bounded stop"))
            with pytest.raises(expected, match="bounded stop"):
                await anext(replies)
        else:
            pending = asyncio.create_task(channel.request("model_request", {}))
            request = await asyncio.to_thread(result.recv)
            work.send(_reply(request, error_type=error_type, error="bounded stop"))
            with pytest.raises(expected, match="bounded stop"):
                await pending
    finally:
        await channel.close()
