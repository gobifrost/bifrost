"""Lightweight physical runner: OS supervision without application imports.

Only the API can issue commands. Durable claims, SDK authorization and result
commits stay in the ordinary consumers hosted by ``remote.host``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from queue import Empty
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NamedTuple
from uuid import UUID, uuid4

from shared.version import get_version
from src.services.execution.cpu_sampler import CPUSampler, get_clock_ticks
from src.services.execution.memory_monitor import effective_memory_sample, memory_pressure_ratio
from src.services.execution.template_process import TemplateProcess
from src.worker.remote.app_compile import (
    APP_COMPILE_OPERATION,
    AppCompileStages,
    app_compile_operation,
)
from shared.worker_compute import (
    AGENT_COMPUTE_CAPABILITY,
    SUMMARY_COMPUTE_CAPABILITY,
    COMPUTE_CAPABILITIES,
)
from src.worker.remote.agent_compute import MAX_AGENT_COMPUTE_FRAME_BYTES
from src.worker.remote.control_connection import connect_control, load_control_headers
from src.worker.remote.identity import LauncherIdentity, load_launcher_identity
from src.worker.remote.file_operation import NamedFileOperationStages
from src.worker.remote.protocol import (
    CREDENTIAL_PROTOCOL,
    EXECUTION_HANDLERS,
    SUMMARY_CLAIM_PROTOCOL,
    ControlEnvelope,
    FragmentAssembler,
    Hello,
    PayloadSerializationError,
    fragment,
    new_nonce,
    sign_hello,
)

logger = logging.getLogger(__name__)
LAUNCHER_DRAIN_GRACE_SECONDS = 300.0
MAX_AGENT_COMPUTE_STAGES = 4
MAX_AGENT_COMPUTE_STAGE_RECORDS = 4096
MAX_AGENT_COMPUTE_INFLIGHT_RPCS = 64
MAX_AGENT_COMPUTE_RPC_BYTES = 64 * 1024 * 1024
MAX_AGENT_COMPUTE_RPC_RECEIPTS = 4096


async def _next_control_frame(
    socket: Any,
    stopping: asyncio.Event,
    retired: asyncio.Event | None = None,
    drain_task: asyncio.Task[None] | None = None,
) -> str | None:
    """Receive one frame while still observing process shutdown signals."""
    receive = asyncio.create_task(socket.recv())
    wake_tasks = {asyncio.create_task(stopping.wait())}
    if retired is not None:
        wake_tasks.add(asyncio.create_task(retired.wait()))
    waiters = {receive, *wake_tasks}
    if drain_task is not None:
        waiters.add(drain_task)
    done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        if task is not drain_task:
            task.cancel()
    await asyncio.gather(*(task for task in pending if task is not drain_task), return_exceptions=True)
    if receive not in done:
        return None
    return receive.result()


async def _receive_control_commands(
    launcher: Launcher,
    socket: Any,
    stopping: asyncio.Event,
    drain_task: asyncio.Task[None] | None = None,
) -> asyncio.Task[None] | None:
    """Continue receiving ACKs and retirement while physical drain runs."""
    assembler = FragmentAssembler()
    while not launcher.retired.is_set():
        raw = await _next_control_frame(socket, stopping, launcher.retired, drain_task)
        if raw is None:
            if launcher.retired.is_set() or (drain_task is not None and drain_task.done()):
                return drain_task
            if drain_task is None:
                drain_task = asyncio.create_task(launcher.begin_drain())
            continue
        frame = ControlEnvelope.from_json(raw)
        if frame.session_generation != launcher.session_generation:
            raise RuntimeError("stale API control session")
        if frame.sequence <= launcher._last_command_sequence:
            raise RuntimeError("stale API control command")
        launcher._last_command_sequence = frame.sequence
        command = assembler.feed(frame)
        if command is None:
            continue
        if command.kind == "ack":
            event_id = command.payload.get("event_id")
            if isinstance(event_id, str):
                launcher.pending_terminal.pop(event_id, None)
                launcher.pending_terminal_inventory.pop(event_id, None)
            continue
        if command.kind != "command":
            raise RuntimeError("unexpected API control message")
        launcher.task(launcher.command(command.payload, generation=launcher.session_generation))
    return drain_task


async def _shutdown_launcher(
    launcher: Launcher,
    drain_task: asyncio.Task[None] | None,
    stopping: asyncio.Event,
) -> None:
    """Honor one bounded physical drain before stopping local children."""
    if stopping.is_set() and drain_task is None:
        drain_task = asyncio.create_task(launcher.begin_drain())
    if drain_task is not None:
        await asyncio.gather(drain_task, return_exceptions=True)
    await launcher.stop()


class ProcStat(NamedTuple):
    state: str
    ppid: int
    start_ticks: int


def proc_stat(pid: int) -> ProcStat | None:
    """State, parent and start time of ``pid``, or None once it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    # The command name may contain spaces; fields after it are fixed.
    fields = stat.rsplit(")", 1)[1].split()
    return ProcStat(state=fields[0], ppid=int(fields[1]), start_ticks=int(fields[19]))


@dataclass
class Child:
    route_id: str
    execution_id: str
    pid: int
    work: Any
    result: Any
    started: float
    timeout: float
    service: dict[str, Any] | None = None
    memory_kill_growth_bytes: int = 0
    grace: float = 5
    rss_start: int = 0
    rss_peak: int = 0
    growth_peak: int = 0
    current_growth: int = 0
    stopping: bool = False
    sampler: CPUSampler | None = None
    terminal_reason: tuple[str, str] | None = None
    active_execution: dict[str, Any] | None = None
    started_at: str | None = None
    memory_request_bytes: int | None = None
    agent_run: bool = False
    agent_handler: str = "agent.run"
    summary_claim_id: str | None = None
    # Start time of the forked process, so a reused PID is never mistaken
    # for it when the launcher reaps an orphaned agent child.
    start_ticks: int | None = None

    @property
    def kind(self) -> str:
        if self.agent_run:
            return "agent_run" if self.agent_handler == "agent.run" else "agent_summary"
        return "service" if self.service is not None else "workflow"


@dataclass(frozen=True)
class AgentComputeBinding:
    """Immutable API-owned identity for one physical pure-compute stage."""

    stage_id: str
    route_id: str
    run_id: str
    dispatch_id: str
    input_sha256: str


@dataclass
class AgentComputeRequest:
    frame: dict[str, Any]
    frame_bytes: int
    stream: bool
    next_sequence: int = 0


@dataclass
class AgentComputeStage:
    binding: AgentComputeBinding
    init: dict[str, Any]
    memory_request_bytes: int
    state: str = "starting"
    pid: int | None = None
    work: Any | None = None
    result: Any | None = None
    sampler: CPUSampler | None = None
    terminal: dict[str, Any] | None = None
    pending: dict[str, AgentComputeRequest] = field(default_factory=dict)
    reply_receipts: dict[tuple[str, int | None], str] = field(default_factory=dict)
    total_rpc_bytes: int = 0
    request_count: int = 0


