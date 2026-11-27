"""Credential-free physical child for one API-orchestrated agent compute stage."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any, AsyncIterator, NoReturn
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessagesTypeAdapter,
    PartDeltaEvent,
    PartStartEvent,
    UserContent,
)
from pydantic_ai.usage import RunUsage

from shared.agent_model_compute import RemoteModel, deserialize_run_usage, serialize_run_usage
from src.services.agent_runtime.compute import AgentRuntimePlan, build_agent_runtime
from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.agent_runtime.toolset import ToolEvent

MAX_AGENT_COMPUTE_FRAME_BYTES = 8 * 1024 * 1024
MAX_PENDING_AGENT_COMPUTE_RPCS = 128
MAX_QUEUED_AGENT_COMPUTE_REPLIES = 128
_INIT_FIELDS = frozenset({
    "kind", "stage_id", "route_id", "run_id", "dispatch_id", "input_sha256",
    "plan", "model_name", "model_profile_payload_b64", "usage_initial",
    "user_prompt", "history",
})
_IMMUTABLE_INPUT_FIELDS = frozenset({
    "plan", "model_name", "model_profile_payload_b64", "usage_initial",
    "user_prompt", "history",
})
_RUNTIME_EVENT_ADAPTERS = {
    "PartStartEvent": TypeAdapter(PartStartEvent),
    "PartDeltaEvent": TypeAdapter(PartDeltaEvent),
    "FunctionToolCallEvent": TypeAdapter(FunctionToolCallEvent),
    "FunctionToolResultEvent": TypeAdapter(FunctionToolResultEvent),
}
_RUNTIME_EVENT_TYPES = (
    PartStartEvent,
    PartDeltaEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
)
_USER_PROMPT_ADAPTER = TypeAdapter(UserContent)


class AgentComputeError(RuntimeError):
    """The API callback attempt could not be completed safely."""


def _raise_callback_error(frame: Mapping[str, Any]) -> NoReturn:
    message = str(frame["error"])
    if frame.get("error_type") == "UsageLimitExceeded":
        raise UsageLimitExceeded(message)
    if frame.get("error_type") == "AgentRunCancelled":
        raise AgentRunCancelled(message)
    raise AgentComputeError(message)


def _json_bytes(value: Any) -> bytes:
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_AGENT_COMPUTE_FRAME_BYTES:
        raise AgentComputeError("Agent compute frame exceeds the byte limit")
    return raw


def _canonical_json_bytes(value: Any) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_AGENT_COMPUTE_FRAME_BYTES:
        raise AgentComputeError("Agent compute frame exceeds the byte limit")
    return raw


def agent_compute_input_sha256(frame: Mapping[str, Any]) -> str:
    """Hash the exact immutable bootstrap fields for one compute attempt."""
    unexpected, missing = set(frame) - _INIT_FIELDS, _INIT_FIELDS - set(frame)
    if unexpected or missing:
        raise AgentComputeError("Agent compute init fields do not match the wire contract")
    immutable = {key: frame[key] for key in _IMMUTABLE_INPUT_FIELDS}
    return hashlib.sha256(_canonical_json_bytes(immutable)).hexdigest()


def _strict_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AgentComputeError(f"Agent compute {field} must be a UUID")
    try:
        canonical = str(UUID(value))
    except ValueError as exc:
        raise AgentComputeError(f"Agent compute {field} must be a UUID") from exc
    if value != canonical:
        raise AgentComputeError(f"Agent compute {field} must be a canonical UUID")
    return canonical


def _validate_init(frame: Any, worker_id: str) -> dict[str, Any]:
    if not isinstance(frame, dict) or frame.get("kind") != "init":
        raise AgentComputeError("Agent compute child requires an init frame")
    _json_bytes(frame)
    stage_id = _strict_uuid(frame.get("stage_id"), "stage_id")
    _strict_uuid(frame.get("run_id"), "run_id")
    _strict_uuid(frame.get("dispatch_id"), "dispatch_id")
    route_id = frame.get("route_id")
    if not isinstance(route_id, str) or not route_id or route_id != worker_id:
        raise AgentComputeError("Agent compute route identity does not match this child")
    digest = frame.get("input_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or digest != agent_compute_input_sha256(frame)
    ):
        raise AgentComputeError("Agent compute init digest does not match its immutable input")
    if not isinstance(frame.get("plan"), Mapping):
        raise AgentComputeError("Agent compute init plan must be an object")
    if not isinstance(frame.get("model_name"), str) or not frame["model_name"]:
        raise AgentComputeError("Agent compute init model name is required")
    if not isinstance(frame.get("model_profile_payload_b64"), str):
        raise AgentComputeError("Agent compute init model profile is required")
    try:
        base64.b64decode(frame["model_profile_payload_b64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise AgentComputeError("Agent compute init model profile is invalid") from exc
    if not isinstance(frame.get("usage_initial"), dict):
        raise AgentComputeError("Agent compute init usage must be an object")
    if not isinstance(frame.get("history"), list):
        raise AgentComputeError("Agent compute init prompt or history is invalid")
    try:
        _USER_PROMPT_ADAPTER.validate_python(frame["user_prompt"])
    except Exception as exc:
        raise AgentComputeError("Agent compute init prompt is invalid") from exc
    frame["stage_id"] = stage_id
    return frame


def encode_runtime_event(event: Any) -> dict[str, Any]:
    """Serialize only the four chat events the API consumer understands."""
    event_type = type(event).__name__
    adapter = _RUNTIME_EVENT_ADAPTERS.get(event_type)
    if adapter is None or not isinstance(event, (PartStartEvent, PartDeltaEvent,
                                                 FunctionToolCallEvent, FunctionToolResultEvent)):
        raise AgentComputeError("Agent compute attempted to emit an unsupported runtime event")
    return {"event_type": event_type, "event": adapter.dump_python(event, mode="json")}


def decode_runtime_event(value: Mapping[str, Any]) -> Any:
    """Reconstruct a typed chat event at the API callback boundary."""
    if set(value) != {"event_type", "event"} or not isinstance(value.get("event_type"), str):
        raise AgentComputeError("Agent compute runtime event has an invalid shape")
    adapter = _RUNTIME_EVENT_ADAPTERS.get(value["event_type"])
    if adapter is None or not isinstance(value.get("event"), Mapping):
        raise AgentComputeError("Agent compute runtime event type is unsupported")
    try:
        return adapter.validate_python(value["event"])
    except Exception as exc:
        raise AgentComputeError("Agent compute runtime event is invalid") from exc


def _empty_output_guard_state(guard: Any) -> dict[str, Any] | None:
    if guard is None:
        return None
    return {
        "handoff_triggered": guard.handoff_triggered,
        "handoff_reason": guard.handoff_reason,
        "fallbacks_used": guard.fallbacks_used,
        "saw_empty_response": guard.saw_empty_response,
    }


@dataclass
class _AttemptState:
    usage: RunUsage | None = None
    empty_output_guard: Any = None


class AgentComputeChannel:
    """One child pipe reader and a correlated, bounded callback map."""

    def __init__(self, work_recv: Connection, result_send: Connection, *, stage_id: str) -> None:
        self._work_recv, self._result_send = work_recv, result_send
        self.stage_id = str(UUID(stage_id))
        self._pending: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader_error: str | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop.add_reader(self._work_recv.fileno(), self._read_reply)

    async def close(self) -> None:
        if self._loop is not None:
            self._loop.remove_reader(self._work_recv.fileno())
        self._work_recv.close()

    def _fail_pending(self, error: str) -> None:
        self._reader_error = error
        if self._loop is not None:
            self._loop.remove_reader(self._work_recv.fileno())
        for queue in self._pending.values():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({"error": error})

    def _read_reply(self) -> None:
        try:
            frame = self._work_recv.recv()
            if not isinstance(frame, dict) or frame.get("kind") != "rpc_reply":
                raise AgentComputeError("Agent compute child received an invalid callback reply")
            request_id = frame.get("request_id")
            if not isinstance(request_id, str):
                raise AgentComputeError("Agent compute callback reply lacks an identity")
            queue = self._pending.get(request_id)
            if queue is not None:
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull as exc:
                    raise AgentComputeError("Agent compute callback replies exceeded their bound") from exc
        except (EOFError, OSError, AgentComputeError) as exc:
            self._fail_pending(str(exc))

    async def request(self, op: str, payload: dict[str, Any], *, stream: bool = False) -> Any:
        _json_bytes(payload)
        if self._reader_error is not None:
            raise AgentComputeError(self._reader_error)
        if len(self._pending) >= MAX_PENDING_AGENT_COMPUTE_RPCS:
            raise AgentComputeError("Agent compute has too many pending callbacks")
        request_id = str(uuid4())
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=MAX_QUEUED_AGENT_COMPUTE_REPLIES
        )
        self._pending[request_id] = queue
        try:
            self._result_send.send({
                "kind": "rpc", "stage_id": self.stage_id, "request_id": request_id,
                "op": op, "payload": payload, "stream": stream,
            })
        except OSError as exc:
            self._pending.pop(request_id, None)
            self._fail_pending("Agent compute callback pipe is unavailable")
            raise AgentComputeError("Agent compute callback pipe is unavailable") from exc
        if stream:
            async def replies() -> AsyncIterator[bytes]:
                try:
                    expected = 0
                    while True:
                        frame = await queue.get()
                        if frame.get("error") is not None:
                            _raise_callback_error(frame)
                        if frame.get("sequence") != expected:
                            raise AgentComputeError("Agent model stream reply sequence changed")
                        expected += 1
                        payload_b64 = frame.get("payload_b64")
                        if payload_b64 is not None:
                            yield base64.b64decode(payload_b64, validate=True)
                        if frame.get("final") is True:
                            return
                finally:
                    self._pending.pop(request_id, None)
            return replies()
        try:
            frame = await queue.get()
            if frame.get("error") is not None:
                _raise_callback_error(frame)
            return frame.get("result")
        finally:
            self._pending.pop(request_id, None)


async def _run(
    frame: dict[str, Any], channel: AgentComputeChannel, state: _AttemptState
) -> dict[str, Any]:
    _json_bytes(frame)
    if frame["plan"].get("surface") == "summary":
        from src.worker.remote.summary_compute import run_summary_compute

        return await run_summary_compute(frame["plan"], channel)
    plan = AgentRuntimePlan.from_json(frame["plan"])
    profile = base64.b64decode(frame["model_profile_payload_b64"], validate=True)
    user_prompt = _USER_PROMPT_ADAPTER.validate_python(frame["user_prompt"])
    usage = deserialize_run_usage(frame["usage_initial"])
    state.usage = usage

    async def model_request(payload: bytes) -> bytes:
        result = await channel.request("model_request", {"payload_b64": base64.b64encode(payload).decode()})
        return base64.b64decode(result["payload_b64"], validate=True)

    async def model_count(payload: bytes) -> bytes:
        result = await channel.request("model_count", {"payload_b64": base64.b64encode(payload).decode()})
        return base64.b64decode(result["payload_b64"], validate=True)

    def model_stream(payload: bytes) -> AsyncIterator[bytes]:
        async def replies() -> AsyncIterator[bytes]:
            stream = await channel.request(
                "model_stream",
                {"payload_b64": base64.b64encode(payload).decode()},
                stream=True,
            )
            async for reply in stream:
                yield reply
        return replies()

    async def effect(name: str, args: dict[str, Any], call_id: str) -> Any:
        result = await channel.request(
            "effect", {"name": name, "arguments": args, "tool_call_id": call_id}
        )
        if (
            not isinstance(result, Mapping)
            or set(result) != {"value", "usage_delta"}
            or not isinstance(result["usage_delta"], Mapping)
        ):
            raise AgentComputeError("Agent compute effect reply has an invalid usage envelope")
        try:
            usage.incr(deserialize_run_usage(result["usage_delta"]))
        except (TypeError, ValueError) as exc:
            raise AgentComputeError("Agent compute effect usage delta is invalid") from exc
        return result["value"]

    async def capability_load(capability_id: str) -> None:
        await channel.request("capability_load", {"capability_id": capability_id})

    async def tool_event(event: ToolEvent) -> None:
        await channel.request("tool_event", {
            "type": event.type,
            "tool_name": event.tool_name,
            "arguments": event.arguments,
            "result": event.result,
            "error": event.error,
            "duration_ms": event.duration_ms,
        })

    model = RemoteModel(
        model_name=str(frame["model_name"]), request=model_request, request_stream=model_stream,
        count_tokens=model_count, profile_payload=profile,
    )
    built = build_agent_runtime(
        plan, model=model, executor=effect,
        event_handler=tool_event if plan.surface == "autonomous" else None,
        model_settings=None, usage=usage, capability_load_authorizer=capability_load,
        composition_error_type=AgentComputeError,
    )
    state.empty_output_guard = built.empty_output_guard
    history = ModelMessagesTypeAdapter.validate_json(_json_bytes(frame.get("history", [])))
    if plan.surface == "chat":
        output = ""
        async with built.runtime.run_stream_events(
            user_prompt, message_history=history, usage=usage,
            usage_limits=plan.budget.usage_limits(), conversation_id=plan.runtime_id,
        ) as events:
            async for event in events:
                if isinstance(event, _RUNTIME_EVENT_TYPES):
                    await channel.request("runtime_event", encode_runtime_event(event))
                elif isinstance(event, AgentRunResultEvent):
                    output = str(event.result.output or "")
    else:
        result = await built.runtime.run(
            user_prompt, message_history=history, usage=usage,
            usage_limits=plan.budget.usage_limits(), conversation_id=plan.runtime_id,
        )
        output = str(result.output or "")
    return {
        "output": output,
        "usage": serialize_run_usage(usage),
        "empty_output_guard": _empty_output_guard_state(state.empty_output_guard),
    }


def run_agent_compute_child(work_recv: Connection, result_send: Connection, worker_id: str) -> None:
    """Read one compute-stage init and report only explicit terminal state."""
    frame: dict[str, Any] | None = None
    state = _AttemptState()
    try:
        if not worker_id:
            raise AgentComputeError("Agent compute child requires its route identity")
        frame = _validate_init(work_recv.recv(), worker_id)
        channel = AgentComputeChannel(work_recv, result_send, stage_id=str(frame["stage_id"]))
        async def run() -> dict[str, Any]:
            await channel.start()
            try:
                return await _run(frame, channel, state)
            finally:
                await channel.close()
        result_send.send({"kind": "terminal", "stage_id": frame["stage_id"], "ok": True,
                          "result": asyncio.run(run())})
    except (Exception, asyncio.CancelledError) as exc:
        result_send.send({"kind": "terminal", "stage_id": frame.get("stage_id") if frame is not None else None,
                          "ok": False, "error_type": type(exc).__name__, "error": str(exc),
                          "result": {"output": "",
                                     "usage": serialize_run_usage(state.usage) if state.usage is not None else None,
                                     "empty_output_guard": _empty_output_guard_state(state.empty_output_guard)}})
