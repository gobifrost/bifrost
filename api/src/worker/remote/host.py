"""API-side control host for physical remote execution launchers.

This module deliberately does not import ``src.main`` or mutate application
startup. The API lifespan owns ``start_remote_control_host`` and registers the
router before the MCP catch-all route.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette.websockets import WebSocketState

from src.config import get_settings
from src.core.database import get_liveness_db_context
from src.jobs.consumers.package_install import PackageInstallConsumer
from src.jobs.schedulers.platform_jobs import claim_platform_job, run_claimed_platform_job
from src.models.orm.execution_workers import ExecutionWorker
from src.services.execution.template_process import TEMPLATE_START_TIMEOUT_SECONDS
from src.services.memory_requests import MemoryBudget
from src.services.worker_pool.claims import (
    REMOTE_WORKER_HANDLER_NAMES,
    REMOTE_WORKER_HANDLERS,
    admit_worker,
    register_static_worker,
)
from src.worker.pool_main import PoolWorker
from src.worker.remote.agent_route import AgentRoute, RemoteAgentConsumer
from src.worker.remote.fencing import LeaseRef, remote_owner_context
from src.worker.remote.ownership import (
    DEFAULT_OWNER_LEASE_SECONDS,
    RemoteOwnerLease,
    acquire_remote_owner,
    owns_remote_session,
    release_remote_owner,
    renew_remote_owner,
)
from src.worker.remote.protocol import (
    AuthenticationError,
    ControlEnvelope,
    FragmentAssembler,
    ProtocolError,
    SignedHello,
    nonce_digest,
    fragment,
    verify_hello,
)


router = APIRouter()

COMMAND_REPLY_SECONDS = 15


def _reply_timeout(action: str) -> float:
    """How long a launcher command may take to reply."""
    if action in ("package_tool", "recycle"):
        # Package installation uses the existing long bounded pip calls.
        return 600
    if action == "agent_run":
        # It may wait while the launcher replaces a dead agent template.
        return TEMPLATE_START_TIMEOUT_SECONDS + COMMAND_REPLY_SECONDS
    return COMMAND_REPLY_SECONDS


class RemoteProcess:
    def __init__(self, bridge: Bridge, route_id: str) -> None:
        self.bridge = bridge
        self.route_id = route_id
        self.returncode: int | None = None
        self.exited: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def wait(self) -> int:
        return await asyncio.shield(self.exited)

    async def terminate(self) -> None:
        if self.returncode is None:
            await self.bridge.request("terminate_job", {"route_id": self.route_id})
            await self.wait()


class Bridge:
    """Ephemeral command transport; PostgreSQL remains the durable authority."""
    def __init__(
        self, socket: WebSocket, hello: dict[str, Any], lease: RemoteOwnerLease
    ) -> None:
        self.socket = socket
        self.connected = True
        self.current = int(hello["current"])
        self.limit = int(hello["limit"])
        self.pressure_ratio = float(hello["pressure_ratio"]) if "pressure_ratio" in hello else (
            self.current / self.limit if self.limit > 0 else 1.0
        )
        self.baseline_bytes = self.current
        self.hostname = hello["hostname"]
        self.sample_at = time.monotonic()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.jobs: dict[str, RemoteProcess] = {}
        self.platform_routes: dict[tuple[str, str], str] = {}
        self.pool: Any = None
        # Agent runs in launcher children; kept across reconnects like jobs.
        self.agents: RemoteAgentConsumer | None = None
        self.admission: Any = None
        self.requirements = hello["requirements"]
        self.inventory: dict[str, Any] | None = None
        self.inventory_ready = asyncio.Event()
        self.reconciliation_ready = asyncio.Event()
        self._deferred_terminal: list[dict[str, Any]] = []
        self.lease = lease
        self.handoff = False
        self.lease_ref: LeaseRef | None = None
        self._sequence = 0
        self.results: set[asyncio.Task[Any]] = set()
        self.lock = asyncio.Lock()

    def memory(self) -> tuple[int, int]:
        if not self.connected or time.monotonic() - self.sample_at > 3:
            return -1, self.limit
        return self.current, self.limit

    async def request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.connected:
            raise ConnectionError("Launcher disconnected")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            try:
                async with self.lock:
                    message = ControlEnvelope(
                        session_generation=self.lease.session_generation,
                        sequence=self._sequence + 1,
                        kind="command",
                        payload={"id": request_id, "action": action, "payload": payload},
                    )
                    sent = 0
                    for frame in fragment(message):
                        await self.socket.send_text(frame.to_json())
                        sent += 1
                    self._sequence += sent
            except (WebSocketDisconnect, OSError, RuntimeError) as exc:
                self.connected = False
                raise ConnectionError("Launcher control transport is unavailable") from exc
            async with asyncio.timeout(_reply_timeout(action)):
                return await asyncio.shield(future)
        finally:
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def acknowledge(self, event_id: str) -> None:
        """Release a launcher-retained terminal result after canonical delivery."""
        async with self.lock:
            self._sequence += 1
            await self.socket.send_text(ControlEnvelope(
                session_generation=self.lease.session_generation,
                sequence=self._sequence,
                kind="ack",
                payload={"event_id": event_id},
            ).to_json())

    async def spawn(self, claim: Any) -> RemoteProcess:
        key = (str(claim.id), str(claim.lease_token))
        adopted_route = self.platform_routes.get(key)
        if adopted_route is not None:
            process = self.jobs.get(adopted_route)
            if process is not None:
                return process
        route_id = str(uuid4())
        process = RemoteProcess(self, route_id)
        self.jobs[route_id] = process
        self.platform_routes[key] = route_id
        request_bytes = self.memory_request_bytes(str(claim.id))
        try:
            await self.request("platform_job", {"route_id": route_id,
                               "job_id": str(claim.id), "lease_token": str(claim.lease_token),
                               "memory_request_bytes": (
                                   request_bytes
                                   if request_bytes is not None
                                   else claim.memory_required_bytes
                               )})
        except (ConnectionError, TimeoutError):
            # The launcher may have forked before its reply was lost. Keep the
            # route until authoritative inventory proves it absent; otherwise
            # the canonical supervisor could requeue a second attempt.
            return process
        except BaseException:
            self.jobs.pop(route_id, None)
            if self.platform_routes.get(key) == route_id:
                self.platform_routes.pop(key, None)
            raise
        return process

    async def adopt_platform_job(self, route_id: str, claim: Any) -> RemoteProcess:
        """Attach the canonical platform supervisor to an already-live process."""
        key = (str(claim.id), str(claim.lease_token))
        self.platform_routes[key] = route_id
        process = self.jobs.get(route_id)
        if process is None:
            process = RemoteProcess(self, route_id)
            self.jobs[route_id] = process
        await self._release_deferred_terminals()
        return process

    def reject_missing_platform_routes(self, inventory: dict[str, Any]) -> None:
        """Resolve only launches the physical launcher did not report.

        A lost platform-launch reply is uncertain until the signed inventory
        arrives. Once that inventory omits the route (including its retained
        terminal journal), unblock the canonical supervisor as a failed child
        instead of leaving its heartbeat alive until the job timeout.
        """
        observed = {
            entry.get("route_id")
            for entry in [*inventory.get("routes", []), *inventory.get("terminal_routes", [])]
            if isinstance(entry, dict) and isinstance(entry.get("route_id"), str)
        }
        for route_id, process in tuple(self.jobs.items()):
            if route_id in observed:
                continue
            self.jobs.pop(route_id, None)
            for key, mapped_route in tuple(self.platform_routes.items()):
                if mapped_route == route_id:
                    self.platform_routes.pop(key, None)
            process.returncode = 1
            if not process.exited.done():
                process.exited.set_result(process.returncode)

    def memory_request_bytes(self, key: str) -> int | None:
        """Read an already-held admission request for reconnect inventory."""
        if self.admission is None:
            return None
        return self.admission.request_bytes(key)

    async def receive(self, event: dict[str, Any]) -> None:
        if "reply" in event:
            future = self.pending.get(event["reply"])
            if future is not None and not future.done():
                if "error" in event:
                    error = MemoryError if event.get("error_type") == "MemoryError" else RuntimeError
                    future.set_exception(error(event["error"]))
                else:
                    future.set_result(event["result"])
        elif event.get("event") == "sample":
            self.current, self.limit = int(event["current"]), int(event["limit"])
            self.pressure_ratio = float(event["pressure_ratio"]) if "pressure_ratio" in event else (
                self.current / self.limit if self.limit > 0 else 1.0
            )
            self.sample_at = time.monotonic()
            if self.pool is not None:
                await self.pool.handle_event(event)
        elif event.get("event") == "inventory":
            inventory = event.get("inventory")
            if not isinstance(inventory, dict):
                raise ProtocolError("launcher inventory must be an object")
            baseline_bytes = inventory.get("baseline_bytes")
            if (
                isinstance(baseline_bytes, bool)
                or not isinstance(baseline_bytes, int)
                or baseline_bytes < 0
            ):
                raise ProtocolError("launcher inventory must include its baseline memory")
            self.baseline_bytes = baseline_bytes
            requirements = event.get("requirements")
            if requirements is not None:
                if not isinstance(requirements, dict):
                    raise ProtocolError("launcher requirements must be an object")
                self.requirements = requirements
            self.inventory = inventory
            self.inventory_ready.set()
        elif event.get("event") in {"result", "job_exit", "agent_exit"}:
            # Inventory is authoritative when an API takes over. In
            # particular, a terminal replay can arrive before the old route
            # has been recreated in this API process. Retain it until that
            # reconciliation succeeds; an ACK before then would lose it.
            if not self.reconciliation_ready.is_set():
                self._deferred_terminal.append(event)
            else:
                await self._deliver_terminal(event)
        elif event.get("event") == "draining":
            # The router tells this session's executor to drain after the
            # transport event is accepted. It is not a pool result.
            return
        else:
            # Completion may call SDK/Redis/DB or send a new command. Never
            # block this receiver, which must also deliver command replies.
            async def handle_event() -> None:
                if self.lease_ref is None:
                    await self.pool.handle_event(event)
                    return
                with remote_owner_context(self.lease_ref):
                    await self.pool.handle_event(event)
            task = asyncio.create_task(handle_event())
            self.results.add(task)
            task.add_done_callback(self.results.discard)
            task.add_done_callback(_observe_task)

    async def reconciliation_complete(self) -> None:
        """Permit terminal replay only after canonical routes were restored."""
        self.reconciliation_ready.set()
        deferred, self._deferred_terminal = self._deferred_terminal, []
        for event in deferred:
            if await self._deliver_terminal(event):
                continue
            # A terminal platform runner already committed its own canonical
            # result. Reconciliation rejected its old route, so retaining the
            # exit would hold a dead launcher open forever.
            if event.get("event") == "job_exit" and isinstance(event.get("_event_id"), str):
                await self.acknowledge(event["_event_id"])
            else:
                self._deferred_terminal.append(event)

    async def _release_deferred_terminals(self) -> None:
        deferred, self._deferred_terminal = self._deferred_terminal, []
        for event in deferred:
            if not await self._deliver_terminal(event):
                self._deferred_terminal.append(event)

    async def _deliver_terminal(self, event: dict[str, Any]) -> bool:
        event_id = event.get("_event_id")
        if event.get("event") == "job_exit":
            route_id = event.get("route_id")
            process = self.jobs.pop(route_id, None) if isinstance(route_id, str) else None
            if process is None:
                # The platform supervisor is attached from inventory. Do not
                # discard an exit that beat that attachment.
                return False
            process.returncode = int(event["returncode"])
            if not process.exited.done():
                process.exited.set_result(process.returncode)
            for key, mapped_route in tuple(self.platform_routes.items()):
                if mapped_route == route_id:
                    self.platform_routes.pop(key, None)
            if isinstance(event_id, str):
                await self.acknowledge(event_id)
            return True

        if event.get("event") == "agent_exit":
            agents = self.agents
            if agents is None or not agents.tracks(event.get("route_id")):
                if not self.reconciliation_ready.is_set():
                    # Its route may still be adopted from inventory.
                    return False
                # Reconciliation rejected the route, or its run was already
                # settled: nothing here waits for this exit.
                if isinstance(event_id, str):
                    await self.acknowledge(event_id)
                return True
            deliver = agents.handle_exit
        elif self.pool is None:
            return False
        else:
            deliver = self.pool.handle_event

        # Completion may call SDK/Redis/DB or send a new command. Never
        # block this receiver, which must also deliver command replies. The
        # launcher receives ACK only after that canonical mutation returns.
        async def handle_event() -> None:
            if self.lease_ref is None:
                await deliver(event)
            else:
                with remote_owner_context(self.lease_ref):
                    await deliver(event)
            if isinstance(event_id, str):
                await self.acknowledge(event_id)

        task = asyncio.create_task(handle_event())
        self.results.add(task)
        task.add_done_callback(self.results.discard)
        task.add_done_callback(_observe_task)
        socket = self.socket

        def reconnect_on_failure(done: asyncio.Task[Any]) -> None:
            if done.cancelled() or done.exception() is None or self.socket is not socket:
                return
            async def close_current_socket() -> None:
                with contextlib.suppress(Exception):
                    await socket.close(code=1011, reason="terminal delivery failed")
            asyncio.create_task(close_current_socket())

        task.add_done_callback(reconnect_on_failure)
        return True

    def replace_socket(self, socket: WebSocket, lease: RemoteOwnerLease) -> None:
        """Fence the old socket while preserving the launcher's live routes."""
        self.socket = socket
        self.lease = lease
        self.connected = True
        self.sample_at = time.monotonic()
        self.inventory = None
        self.inventory_ready.clear()
        self.reconciliation_ready.clear()

    def disconnect(self, socket: WebSocket | None = None) -> None:
        if socket is not None and socket is not self.socket:
            return
        self.connected = False
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Launcher disconnected"))