def _agent_compute_uuid(value: object, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _agent_compute_json(value: Any, *, label: str) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc


def _agent_compute_binding(payload: dict[str, Any]) -> AgentComputeBinding:
    route_id = payload.get("route_id")
    if not isinstance(route_id, str) or not route_id:
        raise ValueError("route_id is required")
    input_sha256 = payload.get("input_sha256")
    if (
        not isinstance(input_sha256, str)
        or len(input_sha256) != 64
        or any(char not in "0123456789abcdef" for char in input_sha256)
    ):
        raise ValueError("input_sha256 must be a lowercase SHA-256 digest")
    return AgentComputeBinding(
        stage_id=_agent_compute_uuid(payload.get("stage_id"), "stage_id"),
        route_id=route_id,
        run_id=_agent_compute_uuid(payload.get("run_id"), "run_id"),
        dispatch_id=_agent_compute_uuid(payload.get("dispatch_id"), "dispatch_id"),
        input_sha256=input_sha256,
    )


def _agent_compute_binding_dict(binding: AgentComputeBinding) -> dict[str, str]:
    return {
        "stage_id": binding.stage_id,
        "route_id": binding.route_id,
        "run_id": binding.run_id,
        "dispatch_id": binding.dispatch_id,
        "input_sha256": binding.input_sha256,
    }


class Launcher:
    def __init__(
        self,
        socket: Any,
        *,
        identity: LauncherIdentity,
        secret: str,
        on_ready: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        authenticated_control: bool = False,
    ) -> None:
        self.socket = socket
        self.identity = identity
        self.secret = secret
        self._authenticated_control = authenticated_control
        self.session_generation = 0
        self._sequence = 0
        self._last_command_sequence = 0
        self._hello_sequence = 0
        self.on_ready = on_ready or (lambda: None)
        self.on_stop = on_stop or (lambda: None)
        self.requirements: dict[str, Any] = {}
        self.requirements_content: str | None = None
        # External launchers begin with scoped workflows. Agent runs become
        # eligible only after their credential-free compute template is warm.
        self.compute_only = os.environ.get("BIFROST_WORKER_COMPUTE_ONLY") == "1"
        self.handlers = () if self.compute_only else tuple(
            pair for pair in EXECUTION_HANDLERS
            if identity.form != "external" or pair[0] == "workflow.execute"
        )
        self.pending_terminal: dict[str, dict[str, Any]] = {}
        self.pending_terminal_inventory: dict[str, dict[str, Any]] = {}
        self.template = TemplateProcess()
        # Started with the launcher and kept, like the workflow template.
        self.agent_template = (
            TemplateProcess(profile="agent")
            if any(pair[0].startswith("agent.") for pair in self.handlers) else None
        )
        # The pure compute template intentionally has none of the legacy
        # agent consumer's database or provider authority. It is started
        # before advertising ``agent.loop.v1`` to the API control host.
        self.agent_compute_template = TemplateProcess(profile="agent_compute")
        self._agent_compute_template_ready = False
        # Finished agent children by PID -> start time. If their template
        # dies they re-parent to the launcher (PID 1 in a pod), which must
        # reap them.
        self.exited_agent_children: dict[int, int] = {}
        self.children: dict[str, Child] = {}
        self.jobs: dict[str, asyncio.subprocess.Process] = {}
        self.job_claims: dict[str, dict[str, str | int]] = {}
        # This owns only file-backed CPU compute; PlatformJob authority stays
        # with the API control host.
        # Operation recipes are code-owned.  In particular, control payloads
        # can select a registered name but can never import a callable or
        # supply a subprocess command.
        from src.worker.remote.audit_archive_compute import audit_archive_operation
        from src.worker.remote.audit_export_compute import audit_export_operation
        from src.worker.remote.solution_archive_inspect import solution_archive_inspect_operation
        from src.worker.remote.app_bundle_compute import (
            APP_BUNDLE_OPERATION,
            app_bundle_operation,
        )

        from src.worker.remote.app_preview_compile import app_preview_operation
        from src.worker.remote.workflow_graph_analyze import workflow_graph_operation
        from src.worker.remote.workspace_preflight import workspace_preflight_operation

        self.file_operations = NamedFileOperationStages(
            operations={
                "workspace.preflight.v1": workspace_preflight_operation(),
                "workflow.graph.analyze.v1": workflow_graph_operation(),
                "app.preview.compile.v1": app_preview_operation(),
                APP_COMPILE_OPERATION: app_compile_operation(),
                APP_BUNDLE_OPERATION: app_bundle_operation(),
                "audit.archive.v1": audit_archive_operation(),
                "audit.export.v1": audit_export_operation(),
                "solution.archive.inspect.v1": solution_archive_inspect_operation(),
            },
            terminal_notification=self.send_file_compute_state,
        )
        self.app_compiles = AppCompileStages(self.file_operations)
        self.agent_compute_stages: dict[str, AgentComputeStage] = {}
        self._agent_compute_tombstones: dict[str, AgentComputeBinding] = {}
        self._agent_compute_retire_requested = False
        self._template_lock = asyncio.Lock()
        # Separate from the workflow template's lock: rebuilding the agent
        # template takes seconds and must not stall workflow forks.
        self._agent_template_lock = asyncio.Lock()
        self._agent_compute_template_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._monitor_task: asyncio.Task[Any] | None = None
        self.failed = False
        self.pressure_threshold: float | None = None
        self.dangerous_memory_ratio: float | None = None
        self.baseline_bytes: int | None = None
        self.retired = asyncio.Event()
        self._compute_retirement_drain_sent = False

    async def send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            if self.session_generation < 1:
                raise ConnectionError("launcher has no fenced API session")
            envelope = ControlEnvelope(
                session_generation=self.session_generation,
                sequence=self._sequence + 1,
                kind="event",
                payload=payload,
            )
            sent = 0
            for frame in fragment(envelope):
                raw = frame.to_json()
                try:
                    await self.socket.send(raw)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Background task logging must not recover credentials
                    # echoed in a transport exception or WebSocket close reason.
                    raise ConnectionError("remote control transport is unavailable") from (
                        None if self._authenticated_control else exc
                    )
                sent += 1
            self._sequence += sent

    async def send_terminal(
        self, payload: dict[str, Any], *, inventory: dict[str, Any]
    ) -> None:
        """Keep terminal results until the current fenced API owner ACKs them."""
        event_id = str(uuid4())
        event = {**payload, "_event_id": event_id}
        self.pending_terminal[event_id] = event
        self.pending_terminal_inventory[event_id] = {"event_id": event_id, **inventory}
        try:
            await self.send(event)
        except PayloadSerializationError:
            # Encoding fails before fragment() yields any frames. This event
            # cannot be replayed; transport failures still retain it for ACK.
            self.pending_terminal.pop(event_id, None)
            self.pending_terminal_inventory.pop(event_id, None)
            raise

    def task(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self.task_finished)
        return task

    def task_finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            if task is self._monitor_task:
                self.failed = True
                self.on_stop()
            logger.error("Launcher supervision failed", exc_info=task.exception())
            asyncio.create_task(self.socket.close(code=1011, reason="Launcher supervision failed"))

    async def start(self) -> None:
        if self.compute_only:
            from src.services.execution.requirements_setup_result import RequirementsInstallResult

            self.requirements = RequirementsInstallResult().to_json_dict()
            current, _limit = effective_memory_sample()
            self.baseline_bytes = current
            self._monitor_task = self.task(self.monitor())
            return
        # This existing helper owns requirements IO and exits before ready.
        result = await self.helper(
            "src.services.execution.requirements_setup_helper",
            input_payload={"content": self.requirements_content},
        )
        self.requirements = result
        startup = [asyncio.to_thread(self.template.start)]
        if self.agent_template is not None:
            startup.append(asyncio.to_thread(self.agent_template.start))
        if not self._agent_compute_template_ready:
            startup.append(self.start_agent_compute_template())
        await asyncio.gather(*startup)
        current, limit = effective_memory_sample()
        self.baseline_bytes = current
        self._monitor_task = self.task(self.monitor())

    async def start_agent_compute_template(self) -> None:
        """Make the pure compute path real before it is advertised."""
        async with self._agent_compute_template_lock:
            if self._agent_compute_template_ready:
                return
            await asyncio.to_thread(self.agent_compute_template.start)
            self._agent_compute_template_ready = True
            if self.identity.form == "external":
                self.handlers = tuple(
                    pair for pair in EXECUTION_HANDLERS
                    if pair[0] in ("workflow.execute", "agent.run", "agent.summarize", "agent.summarize_backfill")
                )

    async def hello(self, requirements: dict[str, Any]) -> None:
        """Authenticate each new WebSocket; the physical process stays alive."""
        async with self._send_lock:
            await self._hello(requirements)

    async def reconnect(self, socket: Any, requirements: dict[str, Any]) -> None:
        """Fence a replacement transport before another event can use it."""
        async with self._send_lock:
            self.socket = socket
            await self._hello(requirements)

    async def _hello(self, requirements: dict[str, Any]) -> None:
        current, limit = effective_memory_sample()
        self._hello_sequence += 1
        hello = Hello(
            launcher_id=self.identity.launcher_id,
            incarnation_id=self.identity.incarnation_id,
            job_uid=self.identity.job_uid,
            pod_uid=self.identity.pod_uid,
            hostname=self.identity.hostname,
            form=self.identity.form,
            resource_uid=self.identity.resource_uid,
            issued_at=int(time.time()),
            nonce=new_nonce(),
            hello_sequence=self._hello_sequence,
            execution_lane=os.environ.get("BIFROST_POOL_WORKER_EXECUTION_LANE", "normal"),
        )
        await self.socket.send(json.dumps({
            "event": "hello",
            "hello": sign_hello(self.secret, hello).to_dict(),
            "sample": {
                "current": current, "limit": limit,
                "pressure_ratio": memory_pressure_ratio(),
            },
            "requirements": requirements,
            "version": get_version(),
            "credential_protocol": CREDENTIAL_PROTOCOL,
            "handlers": self.handlers,
            "supports_platform_jobs": self.identity.form != "external" and not self.compute_only,
            "compute_capabilities": [
                list(capability)
                for capability in COMPUTE_CAPABILITIES
                if capability not in (AGENT_COMPUTE_CAPABILITY, SUMMARY_COMPUTE_CAPABILITY)
                or self._agent_compute_template_ready
            ],
            "summary_claim_protocol": SUMMARY_CLAIM_PROTOCOL,
        }, separators=(",", ":")))
        welcome = json.loads(await self.socket.recv())
        if welcome.get("event") != "welcome" or not isinstance(welcome.get("session_generation"), int):
            raise RuntimeError("remote host refused launcher session")
        if (self.identity.form == "external" or self.compute_only) and welcome.get("api_version") != get_version():
            raise RuntimeError("External worker build must match the connected API build")
        self.session_generation = welcome["session_generation"]
        self._sequence = 0
        self._last_command_sequence = 0

        assembler = FragmentAssembler()
        while True:
            frame = ControlEnvelope.from_json(await self.socket.recv())
            if frame.session_generation != self.session_generation or frame.sequence <= self._last_command_sequence:
                raise RuntimeError("Invalid worker bootstrap session")
            self._last_command_sequence = frame.sequence
            startup = assembler.feed(frame)
            if startup is None:
                continue
            if startup.kind != "bootstrap" or "requirements_content" not in startup.payload:
                raise RuntimeError("Worker bootstrap inputs are required")
            content = startup.payload["requirements_content"]
            if content is not None and not isinstance(content, str):
                raise RuntimeError("Invalid worker requirements content")
            self.requirements_content = content
            break

    def inventory(self) -> dict[str, Any]:
        """Describe live physical routes for fenced API-owner adoption.

        Context and authoritative claim state are intentionally not copied over
        the wire: the adopting API reconstructs them from canonical DB claims.
        """
        routes: list[dict[str, Any]] = []
        for child in self.children.values():
            if child.agent_run:
                routes.append(self.agent_route(child, pid=child.pid))
                continue
            routes.append({
                "route_id": child.route_id,
                "kind": child.kind,
                "subject_id": child.execution_id,
                "pid": child.pid,
                "service": child.service,
                "active_execution": child.active_execution,
                "started_at": child.started_at,
                "timeout_seconds": child.timeout,
                "memory_request_bytes": child.memory_request_bytes,
            })
        for route_id, process in self.jobs.items():
            claim = self.job_claims.get(route_id, {})
            routes.append({
                "route_id": route_id,
                "kind": "platform_job",
                "subject_id": claim.get("job_id"),
                "pid": process.pid,
                "lease_token": claim.get("lease_token"),
                "memory_request_bytes": claim.get("memory_request_bytes"),
            })
        return {
            "version": 1,
            "baseline_bytes": self.baseline_bytes,
            "routes": routes,
            "compute_stages": self.file_operations.inventory(),
            "compute_retirement_requested": self.file_operations.retire_requested,
            "agent_compute_stages": [
                {
                    **self.agent_compute_status(_agent_compute_binding_dict(stage.binding)),
                    "compute_kind": stage.init["plan"].get("surface"),
                    **({"summary_claim_id": stage.init["plan"].get("claim_id")}
                       if stage.init["plan"].get("surface") == "summary" else {}),
                }
                for stage in self.agent_compute_stages.values()
            ],
            "agent_compute_retirement_requested": self._agent_compute_retire_requested,
            "terminal_routes": list(self.pending_terminal_inventory.values()),
        }

    async def helper(
        self, module: str, *, input_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", module,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, start_new_session=True,
        )
        try:
            stdin = json.dumps(input_payload).encode() if input_payload is not None else None
            stdout, _ = await process.communicate(stdin)
        except BaseException:
            await self.kill_process(process)
            raise
        if process.returncode:
            raise RuntimeError(f"{module} failed with exit code {process.returncode}")
        return json.loads(stdout)

    @staticmethod
    def _agent_compute_init(payload: dict[str, Any], binding: AgentComputeBinding) -> dict[str, Any]:
        keys = ("plan", "model_name", "model_profile_payload_b64", "usage_initial", "user_prompt", "history")
        if any(key not in payload for key in keys):
            raise ValueError("Agent compute start is missing immutable initialization inputs")
        immutable = {key: payload[key] for key in keys}
        digest = hashlib.sha256(_agent_compute_json(immutable, label="Agent compute initialization")).hexdigest()
        if digest != binding.input_sha256:
            raise ValueError("Agent compute initialization digest does not match input_sha256")
        return {"kind": "init", **_agent_compute_binding_dict(binding), **immutable}

    @staticmethod
    def _agent_compute_memory_request(payload: dict[str, Any]) -> int:
        value = payload.get("memory_request_bytes")
        if type(value) is not int or value < 0:
            raise ValueError("memory_request_bytes must be a non-negative integer")
        return value

    @staticmethod
    def _agent_compute_same_binding(actual: AgentComputeBinding, expected: AgentComputeBinding) -> None:
        if actual != expected:
            raise ValueError("Agent compute stage identity does not match its existing binding")

    def _agent_compute_stage(self, payload: dict[str, Any]) -> AgentComputeStage:
        binding = _agent_compute_binding(payload)
        stage = self.agent_compute_stages.get(binding.stage_id)
        if stage is None:
            raise ValueError("Unknown Agent compute stage")
        self._agent_compute_same_binding(stage.binding, binding)
        return stage

    def agent_compute_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        binding = _agent_compute_binding(payload)
        stage = self.agent_compute_stages.get(binding.stage_id)
        if stage is None:
            tombstone = self._agent_compute_tombstones.get(binding.stage_id)
            if tombstone is not None:
                self._agent_compute_same_binding(tombstone, binding)
            return {
                **_agent_compute_binding_dict(binding),
                "state": "released" if tombstone is not None else "unknown",
                "memory_request_bytes": 0,
                "pid": None,
                "pending_rpc_count": 0,
            }
        self._agent_compute_same_binding(stage.binding, binding)
        response: dict[str, Any] = {
            **_agent_compute_binding_dict(stage.binding),
            "state": stage.state,
            "memory_request_bytes": stage.memory_request_bytes,
            "pid": stage.pid,
            "pending_rpc_count": len(stage.pending),
        }
        if stage.terminal is not None:
            response["terminal"] = stage.terminal
        return response

    async def agent_compute_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fork at most one pure-compute child for an immutable stage binding."""
        binding = _agent_compute_binding(payload)
        init = self._agent_compute_init(payload, binding)
        memory_request_bytes = self._agent_compute_memory_request(payload)
        tombstone = self._agent_compute_tombstones.get(binding.stage_id)
        if tombstone is not None:
            self._agent_compute_same_binding(tombstone, binding)
            raise ValueError("Agent compute stage has already been released")
        existing = self.agent_compute_stages.get(binding.stage_id)
        if existing is not None:
            self._agent_compute_same_binding(existing.binding, binding)
            if existing.init != init or existing.memory_request_bytes != memory_request_bytes:
                raise ValueError("Agent compute start conflicts with the existing stage")
            return self.agent_compute_status(_agent_compute_binding_dict(binding))
        if self._agent_compute_retire_requested:
            raise RuntimeError("Agent compute stage journal is full; launcher retirement is requested")
        if len(self.agent_compute_stages) >= MAX_AGENT_COMPUTE_STAGES:
            raise RuntimeError("Agent compute stage limit is reached")
        if len(self.agent_compute_stages) + len(self._agent_compute_tombstones) >= MAX_AGENT_COMPUTE_STAGE_RECORDS:
            raise RuntimeError("Agent compute stage record limit is reached")
        # Insert before the first await: uncertain starts read back this stage,
        # never fork a second process for the same immutable identity.
        stage = AgentComputeStage(binding=binding, init=init, memory_request_bytes=memory_request_bytes)
        self.agent_compute_stages[binding.stage_id] = stage
        try:
            self.check_fork_pressure()
            if not self._agent_compute_template_ready:
                raise RuntimeError("Agent compute template is not ready")
            async with self._agent_compute_template_lock:
                if not self._agent_compute_template_ready:
                    raise RuntimeError("Agent compute template is not ready")
                pid, work, result = await asyncio.to_thread(
                    self.agent_compute_template.fork, worker_id=binding.route_id
                )
            stage.pid, stage.work, stage.result = pid, work, result
            stage.sampler = CPUSampler(pid=pid, clock_ticks=get_clock_ticks())
            stage.sampler.set_baseline(time.monotonic())
            asyncio.get_running_loop().add_reader(result.fileno(), self.agent_compute_result_ready, binding.stage_id)
            work.put_nowait(init)
            stage.state = "running"
        except Exception as exc:
            await self._fail_agent_compute_stage(stage, type(exc).__name__, str(exc), stop_child=stage.pid is not None)
        return self.agent_compute_status(_agent_compute_binding_dict(binding))

    async def agent_compute_reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        stage = self._agent_compute_stage(payload)
        request_id = _agent_compute_uuid(payload.get("request_id"), "request_id")
        reply = payload.get("reply_frame")
        if not isinstance(reply, dict):
            raise ValueError("Agent compute reply_frame is required")
        reply_hash = hashlib.sha256(_agent_compute_json(reply, label="Agent compute reply")).hexdigest()
        receipt_key = (request_id, reply.get("sequence") if type(reply.get("sequence")) is int else None)
        receipt = stage.reply_receipts.get(receipt_key)
        if receipt is not None:
            if receipt != reply_hash:
                raise ValueError("Agent compute reply conflicts with its prior delivery")
            return {**_agent_compute_binding_dict(stage.binding), "request_id": request_id, "accepted": True}
        request = stage.pending.get(request_id)
        if request is None:
            raise ValueError("Unknown Agent compute callback request")
        if request.stream:
            sequence = reply.get("sequence")
            if type(sequence) is not int or sequence != request.next_sequence:
                raise ValueError("Agent compute stream reply sequence is invalid")
            if type(reply.get("final")) is not bool:
                raise ValueError("Agent compute stream reply must declare final")
        elif "sequence" in reply:
            raise ValueError("Non-stream Agent compute reply cannot have a sequence")
        frame = {"kind": "rpc_reply", "request_id": request_id, **reply}
        if len(_agent_compute_json(frame, label="Agent compute reply")) > MAX_AGENT_COMPUTE_FRAME_BYTES:
            raise ValueError("Agent compute reply exceeds the byte limit")
        if stage.work is None or stage.state != "running":
            raise RuntimeError("Agent compute stage is not accepting replies")
        if len(stage.reply_receipts) >= MAX_AGENT_COMPUTE_RPC_RECEIPTS:
            self._agent_compute_retire_requested = True
            await self._fail_agent_compute_stage(
                stage, "ResourceLimitError", "Agent compute callback journal limit is reached", stop_child=True
            )
            raise RuntimeError("Agent compute callback journal limit is reached")
        try:
            await asyncio.to_thread(stage.work.put_nowait, frame)
        except Exception as exc:
            await self._fail_agent_compute_stage(stage, type(exc).__name__, str(exc), stop_child=True)
            raise
        stage.reply_receipts[receipt_key] = reply_hash
        if request.stream and reply["final"] is False:
            request.next_sequence += 1
        else:
            stage.pending.pop(request_id, None)
            stage.total_rpc_bytes -= request.frame_bytes
        return {**_agent_compute_binding_dict(stage.binding), "request_id": request_id, "accepted": True}

    async def agent_compute_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        binding = _agent_compute_binding(payload)
        tombstone = self._agent_compute_tombstones.get(binding.stage_id)
        if tombstone is not None:
            self._agent_compute_same_binding(tombstone, binding)
            return {**_agent_compute_binding_dict(binding), "released": True}
        stage = self.agent_compute_stages.get(binding.stage_id)
        if stage is None:
            raise ValueError("Unknown Agent compute stage")
        self._agent_compute_same_binding(stage.binding, binding)
        try:
            await self._stop_agent_compute_stage(stage)
        except Exception as exc:
            # Do not forget an uncertain physical child. The same release can
            # be retried against the retained, fully-bound stage record.
            await self._fail_agent_compute_stage(stage, type(exc).__name__, str(exc))
            raise
        self.agent_compute_stages.pop(binding.stage_id, None)
        self._agent_compute_tombstones[binding.stage_id] = binding
        if len(self._agent_compute_tombstones) >= MAX_AGENT_COMPUTE_STAGE_RECORDS:
            self._agent_compute_retire_requested = True
        if self._agent_compute_retire_requested and not self.agent_compute_stages:
            self._request_compute_retirement_drain()
        return {**_agent_compute_binding_dict(binding), "released": True}

    async def command(self, request: dict[str, Any], *, generation: int) -> None:
        """Execute one command only while its fenced session remains current."""
        if generation != self.session_generation:
            return
        action, payload = request["action"], request["payload"]
        try:
            response: dict[str, Any] = {}
            if self.compute_only and action in (
                "workflow", "service", "agent_run", "agent_summary", "platform_job", "helper",
                "agent_compute_start", "agent_compute_status", "agent_compute_reply",
                "agent_compute_release", "package_tool", "recycle",
            ):
                raise ValueError("Compute-only workers cannot start privileged execution")
            if action in ("workflow", "service"):
                self.check_fork_pressure()
                route_id = payload["route_id"]
                if route_id in self.children or route_id in self.jobs:
                    raise ValueError("Duplicate route")
                if generation != self.session_generation:
                    return
                async with self._template_lock:
                    if generation != self.session_generation:
                        return
                    pid, work, result = await asyncio.to_thread(
                        self.template.fork, worker_id=route_id, sdk_socket_path=None,
                    )
                context = payload["context"]
                timeout = context.get("timeout_seconds", 300) if action == "workflow" else 0
                child = Child(route_id, payload["execution_id"], pid, work, result,
                              time.monotonic(), timeout,
                              service=payload.get("service"),
                              memory_kill_growth_bytes=payload.get("memory_kill_growth_bytes", 0),
                              grace=payload.get("graceful_shutdown_seconds", 5),
                              active_execution=payload.get("active_execution"),
                              started_at=payload.get("started_at"),
                              memory_request_bytes=payload.get("memory_request_bytes"))
                self.track_child(child)
                work.put_nowait((child.execution_id, context))
                response["pid"] = pid
            elif action in ("agent_run", "agent_summary"):
                if self.agent_template is None:
                    raise ValueError("This worker does not support agent execution")
                handler = "agent.run" if action == "agent_run" else payload["handler"]
                if handler not in ("agent.run", "agent.summarize", "agent.summarize_backfill"):
                    raise ValueError("Unknown agent handler")
                if action == "agent_summary" and handler == "agent.run":
                    raise ValueError("Summary command requires a summary handler")
                if action == "agent_summary" and payload.get("worker_id") != str(self.identity.incarnation_id):
                    raise ValueError("Summary worker identity does not match this launcher")
                claim_id = str(UUID(payload["message"]["claim_id"])) if action == "agent_summary" else None
                self.check_fork_pressure()
                route_id = payload["route_id"]
                if route_id in self.children or route_id in self.jobs:
                    raise ValueError("Duplicate route")
                if generation != self.session_generation:
                    return
                async with self._agent_template_lock:
                    if generation != self.session_generation:
                        return
                    pid, work, result = await asyncio.to_thread(
                        self.agent_template.fork, worker_id=route_id
                    )
                child = Child(route_id, str(payload["run_id"]), pid, work, result,
                              time.monotonic(), 0,
                              memory_request_bytes=int(payload["memory_request_bytes"]),
                              agent_run=True, agent_handler=handler, summary_claim_id=claim_id)
                self.track_child(child)
                work.put_nowait({
                    "message": payload["message"],
                    **({"handler": handler, "worker_id": payload["worker_id"]} if action == "agent_summary" else {}),
                })
                response["pid"] = pid
            elif action == "platform_job":
                if self.identity.form == "external":
                    raise ValueError("This worker does not support privileged platform runners")
                if self.pressure_threshold is None:
                    raise RuntimeError("Launcher resource policy has not been configured")
                if memory_pressure_ratio() >= self.pressure_threshold:
                    raise MemoryError("Launcher memory pressure rejects platform job")
                route_id = payload["route_id"]
                if route_id in self.jobs or route_id in self.children:
                    raise ValueError("Duplicate route")
                if generation != self.session_generation:
                    return
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "src.jobs.platform.runner",
                    payload["job_id"], payload["lease_token"], start_new_session=True,
                )
                self.jobs[route_id] = process
                self.job_claims[route_id] = {
                    "job_id": str(payload["job_id"]),
                    "lease_token": str(payload["lease_token"]),
                    "memory_request_bytes": int(payload["memory_request_bytes"]),
                }
                self.task(self.wait_job(route_id, process))
            elif action == "app_compile_prepare":
                response = self.app_compiles.prepare(payload)
            elif action == "app_compile_chunk":
                response = self.app_compiles.add_chunk(payload)
            elif action == "app_compile_start":
                response = await self.app_compiles.start(payload)
            elif action == "app_compile_status":
                response = self.app_compiles.status(payload)
            elif action == "app_compile_result_chunk":
                response = self.app_compiles.result_chunk(payload)
            elif action == "app_compile_release":
                response = await self.app_compiles.release(payload)
                if (
                    self.file_operations.retire_requested
                    and not self.file_operations.has_stages
                    and not self._compute_retirement_drain_sent
                ):
                    self._compute_retirement_drain_sent = True
                    self.task(self.send_compute_retirement_drain())
            elif action.startswith("file_compute_"):
                operation = payload.get("operation")
                if not isinstance(operation, str):
                    raise ValueError("File compute operation is required")
                if action == "file_compute_prepare":
                    response = self.file_operations.prepare(operation, payload)
                elif action == "file_compute_chunk":
                    response = self.file_operations.add_chunk(operation, payload)
                elif action == "file_compute_start":
                    response = await self.file_operations.start(operation, payload)
                elif action == "file_compute_status":
                    response = self.file_operations.status(operation, payload)
                elif action == "file_compute_result_chunk":
                    response = self.file_operations.result_chunk(operation, payload)
                elif action == "file_compute_release":
                    response = await self.file_operations.release(operation, payload)
                    if (
                        self.file_operations.retire_requested
                        and not self.file_operations.has_stages
                        and not self._compute_retirement_drain_sent
                    ):
                        self._compute_retirement_drain_sent = True
                        self.task(self.send_compute_retirement_drain())
                else:
                    raise ValueError("Unknown file compute command")
            elif action == "agent_compute_start":
                response = await self.agent_compute_start(payload)
            elif action == "agent_compute_status":
                response = self.agent_compute_status(payload)
            elif action == "agent_compute_reply":
                response = await self.agent_compute_reply(payload)
            elif action == "agent_compute_release":
                response = await self.agent_compute_release(payload)
            elif action == "terminate_job":
                process = self.jobs.get(payload["route_id"])
                if process is not None:
                    await self.kill_process(process)
            elif action == "terminate":
                await self.terminate(payload["route_id"], payload.get("error_type", "CancelledError"),
                                     payload.get("error", "Cancelled"), payload.get("grace_seconds"))
            elif action == "recycle":
                # Recycling picks up newly installed workflow packages. Agent
                # children never import customer packages, so the agent
                # template and its children are left alone.
                while any(not child.agent_run for child in self.children.values()):
                    await asyncio.sleep(.05)
                async with self._template_lock:
                    await asyncio.to_thread(self.template.shutdown)
                    await asyncio.to_thread(self.template.start)
            elif action == "package_tool":
                response = await self.helper("src.worker.remote.package_helper", input_payload=payload)
            elif action == "configure":
                pressure = float(payload["pressure_threshold"])
                dangerous = float(payload["dangerous_memory_ratio"])
                if not 0 < pressure < dangerous < 1:
                    raise ValueError("Invalid launcher memory policy")
                self.pressure_threshold, self.dangerous_memory_ratio = pressure, dangerous
            elif action == "ready":
                self.on_ready()
            elif action == "retired":
                self.retired.set()
            else:
                raise ValueError("Unknown launcher command")
            if generation == self.session_generation:
                await self.send({"reply": request["id"], "result": response})
        except Exception as exc:
            if generation == self.session_generation:
                await self.send({"reply": request["id"], "error_type": type(exc).__name__,
                                 "error": str(exc)})

    def check_fork_pressure(self) -> None:
        """Refuse a fork while the launcher's memory is under pressure."""
        current, limit = effective_memory_sample()
        if self.pressure_threshold is None:
            raise RuntimeError("Launcher resource policy has not been configured")
        if (
            current < 0
            or limit <= 0
            or memory_pressure_ratio() >= self.pressure_threshold
        ):
            raise MemoryError("Launcher memory pressure rejects fork")

    def track_child(self, child: Child) -> None:
        """Sample a forked child and watch its result pipe."""
        child.sampler = CPUSampler(pid=child.pid, clock_ticks=get_clock_ticks())
        child.sampler.set_baseline(time.monotonic())
        child.rss_start = child.sampler.start_process_rss_bytes or 0
        if child.agent_run:
            stat = proc_stat(child.pid)
            child.start_ticks = stat.start_ticks if stat is not None else None
        self.children[child.route_id] = child
        asyncio.get_running_loop().add_reader(child.result.fileno(), self.result_ready, child.route_id)

    def reap_exited_agent_children(self) -> None:
        """Reap finished agent children that were orphaned to this process.

        Only the known PIDs, and only while the start time still matches:
        ``waitpid(-1)`` would steal the exit status of asyncio-managed
        subprocesses such as platform jobs.
        """
        for pid, start_ticks in list(self.exited_agent_children.items()):
            stat = proc_stat(pid)
            if stat is None or stat.start_ticks != start_ticks:
                del self.exited_agent_children[pid]
                continue
            if stat.ppid != os.getpid():
                # Still the template's child; the template reaps it.
                continue
            try:
                reaped, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                reaped = pid
            if reaped:
                del self.exited_agent_children[pid]

    @staticmethod
    def agent_route(child: Child, **extra: Any) -> dict[str, Any]:
        """Inventory description of an agent child (live or terminal)."""
        return {
            "route_id": child.route_id,
            "kind": child.kind,
            **({"handler": child.agent_handler, "claim_id": child.summary_claim_id} if child.kind == "agent_summary" else {}),
            "subject_id": child.execution_id,
            **extra,
            "memory_request_bytes": child.memory_request_bytes,
        }

    async def wait_job(self, route_id: str, process: asyncio.subprocess.Process) -> None:
        code = await process.wait()
        try:
            await self.send_terminal(
                {"event": "job_exit", "route_id": route_id, "returncode": code},
                inventory={
                    "route_id": route_id,
                    "kind": "platform_job",
                    "subject_id": self.job_claims.get(route_id, {}).get("job_id"),
                    "lease_token": self.job_claims.get(route_id, {}).get("lease_token"),
                    "memory_request_bytes": self.job_claims.get(route_id, {}).get("memory_request_bytes"),
                },
            )
        finally:
            self.jobs.pop(route_id, None)
            self.job_claims.pop(route_id, None)

    async def send_file_compute_state(self, binding: dict[str, str]) -> None:
        """Wake the API to read retained compute state without sending artifacts."""
        with contextlib.suppress(ConnectionError):
            await self.send({"event": "file_compute_state", "stage_id": binding["stage_id"]})

    send_app_compile_state = send_file_compute_state

    async def send_agent_compute_rpc(
        self, stage: AgentComputeStage, request_id: str, request: AgentComputeRequest
    ) -> None:
        """Forward a child callback only after retaining it for reconnect replay."""
        if stage.state != "running" or stage.pending.get(request_id) is not request:
            return
        with contextlib.suppress(ConnectionError):
            await self.send({
                "event": "agent_compute_rpc",
                **_agent_compute_binding_dict(stage.binding),
                "request_id": request_id,
                "op": request.frame["op"],
                "payload": request.frame["payload"],
                "stream": request.stream,
            })

    async def send_agent_compute_state(self, stage: AgentComputeStage) -> None:
        """Wake the API to read the retained terminal state by exact binding."""
        with contextlib.suppress(ConnectionError):
            await self.send({"event": "agent_compute_state", **_agent_compute_binding_dict(stage.binding)})

    async def resend_agent_compute_rpcs(self) -> None:
        """Replay unresolved child callbacks once after a new inventory frame."""
        for stage in list(self.agent_compute_stages.values()):
            if stage.state != "running":
                continue
            for request_id, request in list(stage.pending.items()):
                await self.send_agent_compute_rpc(stage, request_id, request)

    def _request_compute_retirement_drain(self) -> None:
        if not self._compute_retirement_drain_sent:
            self._compute_retirement_drain_sent = True
            self.task(self.send_compute_retirement_drain())

    async def send_compute_retirement_drain(self) -> None:
        """Use the ordinary drain signal after a bounded compute ledger fills."""
        with contextlib.suppress(ConnectionError):
            await self.send({"event": "draining"})

    @staticmethod
    async def kill_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    def agent_compute_result_ready(self, stage_id: str) -> None:
        """Drain callback frames without ever treating them as workflow results."""
        stage = self.agent_compute_stages.get(stage_id)
        if stage is None or stage.result is None:
            return
        while True:
            try:
                frame = stage.result.get_nowait()
            except Empty:
                return
            except (EOFError, OSError):
                self._mark_agent_compute_terminal(
                    stage, {"ok": False, "error_type": "ProcessCrashError", "error": "Child exited without a terminal frame"}
                )
                return
            if not isinstance(frame, dict) or frame.get("stage_id") != stage.binding.stage_id:
                self._fail_agent_compute_protocol(stage, "Agent compute child sent an invalid frame")
                return
            try:
                frame_size = len(_agent_compute_json(frame, label="Agent compute child frame"))
            except ValueError:
                self._fail_agent_compute_protocol(stage, "Agent compute child frame is not JSON serializable")
                return
            if frame_size > MAX_AGENT_COMPUTE_FRAME_BYTES:
                self._fail_agent_compute_protocol(stage, "Agent compute child frame exceeds the byte limit")
                return
            if frame.get("kind") == "rpc":
                self._receive_agent_compute_rpc(stage, frame)
                if stage.state != "running":
                    return
                continue
            if frame.get("kind") == "terminal":
                terminal: dict[str, Any] = {"ok": frame.get("ok") is True}
                if terminal["ok"]:
                    terminal["result"] = frame.get("result")
                else:
                    terminal["error_type"] = str(frame.get("error_type") or "AgentComputeError")
                    terminal["error"] = str(frame.get("error") or "Agent compute child failed")
                    # A provider may have reported usage or an empty-output
                    # guard before a later budget/runtime failure. Preserve
                    # that bounded evidence; the API remains authoritative
                    # for billing validation.
                    if "result" in frame:
                        terminal["result"] = frame["result"]
                self._mark_agent_compute_terminal(stage, terminal)
                return
            self._fail_agent_compute_protocol(stage, "Agent compute child sent an unknown frame")
            return

    def _receive_agent_compute_rpc(self, stage: AgentComputeStage, frame: dict[str, Any]) -> None:
        request_id = frame.get("request_id")
        op = frame.get("op")
        payload = frame.get("payload")
        stream = frame.get("stream")
        try:
            request_id = _agent_compute_uuid(request_id, "request_id")
        except ValueError as exc:
            self._fail_agent_compute_protocol(stage, str(exc), error_type=type(exc).__name__)
            return
        if not isinstance(op, str) or not op or not isinstance(payload, dict) or type(stream) is not bool:
            self._fail_agent_compute_protocol(stage, "Agent compute RPC frame is invalid")
            return
        if request_id in stage.pending or any(key[0] == request_id for key in stage.reply_receipts):
            self._fail_agent_compute_protocol(stage, "Agent compute child reused a callback request ID")
            return
        frame_bytes = len(_agent_compute_json(frame, label="Agent compute RPC frame"))
        if (
            len(stage.pending) >= MAX_AGENT_COMPUTE_INFLIGHT_RPCS
            or stage.total_rpc_bytes + frame_bytes > MAX_AGENT_COMPUTE_RPC_BYTES
            or stage.request_count >= MAX_AGENT_COMPUTE_RPC_RECEIPTS
        ):
            self._agent_compute_retire_requested = True
            self._fail_agent_compute_protocol(
                stage, "Agent compute callback journal limit is reached", error_type="ResourceLimitError"
            )
            return
        request = AgentComputeRequest(frame=frame, frame_bytes=frame_bytes, stream=stream)
        stage.pending[request_id] = request
        stage.total_rpc_bytes += frame_bytes
        stage.request_count += 1
        self.task(self.send_agent_compute_rpc(stage, request_id, request))

    def _mark_agent_compute_terminal(self, stage: AgentComputeStage, terminal: dict[str, Any]) -> None:
        if stage.state == "terminal":
            return
        stage.state, stage.terminal = "terminal", terminal
        stage.pending.clear()
        stage.total_rpc_bytes = 0
        if stage.result is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(stage.result.fileno())
        if stage.work is not None:
            stage.work.close()
            stage.work = None
        if stage.result is not None:
            stage.result.close()
            stage.result = None
        self.task(self.send_agent_compute_state(stage))

    def _fail_agent_compute_protocol(
        self, stage: AgentComputeStage, error: str, *, error_type: str = "ProtocolError"
    ) -> None:
        self._mark_agent_compute_terminal(
            stage, {"ok": False, "error_type": error_type, "error": error}
        )
        self.task(self._stop_agent_compute_stage(stage))

    async def _fail_agent_compute_stage(
        self, stage: AgentComputeStage, error_type: str, error: str, *, stop_child: bool = False
    ) -> None:
        self._mark_agent_compute_terminal(
            stage, {"ok": False, "error_type": error_type, "error": error[:4096]}
        )
        if stop_child:
            with contextlib.suppress(Exception):
                await self._stop_agent_compute_stage(stage)

    async def _stop_agent_compute_stage(self, stage: AgentComputeStage) -> None:
        if stage.pid is None:
            return
        stat = proc_stat(stage.pid)
        if stat is None or stat.state == "Z":
            return
        os.kill(stage.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stat = proc_stat(stage.pid)
            if stat is None or stat.state == "Z":
                return
            await asyncio.sleep(.05)
        os.kill(stage.pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stat = proc_stat(stage.pid)
            if stat is None or stat.state == "Z":
                return
            await asyncio.sleep(.05)
        raise RuntimeError("Agent compute child did not stop")

    def agent_compute_telemetry(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for stage in self.agent_compute_stages.values():
            sampler = stage.sampler
            if sampler is not None and stage.state == "running":
                sampler.sample(time.monotonic())
            records.append({
                "stage_id": stage.binding.stage_id,
                "pid": stage.pid,
                "state": stage.state,
                "rss_bytes": sampler.last_process_rss_bytes if sampler else None,
                "peak_memory_bytes": sampler.peak_process_rss_bytes if sampler else None,
                "peak_cpu_cores": sampler.peak_cpu_cores if sampler else None,
            })
        return records

    def result_ready(self, route_id: str) -> None:
        child = self.children.get(route_id)
        if child is None:
            return
        asyncio.get_running_loop().remove_reader(child.result.fileno())
        try:
            result = child.result.get_nowait()
        except (EOFError, OSError):
            result = self.failure(child, "ProcessCrashError", "Child exited without a result")
        self.task(self.finish(child, result))

    @staticmethod
    def failure(child: Child, error_type: str, error: str) -> dict[str, Any]:
        return {"execution_id": child.execution_id, "success": False, "status": "Failed",
                "error": error, "error_message": error, "error_type": error_type,
                "duration_ms": int((time.monotonic() - child.started) * 1000)}

    async def finish(self, child: Child, result: dict[str, Any]) -> None:
        if self.children.pop(child.route_id, None) is None:
            return
        if child.terminal_reason is not None:
            result = self.failure(child, *child.terminal_reason)
        if child.sampler is not None:
            child.sampler.sample(time.monotonic())
            child.rss_peak = max(child.rss_peak, child.sampler.peak_process_rss_bytes or 0)
            child.growth_peak = max(child.growth_peak, child.rss_peak - child.rss_start)
        asyncio.get_running_loop().remove_reader(child.result.fileno())
        child.work.close()
        child.result.close()
        if child.agent_run:
            if child.start_ticks is not None:
                self.exited_agent_children[child.pid] = child.start_ticks
            await self.send_terminal(
                {"event": "agent_exit", "route_id": child.route_id, "ok": result.get("ok") is True,
                 "error_type": result.get("error_type"), "error": result.get("error")},
                inventory=self.agent_route(child),
            )
            return
        metrics = result.setdefault("metrics", {})
        if isinstance(metrics, dict):
            metrics["peak_memory_growth_bytes"] = max(metrics.get("peak_memory_growth_bytes") or 0,
                                                       child.growth_peak)
            if child.terminal_reason is not None and child.terminal_reason[0] == "MemoryLimitExceeded":
                metrics["peak_memory_growth_bytes"] = max(
                    metrics["peak_memory_growth_bytes"], child.memory_kill_growth_bytes or 0,
                )
            metrics["peak_process_rss_bytes"] = max(metrics.get("peak_process_rss_bytes") or 0,
                                                     child.rss_peak)
            if child.sampler is not None and child.sampler.peak_cpu_cores is not None:
                metrics["peak_cpu_cores"] = child.sampler.peak_cpu_cores
        inventory = {
            "route_id": child.route_id,
            "kind": child.kind,
            "subject_id": child.execution_id,
            "service": child.service,
            "active_execution": child.active_execution,
            "started_at": child.started_at,
            "timeout_seconds": child.timeout,
            "memory_request_bytes": child.memory_request_bytes,
        }
        try:
            await self.send_terminal(
                {"event": "result", "route_id": child.route_id, "result": result},
                inventory=inventory,
            )
        except PayloadSerializationError:
            logger.warning("Execution result could not be serialized; sending a failed result")
            await self.send_terminal(
                {"event": "result", "route_id": child.route_id,
                 "result": self.failure(child, "SerializationError", "Execution result could not be serialized")},
                inventory=inventory,
            )

    async def terminate(self, route_id: str, error_type: str, error: str,
                        grace: float | None = None) -> None:
        child = self.children.get(route_id)
        if child is None or child.stopping:
            return
        child.stopping = True
        if child.service is None or error_type == "MemoryLimitExceeded":
            child.terminal_reason = (error_type, error)
        with contextlib.suppress(ProcessLookupError):
            os.kill(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + (child.grace if grace is None else grace)
        while route_id in self.children and time.monotonic() < deadline:
            stat = proc_stat(child.pid)
            if stat is None or stat.state == "Z":
                # Gone, or exited and awaiting its reaper (an orphaned child
                # waits for the launcher's monitor).
                break
            await asyncio.sleep(.05)
        if route_id in self.children:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child.pid, signal.SIGKILL)
            await self.finish(child, self.failure(child, error_type, error))

    async def monitor(self) -> None:
        tick = 0
        while True:
            # A package recycle intentionally stops the template while holding
            # this lock. Observe liveness only outside that replacement window.
            async with self._template_lock:
                if not self.compute_only and not self.template.is_alive():
                    # Preserve the canonical pool's template-crash recovery;
                    # live children retain their existing pipes and identities.
                    logger.warning("Execution template died; replacing it")
                    await asyncio.to_thread(self.template.start)
            async with self._agent_template_lock:
                if self.agent_template is not None and not self.agent_template.is_alive():
                    # Its live children keep running; reap_exited_agent_children
                    # reaps those that re-parent to the launcher.
                    logger.warning("Agent template died; replacing it")
                    await asyncio.to_thread(self.agent_template.start)
            async with self._agent_compute_template_lock:
                if self._agent_compute_template_ready and not self.agent_compute_template.is_alive():
                    logger.warning("Agent compute template died; replacing it")
                    await asyncio.to_thread(self.agent_compute_template.start)
            current, limit = effective_memory_sample()
            samples = {}
            for child in list(self.children.values()):
                assert child.sampler is not None
                child.sampler.sample(time.monotonic())
                rss = child.sampler.last_process_rss_bytes or 0
                child.rss_peak = max(child.rss_peak, rss)
                child.growth_peak = max(child.growth_peak, rss - child.rss_start)
                child.current_growth = max(0, rss - child.rss_start)
                samples[child.route_id] = {"growth_bytes": child.current_growth,
                                          "memory_peak_bytes": child.growth_peak,
                                          "peak_cpu_cores": child.sampler.peak_cpu_cores,
                                          "rss_bytes": rss}
                if child.stopping:
                    continue
                reason = None
                if child.timeout and time.monotonic() - child.started > child.timeout:
                    reason = ("TimeoutError", "Execution timed out")
                if reason:
                    # Once observed, a deadline/resource violation owns the
                    # terminal outcome even if the result pipe becomes ready.
                    if child.service is None:
                        child.terminal_reason = reason
                    self.task(self.terminate(child.route_id, *reason, grace=0))
            for stage in self.agent_compute_stages.values():
                if stage.state == "running" and stage.sampler is not None:
                    stage.sampler.sample(time.monotonic())
            self.stop_largest_when_dangerous(
                current, limit,
                dangerous=memory_pressure_ratio() >= (self.dangerous_memory_ratio or 1),
            )
            self.reap_exited_agent_children()
            if tick % 4 == 0:
                async with self._template_lock:
                    await asyncio.to_thread(self.template.collect_child_exit_statuses)
                async with self._agent_template_lock:
                    if self.agent_template is not None:
                        await asyncio.to_thread(self.agent_template.collect_child_exit_statuses)
                async with self._agent_compute_template_lock:
                    if self._agent_compute_template_ready:
                        await asyncio.to_thread(
                            self.agent_compute_template.collect_child_exit_statuses
                        )
                with contextlib.suppress(ConnectionError):
                    await self.send({"event": "sample", "current": current, "limit": limit,
                                     "pressure_ratio": memory_pressure_ratio(), "runs": samples,
                                     "compute_stages": self.file_operations.telemetry(),
                                     "agent_compute_stages": self.agent_compute_telemetry()})
            tick += 1
            await asyncio.sleep(.25)

    def stop_largest_when_dangerous(
        self, current: int, limit: int, *, dangerous: bool | None = None
    ) -> None:
        """Mirror the canonical pool: stop one largest-growing child per tick."""
        if self.dangerous_memory_ratio is None or dangerous is False or (
            dangerous is None and current < limit * self.dangerous_memory_ratio
        ):
            return
        candidates = [child for child in self.children.values()
                      if not child.stopping and child.terminal_reason is None]
        compute_stages = [
            stage for stage in self.agent_compute_stages.values()
            if stage.state == "running" and stage.sampler is not None
        ]
        if candidates:
            child = max(candidates, key=lambda item: item.current_growth)
            if not compute_stages or child.current_growth >= max(
                stage.sampler.growth_bytes() or 0 for stage in compute_stages
            ):
                reason = ("MemoryLimitExceeded", "Runner memory pressure")
                child.terminal_reason = reason
                self.task(self.terminate(child.route_id, *reason, grace=0))
                return
        if compute_stages:
            stage = max(compute_stages, key=lambda item: item.sampler.growth_bytes() or 0)
            self.task(self._fail_agent_compute_stage(
                stage, "MemoryLimitExceeded", "Runner memory pressure", stop_child=True
            ))

    async def stop(self) -> None:
        self.on_stop()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for child in self.children.values():
            with contextlib.suppress(ProcessLookupError):
                os.kill(child.pid, signal.SIGKILL)
            child.work.close()
            child.result.close()
        self.children.clear()
        await asyncio.gather(*(self.kill_process(p) for p in self.jobs.values()))
        for stage in list(self.agent_compute_stages.values()):
            with contextlib.suppress(Exception):
                await self._stop_agent_compute_stage(stage)
            if stage.work is not None:
                stage.work.close()
            if stage.result is not None:
                with contextlib.suppress(Exception):
                    asyncio.get_running_loop().remove_reader(stage.result.fileno())
                stage.result.close()
        self.agent_compute_stages.clear()
        await self.file_operations.shutdown()
        await asyncio.to_thread(self.template.shutdown)
        if self.agent_template is not None:
            await asyncio.to_thread(self.agent_template.shutdown)
        if self._agent_compute_template_ready:
            await asyncio.to_thread(self.agent_compute_template.shutdown)
            self._agent_compute_template_ready = False

    async def begin_drain(self) -> None:
        """Ask the API to drain and retain physical children for its grace."""
        with contextlib.suppress(ConnectionError):
            await self.send({"event": "draining"})
        deadline = time.monotonic() + LAUNCHER_DRAIN_GRACE_SECONDS
        # A child leaves ``children`` before its terminal result is ACKed.
        # Do not cancel that replay task merely because the process itself
        # exited; the canonical owner must persist the terminal outcome.
        while (
            self.children
            or self.jobs
            or self.file_operations.has_live
            or any(stage.state in {"starting", "running"} for stage in self.agent_compute_stages.values())
            or self.pending_terminal
        ) and time.monotonic() < deadline:
            await asyncio.sleep(0.1)


async def run_launcher(
    *,
    on_ready: Callable[[], None] | None = None,
    on_stop: Callable[[], None] | None = None,
) -> int:
    """Keep optional private networking alive through physical worker drain."""
    from src.core.storage import close_shared_storage_clients
    from src.worker.network import worker_network

    try:
        async with worker_network():
            return await _run_launcher(on_ready=on_ready, on_stop=on_stop)
    finally:
        await close_shared_storage_clients()


async def _run_launcher(
    *,
    on_ready: Callable[[], None] | None = None,
    on_stop: Callable[[], None] | None = None,
) -> int:
    """Run one physical launcher, reconnecting without killing live children."""
    api_url = os.environ.get("BIFROST_API_URL", "http://api:8000").rstrip("/")
    control_url = api_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    control_url += "/api/worker-control"
    control_headers = load_control_headers(control_url)
    identity = load_launcher_identity()
    secret = os.environ[
        "BIFROST_WORKER_CONTROL_SECRET"
        if identity.form == "external" or os.environ.get("BIFROST_WORKER_COMPUTE_ONLY") == "1"
        else "BIFROST_SECRET_KEY"
    ]
    launcher: Launcher | None = None
    candidate: Launcher | None = None
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    try:
        drain_task: asyncio.Task[None] | None = None
        while launcher is None or (not launcher.retired.is_set() and not launcher.failed):
            try:
                async with connect_control(control_url, control_headers) as socket:
                    if launcher is None:
                        if candidate is None:
                            candidate = Launcher(
                                socket, identity=identity, secret=secret,
                                on_ready=on_ready, on_stop=on_stop,
                                authenticated_control=bool(control_headers),
                            )
                        else:
                            # A lost welcome may follow a committed enrollment.
                            # Keep the physical identity's monotonic hello sequence.
                            candidate.socket = socket
                        # ``agent.loop.v1`` is advertised only after the
                        # physical pure-compute template is genuinely ready.
                        if not candidate.compute_only:
                            await candidate.start_agent_compute_template()
                        # Enroll before the potentially minute-long requirements
                        # install. The API's ordinary worker heartbeat can then
                        # keep the canonical row alive while it waits for the
                        # inventory below; no work is ready to route yet.
                        await candidate.hello({})
                        launcher = candidate
                        try:
                            setup = asyncio.create_task(candidate.start())
                            while not setup.done():
                                try:
                                    await asyncio.wait_for(asyncio.shield(setup), timeout=5)
                                except TimeoutError:
                                    current, limit = effective_memory_sample()
                                    with contextlib.suppress(ConnectionError):
                                        await candidate.send({
                                            "event": "sample", "current": current,
                                            "limit": limit,
                                            "pressure_ratio": memory_pressure_ratio(), "runs": {},
                                        })
                            await asyncio.gather(setup)
                        except Exception:
                            # Initialization is not a transport failure. Exit so
                            # the provider can replace this failed physical worker.
                            launcher.failed = True
                            raise
                    else:
                        await launcher.reconnect(socket, launcher.requirements)
                    await launcher.send({
                        "event": "inventory", "inventory": launcher.inventory(),
                        "requirements": launcher.requirements,
                    })
                    await launcher.resend_agent_compute_rpcs()
                    for event in launcher.pending_terminal.values():
                        await launcher.send(event)
                    drain_task = await _receive_control_commands(
                        launcher, socket, stopping, drain_task
                    )
                    if stopping.is_set() and drain_task is not None and drain_task.done():
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if launcher is not None and launcher.failed:
                    message = "Remote launcher initialization or supervision failed"
                    level = logging.ERROR
                else:
                    message = "Remote control connection lost; preserving local children"
                    level = logging.WARNING
                if control_headers:
                    # Transport exceptions can retain request headers or echoed secrets.
                    logger.log(level, "%s (%s)", message, type(exc).__name__)
                else:
                    logger.log(level, message, exc_info=True)
                if stopping.is_set() or (launcher is not None and launcher.failed):
                    break
                await asyncio.sleep(1)
    finally:
        if launcher is not None:
            await _shutdown_launcher(launcher, drain_task, stopping)
    return 1 if launcher is not None and launcher.failed else 0


async def main() -> int:
    return await run_launcher()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(main()))
