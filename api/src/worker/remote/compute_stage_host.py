"""Shared API ownership, lifecycle, and retained RPC replies for compute stages."""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from typing import Any, cast
from uuid import UUID

MAX_AGENT_COMPUTE_RPCS = 128
MAX_AGENT_COMPUTE_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_AGENT_COMPUTE_PAYLOAD_BYTES = 8 * 1024 * 1024
# Each admitted callback needs room for one bounded failure receipt.
MAX_AGENT_COMPUTE_ERROR_RESERVE_BYTES = MAX_AGENT_COMPUTE_RPCS * 64 * 1024
_INIT_FIELDS = frozenset({"plan", "model_name", "model_profile_payload_b64", "usage_initial", "user_prompt", "history"})

class RemoteAgentRuntimeError(RuntimeError):
    """The physical compute stage violated its bounded API contract."""


@dataclass(frozen=True)
class AgentComputeBinding:
    """Immutable identity shared by the API runtime and one physical child."""

    stage_id: str
    route_id: str
    run_id: str
    dispatch_id: str

    @classmethod
    def from_value(
        cls, value: "AgentComputeBinding | Mapping[str, Any]"
    ) -> "AgentComputeBinding":
        if isinstance(value, cls):
            return value
        expected = {"stage_id", "route_id", "run_id", "dispatch_id"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise RemoteAgentRuntimeError("Agent compute binding has an invalid shape")
        stage_id, run_id, dispatch_id = (
            _canonical_uuid(value[field], field)
            for field in ("stage_id", "run_id", "dispatch_id")
        )
        route_id = value["route_id"]
        if not isinstance(route_id, str) or not route_id:
            raise RemoteAgentRuntimeError("Agent compute route_id is required")
        return cls(stage_id, route_id, run_id, dispatch_id)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class _Receipt:
    payload_hash: str
    frames: list[dict[str, Any]]
    bytes: int = 0
    task: asyncio.Task[None] | None = None
    complete: bool = False


def _canonical_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise RemoteAgentRuntimeError(f"Agent compute {field} must be a UUID")
    try:
        canonical = str(UUID(value))
    except ValueError as exc:
        raise RemoteAgentRuntimeError(f"Agent compute {field} must be a UUID") from exc
    if canonical != value:
        raise RemoteAgentRuntimeError(f"Agent compute {field} must be canonical")
    return canonical


def _json_bytes(value: Any, *, label: str) -> bytes:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as exc:
        raise RemoteAgentRuntimeError(
            f"Agent compute {label} must be JSON-safe"
        ) from exc
    if len(payload) > MAX_AGENT_COMPUTE_PAYLOAD_BYTES:
        raise RemoteAgentRuntimeError(f"Agent compute {label} exceeds the byte limit")
    return payload


class ComputeStageHost(ABC):
    """One immutable physical attempt; API callbacks survive transport reconnects."""

    rpc_operations: frozenset[str]
    streaming_operations: frozenset[str] = frozenset()

    def __init__(
        self, bridge: Any, binding: AgentComputeBinding | Mapping[str, Any], *,
        authorize_rpc: Callable[[], Awaitable[None]],
        authorize_send: Callable[[], AbstractAsyncContextManager[None]],
        memory_request_bytes: int,
    ) -> None:
        if type(memory_request_bytes) is not int or memory_request_bytes < 0:
            raise RemoteAgentRuntimeError("Agent compute memory request is invalid")
        self.bridge = bridge
        self.binding = AgentComputeBinding.from_value(binding)
        self._api_context = contextvars.copy_context()
        self.authorize_rpc, self.authorize_send = authorize_rpc, authorize_send
        self.memory_request_bytes = memory_request_bytes
        self._receipts: OrderedDict[str, _Receipt] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._journal_bytes = 0
        self._started = False
        self._start_attempted = False
        self._invoked = False
        self._released = False
        self.release_requested = False
        self._input_sha256: str | None = None
        self._terminal: dict[str, Any] | None = None
        self._run_lock = asyncio.Lock()
        self.state_changed = asyncio.Event()
        runtimes = getattr(bridge, "agent_compute_runtimes", None)
        if not isinstance(runtimes, dict) or self.binding.stage_id in runtimes:
            raise RemoteAgentRuntimeError("Agent compute bridge stage registration is unavailable")
        runtimes[self.binding.stage_id] = self

    @abstractmethod
    def _reply_frames(
        self, request_id: str, op: str, payload: dict[str, Any], stream: bool,
    ) -> AsyncIterator[dict[str, Any]]:
        """Produce replies from API-owned callbacks; the base retains and sends them."""
        raise NotImplementedError

    async def _dispatch_rpc(
        self, request_id: str, op: str, payload: dict[str, Any], stream: bool, _payload_hash: str,
    ) -> None:
        receipt = self._receipts[request_id]
        next_sequence = 0
        try:
            async for frame in self._reply_frames(request_id, op, payload, stream):
                self._retain_frame(receipt, frame)
                if stream:
                    next_sequence = frame["sequence"] + 1
                await self._send_reply(request_id, frame)
        except Exception as exc:
            frame = self._error_frame(
                type(exc).__name__[:100], str(exc)[:4_000], stream=stream, sequence=next_sequence,
            )
            self._retain_frame(receipt, frame, terminal_error=True)
            await self._send_reply(request_id, frame)
        receipt.complete = True

    async def _start_stage(self, immutable: dict[str, Any]) -> None:
        if self._started:
            return
        if set(immutable) != _INIT_FIELDS:
            raise RemoteAgentRuntimeError("Agent compute initialization fields are invalid")
        self._input_sha256 = hashlib.sha256(_json_bytes(immutable, label="initialization")).hexdigest()
        init = {**self.binding.to_dict(), **immutable, "input_sha256": self._input_sha256}
        status = await self._status()
        while status["state"] == "unknown":
            try:
                self._start_attempted = True
                status = await self._send("agent_compute_start", {**init, "memory_request_bytes": self.memory_request_bytes})
                self._validate_status(status)
            except (ConnectionError, TimeoutError):
                # Read back an uncertain start. A confirmed, bound start reply
                # already carries the retained status; it needs no second RPC.
                status = await self._status()
        if status["state"] not in {"starting", "running", "terminal"}:
            raise RemoteAgentRuntimeError("Agent compute stage has an invalid state")
        self._started = True
        if status.get("terminal") is not None:
            self._terminal = cast(dict[str, Any], status["terminal"])

    async def handle_rpc(self, event: Mapping[str, Any]) -> None:
        """Authorize and dispatch exactly one retained child callback request."""

        request_id, op, payload, stream = self._validate_rpc(event)
        payload_hash = hashlib.sha256(
            _json_bytes(
                {"op": op, "payload": payload, "stream": stream}, label="RPC payload"
            )
        ).hexdigest()
        receipt = self._receipts.get(request_id)
        if receipt is not None:
            if receipt.payload_hash != payload_hash:
                raise RemoteAgentRuntimeError(
                    "Agent compute request id changed its payload"
                )
            for frame in receipt.frames:
                await self._send_reply(request_id, frame)
            if receipt.task is not None and not receipt.complete:
                await asyncio.shield(receipt.task)
            return
        if len(self._inflight) + len(self._receipts) >= MAX_AGENT_COMPUTE_RPCS:
            await self._send_reply(request_id, self._error_frame(
                "RemoteAgentRuntimeError", "Agent compute RPC journal is full", stream=stream,
            ))
            return
        if (
            self._journal_bytes
            > MAX_AGENT_COMPUTE_JOURNAL_BYTES - MAX_AGENT_COMPUTE_PAYLOAD_BYTES
        ):
            await self._send_reply(request_id, self._error_frame(
                "RemoteAgentRuntimeError", "Agent compute RPC journal byte limit is reached", stream=stream,
            ))
            return
        receipt = _Receipt(payload_hash, [])
        self._receipts[request_id] = receipt
        task = asyncio.create_task(
            self._dispatch_rpc(request_id, op, payload, stream, payload_hash),
            context=self._api_context.copy(),
            name=f"agent-compute-rpc:{request_id}",
        )
        self._inflight[request_id] = task
        receipt.task = task
        task.add_done_callback(lambda _done: self._inflight.pop(request_id, None))
        await asyncio.shield(task)

    def handle_state(self, event: Mapping[str, Any]) -> None:
        """Wake terminal waiters after an exact bound state notification."""

        expected = {"event", *self._bound_binding()}
        if (
            not isinstance(event, Mapping)
            or set(event) != expected
            or event.get("event") != "agent_compute_state"
            or any(
                event.get(key) != value for key, value in self._bound_binding().items()
            )
        ):
            raise RemoteAgentRuntimeError("Agent compute state notification is invalid")
        self.state_changed.set()

    async def release(self) -> None:
        if self._released:
            return
        self.release_requested = True
        if self._input_sha256 is None and not self._start_attempted:
            self._released = True
            getattr(self.bridge, "agent_compute_runtimes", {}).pop(
                self.binding.stage_id, None
            )
            return
        try:
            async with asyncio.timeout(30):
                await self._cleanup_release()
        except (ConnectionError, TimeoutError):
            # Keep registration and the journal for same-owner reconciliation.
            raise
        self._released = True
        for task in self._inflight.values():
            task.cancel()
        await asyncio.gather(*self._inflight.values(), return_exceptions=True)
        self._inflight.clear()
        self._receipts.clear()
        self._journal_bytes = 0
        getattr(self.bridge, "agent_compute_runtimes", {}).pop(
            self.binding.stage_id, None
        )

    async def abandon(self) -> None:
        """Drop this API owner's state after true ownership loss; never command worker."""

        self._released = True
        self.release_requested = True
        for task in self._inflight.values():
            task.cancel()
        await asyncio.gather(*self._inflight.values(), return_exceptions=True)
        self._inflight.clear()
        self._receipts.clear()
        self._count_cache.clear()
        self._journal_bytes = 0
        while not self._events.empty():
            _event, acknowledgement = self._events.get_nowait()
            if not acknowledgement.done():
                acknowledgement.cancel()
        getattr(self.bridge, "agent_compute_runtimes", {}).pop(
            self.binding.stage_id, None
        )

    def _claim_invocation(self) -> None:
        if self._released:
            raise RemoteAgentRuntimeError("An agent compute stage has been released")
        if self._invoked:
            raise RemoteAgentRuntimeError("An agent compute stage may run only once")
        self._invoked = True

    async def _status(self) -> dict[str, Any]:
        while True:
            await self._ready()
            try:
                reply = await self._send("agent_compute_status", self._bound_binding())
            except (ConnectionError, TimeoutError):
                continue
            self._validate_status(reply)
            return reply

    async def _ready(self) -> None:
        """A same-owner reconnect resumes the retained physical stage."""

        while True:
            self.bridge.transport_changed.clear()
            if self.bridge.connected and self.bridge.reconciliation_ready.is_set():
                return
            await self.bridge.transport_changed.wait()

    async def _wait_terminal(self) -> dict[str, Any]:
        if self._terminal is not None:
            return self._terminal
        while True:
            self.state_changed.clear()
            status = await self._status()
            terminal = status.get("terminal")
            if isinstance(terminal, dict):
                self._terminal = terminal
                return terminal
            await self.state_changed.wait()

    async def _send(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.bridge.request(
            action, payload, authorization=self.authorize_send()
        )

    async def _cleanup_release(self) -> None:
        """Read back an uncertain release without waiting for reconciliation."""

        binding = self._bound_binding()
        try:
            await self._send("agent_compute_release", binding)
            return
        except TimeoutError:
            pass
        status = await self._send("agent_compute_status", binding)
        self._validate_status(status)
        if status["state"] == "released":
            return
        await self._send("agent_compute_release", binding)

    def _validate_status(self, status: Any) -> None:
        if not isinstance(status, dict) or any(
            status.get(key) != value for key, value in self._bound_binding().items()
        ):
            raise RemoteAgentRuntimeError("Agent compute reply binding changed")
        if status.get("state") not in {
            "unknown",
            "starting",
            "running",
            "terminal",
            "released",
        }:
            raise RemoteAgentRuntimeError("Agent compute status is invalid")

    def _validate_rpc(
        self, event: Mapping[str, Any]
    ) -> tuple[str, str, dict[str, Any], bool]:
        expected = {
            "event",
            *self._bound_binding(),
            "request_id",
            "op",
            "payload",
            "stream",
        }
        if (
            not isinstance(event, Mapping)
            or set(event) != expected
            or event.get("event") != "agent_compute_rpc"
        ):
            raise RemoteAgentRuntimeError("Agent compute RPC has an invalid shape")
        if any(event.get(key) != value for key, value in self._bound_binding().items()):
            raise RemoteAgentRuntimeError("Agent compute RPC binding changed")
        request_id = _canonical_uuid(event.get("request_id"), "request_id")
        op, payload, stream = event.get("op"), event.get("payload"), event.get("stream")
        if op not in self.rpc_operations:
            raise RemoteAgentRuntimeError("Agent compute RPC operation is unsupported")
        if not isinstance(payload, dict) or type(stream) is not bool:
            raise RemoteAgentRuntimeError("Agent compute RPC payload is invalid")
        if stream != (op in self.streaming_operations):
            raise RemoteAgentRuntimeError("Agent compute RPC stream flag is invalid")
        return request_id, op, payload, stream

    @staticmethod
    def _error_frame(
        error_type: str, error: str, *, stream: bool, sequence: int = 0,
    ) -> dict[str, Any]:
        frame: dict[str, Any] = {"error_type": error_type, "error": error}
        if stream:
            frame.update(sequence=sequence, final=True)
        return frame

    def _retain_frame(
        self, receipt: _Receipt, frame: dict[str, Any], *, terminal_error: bool = False,
    ) -> None:
        size = len(_json_bytes(frame, label="RPC receipt"))
        limit = MAX_AGENT_COMPUTE_JOURNAL_BYTES
        if not terminal_error:
            limit -= MAX_AGENT_COMPUTE_ERROR_RESERVE_BYTES
        if self._journal_bytes + size > limit:
            raise RemoteAgentRuntimeError(
                "Agent compute RPC journal byte limit is reached"
            )
        receipt.frames.append(frame)
        receipt.bytes += size
        self._journal_bytes += size

    async def _send_reply(self, request_id: str, frame: dict[str, Any]) -> None:
        while True:
            try:
                await self._send(
                    "agent_compute_reply",
                    {
                        **self._bound_binding(),
                        "request_id": request_id,
                        "reply_frame": frame,
                    },
                )
                return
            except (ConnectionError, TimeoutError):
                # The task and its computed reply stay in this API process.
                # A same-owner reconnect resends the exact frame, never reruns
                # a provider request or tool effect.
                await self._ready()

    def _bound_binding(self) -> dict[str, str]:
        if self._input_sha256 is None:
            raise RemoteAgentRuntimeError(
                "Agent compute stage has not been initialized"
            )
        return {**self.binding.to_dict(), "input_sha256": self._input_sha256}