def _observe_task(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logging.getLogger(__name__).error("Thin control task failed", exc_info=task.exception())


class PackageProxy(PackageInstallConsumer):
    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge

    @property
    def _worker_id(self) -> str:
        return self.bridge.pool.worker_id

    async def _package_tool(self, operation: str, **payload: Any) -> str | None:
        result = await self.bridge.request(
            "package_tool", {"operation": operation, **payload}
        )
        error = result.get("error")
        return error if isinstance(error, str) else None

    async def _pip_install(self, package: str, version: str | None) -> str | None:
        return await self._package_tool("install", package=package, version=version)

    async def _pip_uninstall(self, package: str) -> str | None:
        return await self._package_tool("uninstall", package=package)

    async def _pip_install_requirements(self) -> str | None:
        return await self._package_tool("requirements")

    async def _recycle_workers(self) -> None:
        await self.bridge.pool.drain_and_restart_template()

    async def _update_pool_packages(self) -> None:
        pool = self.bridge.pool
        if not pool._started:
            return
        try:
            result = await self.bridge.request("package_tool", {"operation": "inventory"})
            packages = result["packages"]
            redis = await pool._get_redis()
            await redis.hset(
                f"bifrost:pool:{pool.worker_id}", "packages", json.dumps(packages)
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("Failed to update packages in Redis: %s", exc)


class ApiExecutor(PoolWorker):
    """One admitted remote worker: workflows, agent runs, services and
    platform jobs, each in a launcher child."""

    def __init__(self, bridge: Bridge, incarnation_id: UUID, *, remote_static: bool = False) -> None:
        def ready() -> None:
            notification = asyncio.create_task(bridge.request("ready", {}))
            notification.add_done_callback(_observe_task)
        super().__init__(
            incarnation_id=None if remote_static else incarnation_id,
            job_uid="remote-controlled",
            pod_uid="remote-controlled",
            on_ready=ready,
            handlers=REMOTE_WORKER_HANDLERS,
        )
        self.incarnation_id = incarnation_id
        self.bridge = bridge
        self._reconcile_lock = asyncio.Lock()
        self._reconciled_generation = 0

    async def _initialize_runtime(self) -> bool:
        return True

    async def _start_executor(self) -> None:
        """Keep the canonical heartbeat alive while the launcher bootstraps."""
        await self.bridge.inventory_ready.wait()
        from src.services.execution.process_pool import _notify_requirements_failures
        from src.services.execution.requirements_setup_result import RequirementsInstallResult

        requirements = RequirementsInstallResult.from_json_dict(self.bridge.requirements)
        await _notify_requirements_failures(requirements)
        await super()._start_executor()

    async def _close_runtime(self) -> None:
        # The app lifespan owns this API replica's database engine.
        pass

    async def _start_background_control(self) -> list[asyncio.Task[Any]]:
        # src.main already starts this API replica's canonical controllers.
        return []

    def _make_workflow_consumer(self) -> Any:
        from src.worker.remote.remote_pool import ThinRemoteProcessPool
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        self.bridge.pool = ThinRemoteProcessPool(self.bridge, worker_id=str(self.incarnation_id))
        from src.services.execution.requirements_setup_result import RequirementsInstallResult
        self.bridge.pool._apply_requirements_status(
            RequirementsInstallResult.from_json_dict(self.bridge.requirements)
        )
        return WorkflowExecutionConsumer(pool=self.bridge.pool)

    def _make_package_consumer(self) -> PackageProxy:
        return PackageProxy(self.bridge)

    def _make_agent_consumer(self) -> RemoteAgentConsumer:
        if self.bridge.agents is None:
            self.bridge.agents = RemoteAgentConsumer(self.bridge, worker_id=self.incarnation_id)
        return self.bridge.agents

    def _measure_budget(self) -> MemoryBudget:
        current, limit = self.bridge.memory()
        if current < 0 or limit <= 0:
            raise RuntimeError("Launcher telemetry unavailable")
        return MemoryBudget(limit_bytes=limit, baseline_bytes=self.bridge.baseline_bytes,
                            threshold=self.settings.memory_pressure_threshold)

    def _under_pressure(self) -> bool:
        current, limit = self.bridge.memory()
        return (
            current < 0
            or limit <= 0
            or self.bridge.pressure_ratio >= self.settings.memory_pressure_threshold
        )

    def _claims_open(self) -> bool:
        """Closed from a reconnect until that socket's inventory is
        reconciled. The launcher takes its inventory before it reads any
        command, so work launched in between would be missing from it and
        reconciliation would fail its live child. Every claim (workflow and
        agent runs, services, platform jobs) passes ``Admission.claiming``."""
        return self.bridge.reconciliation_ready.is_set()

    async def _execute_platform_job(self, claim: Any) -> bool:
        return await run_claimed_platform_job(claim, backend=self.bridge)

    async def _claim_platform_job(self, fits_bytes: int) -> Any:
        return await claim_platform_job(backend="local", fits_bytes=fits_bytes,
                                        memory_sample=self.bridge.memory())

    async def _start_service_claim_loop(self) -> None:
        """Adopt physical routes after memory admission exists, before claims run."""
        assert self.admission is not None
        self.bridge.admission = self.admission
        snapshot = await self._reconcile_transport_inventory()
        assert snapshot is not None
        await super()._start_service_claim_loop()
        await self._apply_reconciled_snapshot(snapshot)
        await self.bridge.reconciliation_complete()

    async def reconcile_transport_inventory(self, *, expected_generation: int | None = None) -> None:
        """Reconcile a same-owner reconnect before its terminal replay runs."""
        if (
            expected_generation is not None
            and self.bridge.lease.session_generation != expected_generation
        ):
            return
        if self._service_claim_loop is None:
            # Initial inventory is consumed by _start_service_claim_loop,
            # after PoolWorker has constructed its admission gate.
            return
        snapshot = await self._reconcile_transport_inventory(expected_generation)
        if snapshot is None:
            return
        await self._apply_reconciled_snapshot(snapshot)
        if (
            expected_generation is not None
            and self.bridge.lease.session_generation != expected_generation
        ):
            return
        await self.bridge.reconciliation_complete()

    async def _reconcile_transport_inventory(
        self, expected_generation: int | None = None
    ) -> dict[str, Any] | None:
        await self.bridge.inventory_ready.wait()
        assert self.bridge.pool is not None and self.bridge.inventory is not None
        async with self._reconcile_lock:
            generation = self.bridge.lease.session_generation
            if expected_generation is not None and generation != expected_generation:
                return None
            if self._reconciled_generation == generation:
                return {
                    "workflow_requests": {}, "services": [], "platform_jobs": [], "agent_runs": {},
                }
            snapshot = await self.bridge.pool.reconcile_inventory(self.bridge.inventory)
            if expected_generation is not None and self.bridge.lease.session_generation != generation:
                return None
            self.bridge.reject_missing_platform_routes(self.bridge.inventory)
            assert self.bridge.agents is not None
            await self.bridge.agents.reject_missing(self.bridge.inventory)
            self._reconciled_generation = generation
            return snapshot

    async def _apply_reconciled_snapshot(self, snapshot: dict[str, Any]) -> None:
        assert self.admission is not None
        for execution_id, request_bytes in snapshot["workflow_requests"].items():
            self.admission.hold(execution_id, request_bytes, workflow=True)
            self.admission.started(execution_id)
            self._remember_started(execution_id)
        agents = self.bridge.agents
        assert agents is not None
        for route_id, (run_id, request_bytes) in snapshot["agent_runs"].items():
            if agents.tracks(route_id):
                # A same-owner reconnect: its original wait still runs.
                continue
            self._wait_for_adopted_agent_run(agents.adopt(route_id, run_id), request_bytes)
        if self._service_claim_loop is None:
            return
        for owned in snapshot["services"]:
            key = str(owned.attempt_id)
            previous = self._service_claim_loop._owned.get(key)
            if previous is not None:
                owned.stop_mirrored = previous.stop_mirrored
                owned.peak_memory_bytes = max(
                    owned.peak_memory_bytes, previous.peak_memory_bytes
                )
            self._service_claim_loop._owned[key] = owned
            self._hold_service_memory(str(owned.attempt_id), owned.memory_request_bytes)
        for route_id, claim in snapshot["platform_jobs"]:
            await self.bridge.adopt_platform_job(route_id, claim)
            if claim.id in self._platform_jobs:
                continue
            self.admission.hold(str(claim.id), claim.memory_required_bytes)
            self._platform_jobs.add(claim.id)
            asyncio.create_task(
                self._run_platform_job(claim), name=f"pool-platform-job-{claim.id}"
            )

    def _wait_for_adopted_agent_run(self, route: AgentRoute, request_bytes: int) -> None:
        """Hold an adopted child's memory and count it as a run until it ends."""
        assert self.admission is not None and self.bridge.agents is not None
        key = f"agent_run:{route.run_id}"
        self.admission.hold(key, request_bytes)

        async def wait() -> None:
            try:
                assert self.bridge.agents is not None
                await self.bridge.agents.supervise(route)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Adopted agent run failed", extra={"run_id": str(route.run_id)}
                )
            finally:
                assert self.admission is not None
                self.admission.release(key)

        task = asyncio.create_task(wait(), name=f"pool-adopted-agent-{route.run_id}")
        in_flight = self._tasks["agent.run"]
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)

    async def _drain(self) -> None:
        """Drain, then end the agent runs the drain grace did not outlast.

        The workflow pool terminates its children at the drain deadline with a
        recorded shutdown outcome. Agent children get the same treatment here,
        before ``_retire`` tells the launcher to stop (which kills them with no
        exit report). An owner that lost the launcher or is handing it over
        leaves its children alone: the next owner adopts or ends them.
        """
        from src.worker.remote.remote_pool import DRAIN_DEADLINE_ERROR, DRAIN_DEADLINE_ERROR_TYPE

        await super()._drain()
        agents = self.bridge.agents
        if (
            agents is None
            or not agents.routes
            or self._lost
            or self.bridge.handoff
            or not await owns_remote_session(self.bridge.lease)
        ):
            return
        await agents.terminate_active_runs(
            error_type=DRAIN_DEADLINE_ERROR_TYPE, error=DRAIN_DEADLINE_ERROR
        )

    async def _retire(self) -> None:
        """Only a drained owner can retire the physical launcher.

        An ownership loss leaves the launcher and its retained terminal
        results available for a fenced successor to adopt. Retiring its row
        here would strand those still-running durable claims.
        """
        if self._lost or self.bridge.handoff or not await owns_remote_session(self.bridge.lease):
            return
        await super()._retire()
        if self.bridge.connected and await owns_remote_session(self.bridge.lease):
            with contextlib.suppress(ConnectionError):
                await self.bridge.request("retired", {})


