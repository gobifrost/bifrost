"""API-owned adapter for one credential-free physical agent-compute stage."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from copy import copy
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, UserContent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage, RunUsage
from pydantic_core import to_json

from shared.agent_model_compute import (
    deserialize_model_response,
    deserialize_run_usage,
    serialize_model_profile,
    serialize_run_usage,
    serve_model_count_tokens,
    serve_model_request,
    serve_model_stream,
)
from src.services.agent_runtime.compute import AgentRuntimePlan, RuntimeComputeResult, RuntimeComputeResultEvent
from src.services.agent_runtime.empty_output import EmptyOutputCircuitBreaker
from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.agent_runtime.toolset import ToolEvent
from src.worker.remote.agent_compute import decode_runtime_event
from src.worker.remote.compute_stage_host import (
    AgentComputeBinding, ComputeStageHost, RemoteAgentRuntimeError,
    MAX_AGENT_COMPUTE_RPCS, MAX_AGENT_COMPUTE_PAYLOAD_BYTES,
)


_USER_CONTENT = TypeAdapter(UserContent)
_REQUEST_USAGE = TypeAdapter(RequestUsage)
_MODEL_RESPONSE = TypeAdapter(ModelResponse)


def _wire_bytes(value: Any, *, label: str) -> bytes:
    if not isinstance(value, str):
        raise RemoteAgentRuntimeError(f"Agent compute {label} must be base64 text")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise RemoteAgentRuntimeError(
            f"Agent compute {label} is invalid base64"
        ) from exc
    if len(decoded) > MAX_AGENT_COMPUTE_PAYLOAD_BYTES:
        raise RemoteAgentRuntimeError(f"Agent compute {label} exceeds the byte limit")
    return decoded


def _json_safe(value: Any, *, label: str) -> Any:
    """Use Pydantic's JSON encoder, never repr/str private object state."""

    try:
        return json.loads(to_json(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RemoteAgentRuntimeError(
            f"Agent compute {label} is not JSON-safe"
        ) from exc


def _count_wire(usage: RequestUsage) -> bytes:
    return json.dumps(
        {
            "version": 1,
            "kind": "usage",
            "usage": _REQUEST_USAGE.dump_python(usage, mode="json"),
        },
        separators=(",", ":"),
    ).encode()


class RemoteAgentRuntime(ComputeStageHost):
    """Run a plan physically while preserving all authority and billing at the API."""

    rpc_operations = frozenset({"model_request", "model_count", "model_stream", "effect", "capability_load", "tool_event", "runtime_event"})
    streaming_operations = frozenset({"model_stream"})

    def __init__(
        self,
        plan: AgentRuntimePlan,
        bridge: Any,
        binding: AgentComputeBinding | Mapping[str, Any],
        *,
        trusted_model: Model,
        trusted_model_settings: ModelSettings | None,
        execute_tool: Callable[[str, dict[str, Any], str], Awaitable[Any]],
        tool_event: Callable[[ToolEvent], Awaitable[None]] | None,
        capability_load: Callable[[str], Awaitable[None]],
        usage: RunUsage,
        empty_output_guard: EmptyOutputCircuitBreaker | None,
        authorize_rpc: Callable[[], Awaitable[None]],
        authorize_send: Callable[[], AbstractAsyncContextManager[None]],
        memory_request_bytes: int,
    ) -> None:
        if plan.empty_output_guard != (empty_output_guard is not None):
            raise RemoteAgentRuntimeError("Agent compute empty-output guard does not match its plan")
        super().__init__(bridge, binding, authorize_rpc=authorize_rpc,
                         authorize_send=authorize_send, memory_request_bytes=memory_request_bytes)
        self.plan = plan
        self.trusted_model, self.trusted_model_settings = trusted_model, trusted_model_settings
        self.execute_tool, self.tool_event = execute_tool, tool_event
        self.capability_load, self.usage = capability_load, usage
        self.empty_output_guard = empty_output_guard
        self._events: asyncio.Queue[tuple[Any, asyncio.Future[None]]] = asyncio.Queue(MAX_AGENT_COMPUTE_RPCS)
        self._event_ready = asyncio.Event()
        self._count_cache: OrderedDict[str, RequestUsage] = OrderedDict()

    async def run(
        self,
        user_prompt: UserContent,
        *,
        message_history: list[Any] | None = None,
        usage_limits: Any | None = None,
        usage: RunUsage | None = None,
        conversation_id: str | None = None,
    ) -> RuntimeComputeResult:
        self._validate_run_arguments(
            message_history, usage_limits, usage, conversation_id
        )
        self._claim_invocation()
        async with self._run_lock:
            await self._start(user_prompt, message_history or [])
            return self._terminal_result(await self._wait_terminal())

    @asynccontextmanager
    async def run_stream_events(
        self,
        user_prompt: UserContent,
        *,
        message_history: list[Any] | None = None,
        usage_limits: Any | None = None,
        usage: RunUsage | None = None,
        conversation_id: str | None = None,
    ) -> AsyncIterator[AsyncIterator[Any]]:
        self._validate_run_arguments(
            message_history, usage_limits, usage, conversation_id
        )
        self._claim_invocation()
        await self._run_lock.acquire()
        terminal_wait: asyncio.Task[dict[str, Any]] | None = None
        try:
            await self._start(user_prompt, message_history or [])
            terminal_task = asyncio.create_task(self._wait_terminal())
            terminal_wait = terminal_task

            async def events() -> AsyncIterator[Any]:
                while True:
                    if not self._events.empty():
                        event, acknowledged = self._events.get_nowait()
                        try:
                            yield event
                        finally:
                            if not acknowledged.done():
                                acknowledged.set_result(None)
                        continue
                    if self._terminal is not None:
                        yield RuntimeComputeResultEvent(
                            self._terminal_result(self._terminal)
                        )
                        return
                    event_wait = asyncio.create_task(self._event_ready.wait())
                    try:
                        done, _ = await asyncio.wait(
                            {event_wait, terminal_task}, return_when=asyncio.FIRST_COMPLETED
                        )
                        if terminal_task in done:
                            self._terminal = terminal_task.result()
                        if event_wait in done:
                            self._event_ready.clear()
                    finally:
                        event_wait.cancel()
                        await asyncio.gather(event_wait, return_exceptions=True)

            yield events()
        finally:
            if terminal_wait is not None:
                terminal_wait.cancel()
                await asyncio.gather(terminal_wait, return_exceptions=True)
            self._run_lock.release()

    def _validate_run_arguments(
        self,
        history: list[Any] | None,
        usage_limits: Any | None,
        usage: RunUsage | None,
        conversation_id: str | None,
    ) -> None:
        if history is not None and not isinstance(history, list):
            raise RemoteAgentRuntimeError("Agent compute history must be a list")
        if usage is not None and usage is not self.usage:
            raise RemoteAgentRuntimeError(
                "Agent compute cannot replace its shared usage ledger"
            )
        if usage_limits is not None and usage_limits != self.plan.budget.usage_limits():
            raise RemoteAgentRuntimeError(
                "Agent compute usage limits do not match its plan"
            )
        if conversation_id is not None and conversation_id != self.plan.runtime_id:
            raise RemoteAgentRuntimeError(
                "Agent compute conversation identity does not match its plan"
            )

    async def _start(self, user_prompt: UserContent, history: list[Any]) -> None:
        if self._started:
            return
        prompt = _USER_CONTENT.dump_python(user_prompt, mode="json")
        try:
            history_wire = ModelMessagesTypeAdapter.dump_python(history, mode="json")
        except (TypeError, ValueError) as exc:
            raise RemoteAgentRuntimeError("Agent compute history is invalid") from exc
        init = {
            "plan": self.plan.to_json(),
            "model_name": self.trusted_model.model_name,
            "model_profile_payload_b64": base64.b64encode(
                serialize_model_profile(self.trusted_model.profile)
            ).decode(),
            "usage_initial": serialize_run_usage(self.usage),
            "user_prompt": prompt,
            "history": history_wire,
        }
        await self._start_stage(init)

    async def _reply_frames(
        self,
        request_id: str,
        op: str,
        payload: dict[str, Any],
        stream: bool,
        _payload_hash: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        if op == "model_stream":
                sequence = 0
                request = _wire_bytes(
                    payload.get("payload_b64"), label="model stream payload"
                )
                await self._preflight(request)
                await self.authorize_rpc()
                async for wire_frame in serve_model_stream(
                    self.trusted_model,
                    self.trusted_model_settings,
                    request,
                ):
                    self._meter_stream_frame(wire_frame)
                    frame = {
                        "sequence": sequence,
                        "payload_b64": base64.b64encode(wire_frame).decode(),
                        "final": False,
                    }
                    yield frame
                    sequence += 1
                frame = {"sequence": sequence, "final": True}
                yield frame
        else:
            yield {"result": await self._rpc_result(op, payload)}

    async def _rpc_result(self, op: str, payload: dict[str, Any]) -> Any:
        if op == "model_request":
            request = _wire_bytes(
                payload.get("payload_b64"), label="model request payload"
            )
            await self._preflight(request)
            await self.authorize_rpc()
            response_wire = await serve_model_request(
                self.trusted_model, self.trusted_model_settings, request
            )
            response = deserialize_model_response(response_wire)
            self.usage.incr(response.usage)
            return {"payload_b64": base64.b64encode(response_wire).decode()}
        if op == "model_count":
            request = _wire_bytes(
                payload.get("payload_b64"), label="model count payload"
            )
            count_wire = await self._count_and_check(request)
            return {"payload_b64": base64.b64encode(count_wire).decode()}
        if op == "effect":
            name, arguments, call_id = (
                payload.get("name"),
                payload.get("arguments"),
                payload.get("tool_call_id"),
            )
            if (
                not isinstance(name, str)
                or not isinstance(arguments, dict)
                or not isinstance(call_id, str)
            ):
                raise RemoteAgentRuntimeError("Agent compute effect payload is invalid")
            await self.authorize_rpc()
            before = copy(self.usage)
            value = await self.execute_tool(name, arguments, call_id)
            return {
                "value": _json_safe(value, label="effect result"),
                "usage_delta": serialize_run_usage(self.usage - before),
            }
        if op == "capability_load":
            capability_id = payload.get("capability_id")
            if not isinstance(capability_id, str) or not capability_id:
                raise RemoteAgentRuntimeError(
                    "Agent compute capability payload is invalid"
                )
            await self.authorize_rpc()
            await self.capability_load(capability_id)
            return None
        if op == "tool_event":
            if self.tool_event is None:
                raise RemoteAgentRuntimeError(
                    "Agent compute surface does not accept tool events"
                )
            try:
                event = TypeAdapter(ToolEvent).validate_python(payload, extra="forbid")
            except ValidationError as exc:
                raise RemoteAgentRuntimeError(
                    "Agent compute tool event is invalid"
                ) from exc
            await self.authorize_rpc()
            await self.tool_event(event)
            return None
        if op == "runtime_event":
            if self.plan.surface != "chat":
                raise RemoteAgentRuntimeError(
                    "Agent compute runtime events require the chat surface"
                )
            event = decode_runtime_event(payload)
            acknowledged: asyncio.Future[None] = (
                asyncio.get_running_loop().create_future()
            )
            try:
                self._events.put_nowait((event, acknowledged))
            except asyncio.QueueFull as exc:
                raise RemoteAgentRuntimeError(
                    "Agent compute runtime event queue is full"
                ) from exc
            self._event_ready.set()
            await acknowledged
            return None
        raise RemoteAgentRuntimeError("Agent compute RPC operation is unsupported")

    async def _preflight(self, request: bytes) -> None:
        limits = self.plan.budget.usage_limits()
        limits.check_before_request(self.usage)
        if limits.count_tokens_before_request:
            await self._count_and_check(request)
        # There is no await between the final limit check and reservation.
        limits.check_before_request(self.usage)
        self.usage.requests += 1

    async def _count_and_check(self, request: bytes) -> bytes:
        key = hashlib.sha256(request).hexdigest()
        counted = self._count_cache.get(key)
        if counted is None:
            await self.authorize_rpc()
            count_wire = await serve_model_count_tokens(
                self.trusted_model, self.trusted_model_settings, request
            )
            try:
                count_payload = json.loads(count_wire)["usage"]
                counted = _REQUEST_USAGE.validate_python(count_payload)
            except (
                KeyError,
                TypeError,
                ValueError,
                ValidationError,
                json.JSONDecodeError,
            ) as exc:
                raise RemoteAgentRuntimeError(
                    "Agent compute token count response is invalid"
                ) from exc
            self._count_cache[key] = counted
            if len(self._count_cache) > MAX_AGENT_COMPUTE_RPCS:
                self._count_cache.popitem(last=False)
        else:
            # Cached counts do not bypass current API authority.
            await self.authorize_rpc()
        projected = copy(self.usage)
        projected.incr(counted)
        self.plan.budget.usage_limits().check_before_request(projected)
        return _count_wire(counted)

    def _meter_stream_frame(self, wire_frame: bytes) -> None:
        try:
            envelope = json.loads(wire_frame)
            if envelope.get("kind") != "stream-final":
                return
            response = _MODEL_RESPONSE.validate_python(envelope["response"])
        except (
            TypeError,
            ValueError,
            KeyError,
            ValidationError,
            json.JSONDecodeError,
        ) as exc:
            raise RemoteAgentRuntimeError(
                "Agent compute stream response is invalid"
            ) from exc
        self.usage.incr(response.usage)

    def _terminal_result(self, terminal: Mapping[str, Any]) -> RuntimeComputeResult:
        if terminal.get("ok") is not True:
            failure_result = terminal.get("result")
            guard = failure_result.get("empty_output_guard") if isinstance(failure_result, Mapping) else None
            if guard is not None:
                if self.empty_output_guard is None or not isinstance(guard, Mapping):
                    raise RemoteAgentRuntimeError(
                        "Agent compute empty-output guard is invalid"
                    )
                self._restore_guard(guard)
            error_type, message = terminal.get("error_type"), terminal.get("error")
            if error_type == "UsageLimitExceeded":
                raise UsageLimitExceeded(str(message))
            if error_type == "AgentRunCancelled":
                raise AgentRunCancelled(str(message))
            raise RuntimeError(str(message or "Agent compute stage failed"))
        result = terminal.get("result")
        if not isinstance(result, Mapping) or set(result) != {
            "output",
            "usage",
            "empty_output_guard",
        }:
            raise RemoteAgentRuntimeError("Agent compute terminal result is invalid")
        guard = result["empty_output_guard"]
        if guard is not None:
            if self.empty_output_guard is None or not isinstance(guard, Mapping):
                raise RemoteAgentRuntimeError(
                    "Agent compute empty-output guard is invalid"
                )
            self._restore_guard(guard)
        # Child usage is intentionally validated but never copied into the API ledger.
        try:
            deserialize_run_usage(result["usage"])
        except (TypeError, ValueError) as exc:
            raise RemoteAgentRuntimeError(
                "Agent compute terminal usage is invalid"
            ) from exc
        output = _json_safe(result["output"], label="terminal output")
        return RuntimeComputeResult(output, dict(guard) if guard is not None else None)

    def _restore_guard(self, state: Mapping[str, Any]) -> None:
        expected = {
            "handoff_triggered",
            "handoff_reason",
            "fallbacks_used",
            "saw_empty_response",
        }
        if (
            set(state) != expected
            or type(state["handoff_triggered"]) is not bool
            or not isinstance(state["handoff_reason"], str)
            or type(state["fallbacks_used"]) is not int
            or state["fallbacks_used"] < 0
            or type(state["saw_empty_response"]) is not bool
        ):
            raise RemoteAgentRuntimeError("Agent compute empty-output guard is invalid")
        guard = self.empty_output_guard
        assert guard is not None
        guard.handoff_triggered = state["handoff_triggered"]
        guard.handoff_reason = state["handoff_reason"]
        guard._fallbacks_used = state["fallbacks_used"]
        guard._consecutive_empty = 1 if state["saw_empty_response"] else 0