class RemoteSession:
    """A durable worker owner with replaceable socket transport."""

    def __init__(
        self, bridge: Bridge, worker: ApiExecutor, lease: RemoteOwnerLease, *, owned_until: float
    ) -> None:
        self.bridge = bridge
        self.worker = worker
        self.lease = lease
        # Monotonic time before which the database lease is known to be held.
        # Only an expired lease can pass to another owner.
        self.owned_until = owned_until
        self.lease_ref = LeaseRef(lease)
        bridge.lease_ref = self.lease_ref
        self.last_sequence = 0
        self.handoff = False
        self._lease_released = False
        self._release_lock = asyncio.Lock()
        self.cleanup_task: asyncio.Task[None] | None = None
        self.reconciliation_tasks: set[asyncio.Task[None]] = set()
        async def run_worker() -> int:
            with remote_owner_context(self.lease_ref):
                return await worker.run()
        self.task = asyncio.create_task(run_worker(), name=f"remote-worker:{lease.worker_id}")
        self.renewal = asyncio.create_task(self._renew(), name=f"remote-owner:{lease.worker_id}")
        self.task.add_done_callback(_observe_task)

    def begin_physical_drain(self) -> None:
        self.worker.request_stop()

    def begin_handoff(self) -> None:
        self.handoff = True
        self.bridge.handoff = True

    async def release_lease(self) -> None:
        """Release once, after either a drained retirement or a handoff."""
        async with self._release_lock:
            if self._lease_released:
                return
            self._lease_released = True
            await release_remote_owner(self.lease)

    def reconcile_transport_inventory(self, socket: WebSocket) -> asyncio.Task[None]:
        """Reconcile without blocking control replies needed by that work."""
        generation = self.lease.session_generation

        async def reconcile() -> None:
            try:
                if self.bridge.socket is not socket or self.lease.session_generation != generation:
                    return
                with remote_owner_context(self.lease_ref):
                    await self.worker.reconcile_transport_inventory(
                        expected_generation=generation
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Remote inventory reconciliation failed")
                if self.bridge.socket is socket and self.lease.session_generation == generation:
                    with contextlib.suppress(Exception):
                        await socket.close(code=1011, reason="inventory reconciliation failed")

        task = asyncio.create_task(reconcile(), name=f"remote-reconcile:{self.lease.worker_id}")
        self.reconciliation_tasks.add(task)
        task.add_done_callback(self.reconciliation_tasks.discard)
        return task

    async def _renew(self) -> None:
        try:
            while True:
                await asyncio.sleep(DEFAULT_OWNER_LEASE_SECONDS / 2)
                # Admission's three-second freshness window pauses new claims
                # during brief API event-loop delays. It must not also skip
                # ownership renewal: repeated short delays could otherwise
                # expire a healthy launcher's twenty-second owner lease.
                if (
                    not self.bridge.connected
                    or self.bridge.current < 0
                    or self.bridge.limit <= 0
                    or time.monotonic() - self.bridge.sample_at >= DEFAULT_OWNER_LEASE_SECONDS
                ):
                    # A WebSocket is only transport. Stop fresh claims as
                    # soon as telemetry is stale, but keep the database lease
                    # until its bounded expiry so a transient reconnect or a
                    # rolling API shutdown can hand the physical children to
                    # a fenced successor without manufacturing failures.
                    if time.monotonic() - self.bridge.sample_at < DEFAULT_OWNER_LEASE_SECONDS:
                        continue
                    await self.lose_ownership("Remote launcher telemetry expired")
                    return
                lease = self.lease
                renewing_at = time.monotonic()
                try:
                    renewed = await renew_remote_owner(lease)
                except Exception:
                    if self.lease != lease:
                        continue
                    logging.getLogger(__name__).exception("Remote owner renewal failed")
                    await self.lose_ownership("Remote API owner lease could not be renewed")
                    return
                if self.lease != lease:
                    continue
                if not renewed:
                    await self.lose_ownership("Remote API owner lease expired")
                    return
                self.owned_until = renewing_at + DEFAULT_OWNER_LEASE_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception("Remote owner renewal failed")
            await self.lose_ownership("Remote API owner lease could not be renewed")

    async def may_accept(self, kind: str, payload: dict[str, Any]) -> bool:
        """Whether this owner may act on a launcher message. Telemetry changes
        nothing durable, so a lease known to be current is enough; everything
        else is fenced in the database."""
        if kind == "event" and payload.get("event") == "sample" and time.monotonic() < self.owned_until:
            return True
        return await owns_remote_session(self.lease)

    async def lose_ownership(self, reason: str) -> None:
        self.owned_until = 0.0
        self.worker._lost = True
        if self.bridge.pool is not None:
            await self.bridge.pool.ownership_lost(reason)
        self.worker.request_stop()

    async def stop(self) -> None:
        """Drain a still-connected remote executor before releasing its fence."""
        self.worker.request_stop()
        await asyncio.gather(self.task, return_exceptions=True)
        self.renewal.cancel()
        await asyncio.gather(self.renewal, return_exceptions=True)
        for task in tuple(self.reconciliation_tasks):
            task.cancel()
        await asyncio.gather(*self.reconciliation_tasks, return_exceptions=True)
        await self.release_lease()

    async def detach(self) -> None:
        """Drop an API owner after transport closure without killing children."""
        self.begin_handoff()
        if self.bridge.agents is not None:
            self.bridge.agents.detach()
        await self.release_lease()
        self.renewal.cancel()
        await asyncio.gather(self.renewal, return_exceptions=True)
        for task in tuple(self.reconciliation_tasks):
            task.cancel()
        await asyncio.gather(*self.reconciliation_tasks, return_exceptions=True)
        self.worker.request_stop()
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class RemoteControlHost:
    """Per-API-replica remote control state; no cluster-wide singleton."""

    def __init__(self) -> None:
        self.owner_id = uuid4()
        self.sessions: dict[UUID, RemoteSession] = {}
        self.agent_executor: Any = None
        self.agent_task: asyncio.Task[Any] | None = None
        self.stopping = False

    async def start(self) -> None:
        """Start the single API-resident agent/summary executor for this replica."""
        if self.agent_task is not None:
            return
        from src.services.memory_requests import measure_budget, under_pressure
        from src.worker.admission import Admission
        from src.worker.api_agent import ApiAgentExecutor

        threshold = get_settings().memory_pressure_threshold
        budget = measure_budget(threshold)
        admission = Admission(
            budget_bytes=budget.budget_bytes,
            under_pressure=lambda: under_pressure(threshold),
            running_workflow_ids=set,
            workflow_growth=dict,
        )
        self.agent_executor = ApiAgentExecutor(admission)
        self.agent_task = asyncio.create_task(self.agent_executor.run(), name="api-agent-executor")
        self.agent_task.add_done_callback(_observe_task)

    async def stop(self) -> None:
        """Begin canonical drains before the lifespan closes control sockets."""
        self.stopping = True
        if self.agent_executor is not None:
            self.agent_executor.request_stop()
        await asyncio.gather(*(
            session.stop() if session.bridge.connected else session.detach()
            for session in self.sessions.values()
        ), return_exceptions=True)
        if self.agent_task is not None:
            await asyncio.gather(self.agent_task, return_exceptions=True)

    async def connect(self, socket: WebSocket) -> tuple[RemoteSession, FragmentAssembler]:
        await socket.accept()
        try:
            raw = await asyncio.wait_for(socket.receive_text(), timeout=30)
            initial = json.loads(raw)
            if not isinstance(initial, dict) or initial.get("event") != "hello":
                raise AuthenticationError("expected signed hello")
            signed = SignedHello.from_dict(initial.get("hello", {}))
            hello = verify_hello(get_settings().secret_key, signed)
            sample = initial.get("sample")
            if not isinstance(sample, dict):
                raise ProtocolError("hello sample is required")
            current, limit = int(sample["current"]), int(sample["limit"])
            pressure_ratio = sample.get("pressure_ratio")
            if pressure_ratio is None:
                pressure_ratio = current / limit if limit > 0 else 1.0
            bootstrap = {
                "current": current,
                "limit": limit,
                "pressure_ratio": float(pressure_ratio),
                "requirements": initial.get("requirements", {}),
                "hostname": hello.hostname,
            }
        except (AuthenticationError, ProtocolError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            await socket.close(code=1008)
            raise

        # Authentication precedes canonical admission. Reconnects naturally
        # get None here because their pod was already admitted.
        if hello.form == "job":
            assert hello.job_uid is not None and hello.pod_uid is not None
            # The launcher's own build, not this API's: an API admitting a pod
            # launched from an older image must leave it outdated.
            version = initial.get("version")
            admitted = await admit_worker(
                hello.incarnation_id, job_uid=hello.job_uid, pod_uid=hello.pod_uid,
                version=version if isinstance(version, str) else None,
            )
            if admitted is None:
                async with get_liveness_db_context() as db:
                    bound_pod = await db.scalar(
                        select(ExecutionWorker.id).where(
                            ExecutionWorker.id == hello.incarnation_id,
                            ExecutionWorker.form == "job",
                            ExecutionWorker.state.in_(("ready", "draining")),
                            ExecutionWorker.kubernetes_job_uid == hello.job_uid,
                            ExecutionWorker.kubernetes_pod_uid == hello.pod_uid,
                        )
                    )
                if bound_pod is None:
                    raise AuthenticationError("launcher pod is not the admitted Job pod")
        else:
            async with get_liveness_db_context() as db:
                existing_static = await db.scalar(
                    select(ExecutionWorker.id).where(
                        ExecutionWorker.id == hello.incarnation_id,
                        ExecutionWorker.form == "static",
                    )
                )
            if existing_static is None:
                await register_static_worker(
                    hostname=hello.hostname,
                    incarnation_id=hello.incarnation_id,
                    supported_handlers=list(REMOTE_WORKER_HANDLER_NAMES),
                )
        acquiring_at = time.monotonic()
        lease = await acquire_remote_owner(
            worker_id=hello.incarnation_id,
            owner_id=self.owner_id,
            launcher_id=hello.launcher_id,
            nonce_digest=nonce_digest(hello.nonce),
            hello_sequence=hello.hello_sequence,
        )
        if lease is None:
            await socket.close(code=1008)
            raise AuthenticationError("launcher ownership was refused")
        existing = self.sessions.get(lease.worker_id)
        if existing is not None:
            existing.lease = lease
            existing.lease_ref.lease = lease
            existing.owned_until = acquiring_at + DEFAULT_OWNER_LEASE_SECONDS
            existing.last_sequence = 0
            existing.bridge.replace_socket(socket, lease)
            await socket.send_text(json.dumps({"event": "welcome", "session_generation": lease.session_generation}))
            return existing, FragmentAssembler()
        bridge = Bridge(socket, bootstrap, lease)
        worker = ApiExecutor(bridge, lease.worker_id, remote_static=hello.form == "static")
        await socket.send_text(json.dumps({"event": "welcome", "session_generation": lease.session_generation}))
        session = RemoteSession(
            bridge, worker, lease, owned_until=acquiring_at + DEFAULT_OWNER_LEASE_SECONDS
        )
        self.sessions[lease.worker_id] = session
        session.task.add_done_callback(
            lambda task: self._session_finished(lease.worker_id, session, task)
        )
        return session, FragmentAssembler()

    def _session_finished(
        self, worker_id: UUID, session: RemoteSession, task: asyncio.Task[Any]
    ) -> None:
        if session.handoff:
            return

        async def cleanup() -> None:
            if self.sessions.get(worker_id) is not session:
                return
            self.sessions.pop(worker_id, None)
            session.renewal.cancel()
            await asyncio.gather(session.renewal, return_exceptions=True)
            for reconciliation in tuple(session.reconciliation_tasks):
                reconciliation.cancel()
            await asyncio.gather(*session.reconciliation_tasks, return_exceptions=True)
            await session.release_lease()

        session.cleanup_task = asyncio.create_task(cleanup())


_host: RemoteControlHost | None = None


async def start_remote_control_host(_application: Any) -> None:
    """Lifespan hook; the caller mounts :data:`router` before ``/`` first."""
    global _host
    if _host is None:
        _host = RemoteControlHost()
        await _host.start()


async def stop_remote_control_host() -> None:
    global _host
    if _host is not None:
        await _host.stop()
        _host = None


@router.websocket("/api/worker-control")
async def remote_control(socket: WebSocket) -> None:
    if _host is None or _host.stopping:
        await socket.close(code=1013)
        return
    session: RemoteSession | None = None
    try:
        session, assembler = await _host.connect(socket)
        while True:
            frame = ControlEnvelope.from_json(await socket.receive_text())
            if session.bridge.socket is not socket:
                raise ProtocolError("superseded remote control socket")
            if frame.session_generation != session.lease.session_generation:
                raise ProtocolError("stale remote control session")
            if frame.sequence <= session.last_sequence:
                raise ProtocolError("stale remote control message")
            session.last_sequence = frame.sequence
            logical = assembler.feed(frame)
            if logical is None:
                continue
            if not await session.may_accept(logical.kind, logical.payload):
                await session.lose_ownership("Remote API owner fence was replaced")
                raise ProtocolError("remote API owner fence was replaced")
            if logical.kind != "event":
                raise ProtocolError("launcher may only send events")
            await session.bridge.receive(logical.payload)
            if logical.payload.get("event") == "inventory":
                session.reconcile_transport_inventory(socket)
            elif logical.payload.get("event") == "draining":
                session.begin_physical_drain()
    except WebSocketDisconnect:
        # A transport disconnect does not kill children or release claims.
        pass
    except (AuthenticationError, ProtocolError):
        if socket.application_state is not WebSocketState.DISCONNECTED:
            await socket.close(code=1008)
    finally:
        if session is not None:
            session.bridge.disconnect(socket)
