"""Reconnect-aware ProcessPoolManager adapter for a remote launcher.

The API process remains the canonical execution owner: it keeps the Redis
leases, completion callbacks, cancellation subscription, and service claim
loop.  The launcher owns every OS process and its cgroup, so this adapter
never reads or signals a local PID.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select

from shared.execution_context import validate_execution_context
from src.config import get_settings
from src.core.cache.keys import active_execution_key
from src.core.redis_client import ActiveExecution
from src.services.execution.process_pool import (
    DANGEROUS_MEMORY_RATIO,
    ExecutionInfo,
    ProcessHandle,
    ProcessPoolManager,
    ProcessState,
    ServiceInfo,
)

logger = logging.getLogger(__name__)

# Why a child still running when the drain grace runs out was stopped. Agent
# children end their runs with the same reason (``RemoteAgentConsumer``).
DRAIN_DEADLINE_ERROR_TYPE = "WorkerShutdown"
DRAIN_DEADLINE_ERROR = "Remote pool drain deadline exceeded"


class ThinRemoteTransport(Protocol):
    """Control channel supplied by the API-side thin-runner host."""

    connected: bool
    hostname: str

    async def request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send an acknowledged physical-runner command."""
        raise NotImplementedError

    def memory(self) -> tuple[int, int]:
        """Return the physical container's current usage and limit."""
        raise NotImplementedError

    def memory_request_bytes(self, key: str) -> int | None:
        """Return the canonical reservation for a running route."""
        raise NotImplementedError


class _RemoteProcess:
    """PID-less ProcessHandle shim; remote liveness arrives as launcher events."""

    def __init__(self) -> None:
        self.exitcode: int | None = None
        self.remote_pid: int | None = None

    def is_alive(self) -> bool:
        return self.exitcode is None


class ThinRemoteProcessPool(ProcessPoolManager):
    """Use canonical pool bookkeeping while dispatching OS work remotely.

    A dropped control socket only pauses new work. Existing OS children stay
    alive in the physical launcher and are reconciled after its signed
    reconnect. ``ownership_lost`` is the separate, fenced terminal path.
    """

    def __init__(
        self,
        transport: ThinRemoteTransport,
        *,
        worker_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        identity = worker_id or (
            f"thin-remote:{os.environ.get('HOSTNAME', 'api')}"
        )
        super().__init__(worker_id=identity, **kwargs)
        self.transport = transport
        self._routes: dict[str, ProcessHandle] = {}
        self._route_metrics: dict[str, dict[str, float | int]] = {}
        self._remote_memory_current = -1
        self._remote_memory_limit = -1
        self._lease_refresh_task: asyncio.Task[None] | None = None
        self._result_locks: dict[str, asyncio.Lock] = {}

    async def reconcile_inventory(self, inventory: dict[str, Any]) -> dict[str, Any]:
        """Reattach physical children only to their still-authoritative claims.

        The launcher journals compact transport metadata until terminal ACK.
        PostgreSQL validates that identity; it never trusts inventory as a new
        claim. Finished but unacknowledged routes need the same reconstruction
        as live children so completion can be replayed after an API restart.
        """
        from src.core.database import get_db_context
        from src.jobs.platform.catalog import get_platform_job_metadata
        from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob
        from src.models.enums import ExecutionStatus
        from src.models.orm.agent_runs import AgentRun
        from src.models.orm.execution_workers import ExecutionWorkerClaim
        from src.models.orm.executions import Execution
        from src.models.orm.platform_jobs import PlatformJob
        from src.models.orm.services import ServiceAttempt, ServiceDefinition
        from src.services.service_claim import OwnedAttempt
        from src.worker.remote.fencing import fence_remote_owner

        if inventory.get("version") != 1:
            raise ValueError("Unsupported launcher inventory")
        entries = inventory.get("routes", []) + inventory.get("terminal_routes", [])
        if not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("Invalid launcher inventory")
        workflows: dict[str, int] = {}
        services: list[OwnedAttempt] = []
        jobs: list[tuple[str, ClaimedPlatformJob]] = []
        agent_runs: dict[str, tuple[UUID, int]] = {}
        observed: set[str] = set()
        rejected: list[tuple[str, str]] = []
        async with get_db_context() as db:
            if not await fence_remote_owner(db):
                raise ConnectionError("Remote API ownership was replaced")
            for entry in entries:
                route_id, kind = entry.get("route_id"), entry.get("kind")
                if not isinstance(route_id, str) or route_id in observed:
                    continue
                observed.add(route_id)
                subject = UUID(str(entry["subject_id"]))
                request_bytes = entry.get("memory_request_bytes")
                if isinstance(request_bytes, bool) or not isinstance(request_bytes, int) or request_bytes < 0:
                    raise ValueError("Launcher route is missing its memory reservation")
                if kind == "workflow":
                    valid = await db.scalar(select(Execution.id).join(
                        ExecutionWorkerClaim,
                        ExecutionWorkerClaim.subject_id == Execution.id,
                    ).where(
                        Execution.id == subject,
                        Execution.status.in_((ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING)),
                        ExecutionWorkerClaim.worker_id == UUID(self.worker_id),
                        ExecutionWorkerClaim.family == "workflow_execution",
                    ))
                    if valid is None:
                        rejected.append((route_id, kind))
                        continue
                    active = entry.get("active_execution")
                    if not isinstance(active, dict) or active.get("execution_id") != str(subject):
                        raise ValueError("Launcher workflow metadata is missing or mismatched")
                    started_at = datetime.fromisoformat(entry["started_at"])
                    execution = ExecutionInfo(
                        execution_id=str(subject), started_at=started_at,
                        timeout_seconds=int(entry["timeout_seconds"]),
                        active_execution=cast(ActiveExecution, active),
                    )
                    handle = self._routes.get(route_id)
                    if handle is None:
                        handle = self._adopt_handle(route_id, entry, started_at)
                        handle.current_execution = execution
                        self.processes[route_id] = handle
                    await self._write_active_execution_lease(execution)
                    workflows[str(subject)] = request_bytes
                elif kind == "service":
                    identity = entry.get("service") or {}
                    row = (await db.execute(select(ServiceAttempt, ServiceDefinition).join(
                        ServiceDefinition, ServiceDefinition.id == ServiceAttempt.service_id,
                    ).where(
                        ServiceAttempt.id == subject,
                        ServiceAttempt.worker_id == self.worker_id,
                        ServiceAttempt.lease_token == identity.get("lease_token"),
                        ServiceAttempt.state.in_(("starting", "running", "stopping")),
                        ServiceAttempt.lease_expires_at > func.now(),
                    ))).one_or_none()
                    if row is None:
                        rejected.append((route_id, kind))
                        continue
                    attempt, definition = row
                    if identity.get("service_id") != str(definition.id):
                        raise ValueError("Launcher service identity does not match its claim")
                    handle = self._routes.get(route_id)
                    if handle is None:
                        handle = self._adopt_handle(
                            route_id, entry, attempt.started_at or datetime.now(timezone.utc),
                        )
                        handle.service = ServiceInfo(
                            service_id=str(definition.id), attempt_id=str(subject),
                            lease_token=attempt.lease_token,
                            graceful_shutdown_seconds=definition.graceful_shutdown_seconds,
                        )
                        self.service_processes[route_id] = handle
                    services.append(OwnedAttempt(
                        attempt_id=subject, service_id=definition.id, lease_token=attempt.lease_token,
                        workflow_id=definition.workflow_id, memory_request_bytes=request_bytes,
                    ))
                elif kind == "platform_job":
                    job = await db.scalar(select(PlatformJob).where(
                        PlatformJob.id == subject,
                        PlatformJob.lease_token == UUID(str(entry["lease_token"])),
                        PlatformJob.status.in_(("running", "cancel_requested")),
                        PlatformJob.lease_expires_at > func.now(),
                    ))
                    metadata = get_platform_job_metadata(job.job_type) if job is not None else None
                    if job is None or metadata is None:
                        rejected.append((route_id, kind))
                        continue
                    jobs.append((route_id, ClaimedPlatformJob(
                        id=subject, lease_token=UUID(str(job.lease_token)),
                        timeout_seconds=job.timeout_seconds,
                        hard_memory_ratio=metadata.policy.hard_memory_ratio,
                        memory_required_bytes=job.memory_required_bytes, job_type=job.job_type,
                    )))
                elif kind == "agent_run":
                    # A cancelling run's child is still winding down; adopt
                    # it so its exit settles the run.
                    valid = await db.scalar(select(AgentRun.id).join(
                        ExecutionWorkerClaim,
                        ExecutionWorkerClaim.subject_id == AgentRun.id,
                    ).where(
                        AgentRun.id == subject,
                        AgentRun.status.in_(("queued", "running", "cancelling")),
                        ExecutionWorkerClaim.worker_id == UUID(self.worker_id),
                        ExecutionWorkerClaim.family == "agent_run",
                    ))
                    if valid is None:
                        rejected.append((route_id, kind))
                        continue
                    agent_runs[route_id] = (subject, request_bytes)
                else:
                    raise ValueError("Unknown launcher route kind")
            await db.commit()
        for route_id, kind in rejected:
            await self.transport.request(
                "terminate_job" if kind == "platform_job" else "terminate",
                {"route_id": route_id, "error_type": "OwnerLost", "error": "Claim no longer belongs to this launcher"},
            )
        # An uncertain launch that never reached the launcher cannot remain
        # RUNNING forever. A present terminal journal is observed above too.
        for route_id, handle in list(self._routes.items()):
            if route_id not in observed:
                await self._handle_result(handle, {
                    "type": "result", "success": False,
                    "execution_id": handle.current_execution.execution_id if handle.current_execution else None,
                    "error_type": "ProcessCrashError", "error": "Launcher no longer owns this process",
                })
        return {
            "workflow_requests": workflows,
            "services": services,
            "platform_jobs": jobs,
            "agent_runs": agent_runs,
        }

    def _adopt_handle(self, route_id: str, entry: dict[str, Any], started_at: datetime) -> ProcessHandle:
        handle = ProcessHandle(
            id=route_id, process=_RemoteProcess(), pid=None, state=ProcessState.BUSY,
            work_queue=None, result_queue=None, started_at=started_at,
        )
        pid = entry.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            handle.process.remote_pid = pid
        self._routes[route_id] = handle
        return handle

    def _workflow_route_id(self, execution_id: str) -> str:
        return f"workflow:{execution_id}:{uuid4()}"

    def _service_route_id(self, attempt_id: str) -> str:
        return f"service:{attempt_id}:{uuid4()}"

    def _workflow_route_for(self, execution_id: str) -> str | None:
        return next(
            (
                route_id
                for route_id, handle in self._routes.items()
                if handle.current_execution is not None
                and handle.current_execution.execution_id == execution_id
            ),
            None,
        )

    def _service_route_for(self, attempt_id: str) -> str | None:
        return next(
            (
                route_id
                for route_id, handle in self._routes.items()
                if handle.service is not None
                and handle.service.attempt_id == attempt_id
            ),
            None,
        )

    def _read_remote_memory(self) -> tuple[int, int]:
        current, limit = self.transport.memory()
        self._remote_memory_current = int(current)
        self._remote_memory_limit = int(limit)
        return self._remote_memory_current, self._remote_memory_limit

    def _ensure_remote_capacity(self) -> None:
        current, limit = self._read_remote_memory()
        if current < 0 or limit <= 0:
            raise MemoryError("Remote launcher cgroup memory is unavailable")
        threshold = get_settings().memory_pressure_threshold
        if current >= limit * threshold:
            raise MemoryError(
                "Remote launcher memory pressure exceeds "
                f"{threshold:.0%} threshold"
            )

    async def start(self) -> None:
        """Register the API owner and start the canonical Redis lifecycle."""
        if self._started:
            return
        if not self.transport.connected:
            raise RuntimeError("Thin remote launcher is not connected")
        await self.transport.request("configure", {
            "pressure_threshold": get_settings().memory_pressure_threshold,
            "dangerous_memory_ratio": DANGEROUS_MEMORY_RATIO,
        })

        self._started = True
        self._shutdown = False
        self._started_at = datetime.now(timezone.utc)
        self._last_active_execution_refresh = time.monotonic()
        self._read_remote_memory()
        await self._register_worker()
        redis_client = await self._get_redis()
        await cast(
            Awaitable[int],
            redis_client.hset(
                f"bifrost:pool:{self.worker_id}",
                mapping={
                    "runtime": "thin_remote",
                    "execution_owner": "remote_launcher",
                    "api_owner": os.environ.get("HOSTNAME", "unknown"),
                },
            ),
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="thin-remote-pool-heartbeat"
        )
        self._lease_refresh_task = asyncio.create_task(
            self._lease_refresh_loop(), name="thin-remote-pool-lease-refresh"
        )
        self._cancel_task = asyncio.create_task(
            self._cancel_listener_loop(), name="thin-remote-pool-cancel"
        )
        self._command_task = asyncio.create_task(
            self._command_listener_loop(), name="thin-remote-pool-command"
        )

    async def _remote_packages(self) -> list[dict[str, Any]]:
        response = await self.transport.request(
            "package_tool", {"operation": "inventory"}
        )
        packages = response.get("packages")
        if not isinstance(packages, list):
            raise RuntimeError("Remote package inventory response is invalid")
        return [package for package in packages if isinstance(package, dict)]

    async def _register_worker(self) -> None:
        """Register canonical pool fields using the launcher's package set."""
        redis_client = await self._get_redis()
        redis_key = f"bifrost:pool:{self.worker_id}"
        packages = await self._remote_packages()
        await cast(
            Awaitable[int],
            redis_client.hset(
                redis_key,
                mapping={
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "status": "online",
                    "hostname": self.transport.hostname,
                    "packages": json.dumps(packages),
                },
            ),
        )
        await redis_client.expire(redis_key, self.registration_ttl_seconds)

    async def update_packages(self) -> None:
        """Refresh Redis from the package owner after a remote install."""
        if not self._started:
            return
        try:
            redis_client = await self._get_redis()
            packages = await self._remote_packages()
            await cast(
                Awaitable[int],
                redis_client.hset(
                    f"bifrost:pool:{self.worker_id}",
                    "packages",
                    json.dumps(packages),
                ),
            )
        except Exception:
            logger.exception("Could not update thin remote package inventory")

    async def stop(self) -> None:
        """Stop accepting work and ask the launcher to terminate owned routes."""
        if not self._started:
            return
        self._shutdown = True
        tasks = [
            self._heartbeat_task,
            self._lease_refresh_task,
            self._cancel_task,
            self._command_task,
        ]
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # Expected after cancelling this owned background task.

        owns_session = await self._owns_session()
        if owns_session and self.transport.connected:
            await asyncio.gather(
                *(
                    self._request_terminate(
                        route_id,
                        error_type="WorkerShutdown",
                        error="Remote pool is stopping",
                        grace_seconds=(
                            handle.service.graceful_shutdown_seconds
                            if handle.service is not None
                            else self.graceful_shutdown_seconds
                        ),
                    )
                    for route_id, handle in list(self._routes.items())
                ),
                return_exceptions=True,
            )
        if owns_session:
            await self._unregister_worker()
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        self._started = False

    async def ownership_lost(self, reason: str = "Remote owner lease lost") -> None:
        """Detach a replaced owner without mutating the surviving children."""
        logger.info("Remote pool detaching: %s", reason)
        self._shutdown = True
        self.processes.clear()
        self.service_processes.clear()
        self._routes.clear()
        self._route_metrics.clear()

    async def _owns_session(self) -> bool:
        from src.worker.remote.fencing import current_remote_lease_ref
        from src.worker.remote.ownership import owns_remote_session

        ref = current_remote_lease_ref()
        return ref is None or await owns_remote_session(ref.lease)

    def _request_bytes(self, key: str) -> int:
        return self.transport.memory_request_bytes(key) or 0

    async def route_execution(
        self,
        execution_id: str,
        context: dict[str, Any],
        active_execution: ActiveExecution,
    ) -> None:
        """Write canonical API ownership records, then await launcher ACK."""
        validate_execution_context(context)
        await self._write_context_to_redis(execution_id, context)
        self._ensure_remote_capacity()
        route_id = self._workflow_route_id(execution_id)
        timeout = int(context.get("timeout_seconds", self.execution_timeout_seconds))

        async with self._restart_lock:
            if route_id in self._routes:
                raise RuntimeError(f"Remote route already exists: {route_id}")
            started_at = datetime.now(timezone.utc)
            context["workflow_deadline"] = (
                (started_at + timedelta(seconds=timeout)).isoformat() if timeout > 0 else None
            )
            execution = ExecutionInfo(
                execution_id=execution_id,
                started_at=started_at,
                timeout_seconds=timeout,
                active_execution=active_execution,
            )
            handle = ProcessHandle(
                id=route_id,
                process=_RemoteProcess(),
                pid=None,
                state=ProcessState.BUSY,
                work_queue=None,
                result_queue=None,
                started_at=started_at,
                current_execution=execution,
            )
            self.processes[handle.id] = handle
            self._routes[route_id] = handle
            try:
                await self._write_active_execution_lease(execution)
            except Exception as exc:  # noqa: BLE001 - dispatch keeps its durable context
                logger.warning(
                    "Could not write active execution lease for %s: %s",
                    execution_id,
                    exc,
                )
            try:
                response = await self.transport.request(
                    "workflow",
                    {
                        "route_id": route_id,
                        "execution_id": execution_id,
                        "context": context,
                        "memory_kill_growth_bytes": self.memory_kill_growth_bytes,
                        "active_execution": dict(active_execution),
                        "started_at": started_at.isoformat(),
                        "timeout_seconds": timeout,
                        "memory_request_bytes": self._request_bytes(execution_id),
                    },
                )
                handle.process.remote_pid = self._remote_pid_from_ack(response)
            except (ConnectionError, TimeoutError):
                # The child may already exist. Inventory resolves this exact
                # route after reconnect; reporting failure now could duplicate
                # side effects while its original process continues.
                logger.warning("Remote workflow launch ACK uncertain: %s", route_id)
            except Exception:
                self.processes.pop(handle.id, None)
                self._routes.pop(route_id, None)
                self._route_metrics.pop(route_id, None)
                try:
                    redis_client = await self._get_redis()
                    await redis_client.delete(
                        f"bifrost:exec:{execution_id}:context",
                        active_execution_key(execution_id),
                    )
                except Exception:
                    logger.warning("Could not clean failed remote dispatch %s", execution_id)
                raise

    async def route_service(
        self,
        *,
        service_id: str,
        attempt_id: str,
        lease_token: str,
        context: dict[str, Any],
        graceful_shutdown_seconds: int = 30,
    ) -> None:
        """Dispatch a canonical claimed service attempt to the launcher."""
        validate_execution_context(context)
        self._ensure_remote_capacity()
        route_id = self._service_route_id(attempt_id)
        async with self._restart_lock:
            if route_id in self._routes:
                raise RuntimeError(f"Remote route already exists: {route_id}")
            handle = ProcessHandle(
                id=route_id,
                process=_RemoteProcess(),
                pid=None,
                state=ProcessState.BUSY,
                work_queue=None,
                result_queue=None,
                started_at=datetime.now(timezone.utc),
                service=ServiceInfo(
                    service_id=service_id,
                    attempt_id=attempt_id,
                    lease_token=lease_token,
                    graceful_shutdown_seconds=graceful_shutdown_seconds,
                ),
            )
            self.service_processes[handle.id] = handle
            self._routes[route_id] = handle
            try:
                response = await self.transport.request(
                    "service",
                    {
                        "route_id": route_id,
                        "execution_id": attempt_id,
                        "context": context,
                        "graceful_shutdown_seconds": graceful_shutdown_seconds,
                        "memory_request_bytes": self._request_bytes("service:" + attempt_id),
                        "service": {
                            "service_id": service_id,
                            "attempt_id": attempt_id,
                            "lease_token": lease_token,
                        },
                    },
                )
                handle.process.remote_pid = self._remote_pid_from_ack(response)
            except (ConnectionError, TimeoutError):
                logger.warning("Remote service launch ACK uncertain: %s", route_id)
            except Exception:
                self.service_processes.pop(handle.id, None)
                self._routes.pop(route_id, None)
                self._route_metrics.pop(route_id, None)
                raise

    @staticmethod
    def _remote_pid_from_ack(response: dict[str, Any]) -> int:
        pid = response.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise RuntimeError("Remote launcher ACK is missing a valid PID")
        return pid

    async def _request_terminate(
        self,
        route_id: str,
        *,
        error_type: str,
        error: str,
        grace_seconds: float,
    ) -> None:
        handle = self._routes.get(route_id)
        if handle is None:
            return
        handle.state = ProcessState.KILLED
        handle.killed_at = datetime.now(timezone.utc)
        await self.transport.request(
            "terminate",
            {
                "route_id": route_id,
                "error_type": error_type,
                "error": error,
                "grace_seconds": grace_seconds,
            },
        )

    async def _handle_cancel_request(self, execution_id: str) -> None:
        route_id = self._workflow_route_for(execution_id)
        if route_id is None:
            return
        await self._request_terminate(
            route_id,
            error_type="CancelledError",
            error="Execution was cancelled",
            grace_seconds=self.graceful_shutdown_seconds,
        )

    async def stop_service_child(self, attempt_id: str) -> bool:
        route_id = self._service_route_for(attempt_id)
        if route_id is None:
            return False
        handle = self._routes.get(route_id)
        if handle is None or handle.service is None:
            return False
        await self._request_terminate(
            route_id,
            error_type="CancelledError",
            error="Service stop requested",
            grace_seconds=handle.service.graceful_shutdown_seconds,
        )
        return True

    async def drain_active_executions(self, drain_timeout: float) -> bool:
        deadline = time.monotonic() + drain_timeout
        while self.active_execution_count() > 0:
            if time.monotonic() >= deadline:
                await asyncio.gather(
                    *(
                        self._request_terminate(
                            route_id,
                            error_type=DRAIN_DEADLINE_ERROR_TYPE,
                            error=DRAIN_DEADLINE_ERROR,
                            grace_seconds=self.graceful_shutdown_seconds,
                        )
                        for route_id, handle in list(self._routes.items())
                        if handle.current_execution is not None
                    ),
                    return_exceptions=True,
                )
                return False
            await asyncio.sleep(0.05)
        return True

    async def drain_and_restart_template(self, drain_timeout: float = 60.0) -> None:
        """Drain remote workflows, retire services, then recycle remotely."""
        async with self._restart_lock:
            await self.drain_active_executions(drain_timeout)
            terminate_services = []
            for route_id, handle in list(self._routes.items()):
                if handle.service is None:
                    continue
                handle.service.recycled = True
                handle.service.recycle_reason = "template_recycle"
                terminate_services.append(
                    self._request_terminate(
                        route_id,
                        error_type="WorkerShutdown",
                        error="Remote template recycle",
                        grace_seconds=handle.service.graceful_shutdown_seconds,
                    )
                )
            await asyncio.gather(*terminate_services)
            await self.transport.request("recycle", {})

    async def _recycle_via_drain(self, reason: str) -> None:
        await self.drain_and_restart_template()
        logger.info("Thin remote pool recycled (%s)", reason)

    async def _lease_refresh_loop(self) -> None:
        """Refresh durable run leases even though this pool has no OS monitor."""
        while not self._shutdown:
            try:
                await self._refresh_active_execution_leases_if_due(time.monotonic())
            except Exception:
                logger.exception("Thin remote active-execution lease refresh failed")
            await asyncio.sleep(min(self.heartbeat_interval_seconds, 30.0))

    async def _heartbeat_loop(self) -> None:
        """Publish remote ownership without sampling the API host cgroup."""
        while not self._shutdown:
            try:
                await self._refresh_registration()
                await self._publish_heartbeat(self._build_heartbeat())
            except Exception:
                logger.exception("Thin remote pool heartbeat failed")
            await asyncio.sleep(self.heartbeat_interval_seconds)

    def workflow_growth(self) -> dict[str, int]:
        return {
            handle.current_execution.execution_id: int(
                self._route_metrics.get(route_id, {}).get("growth_bytes", 0)
            )
            for route_id, handle in self._routes.items()
            if handle.current_execution is not None
        }

    def get_service_memory_mb(self, attempt_id: str) -> float | None:
        route_id = self._service_route_for(attempt_id)
        if route_id is None:
            return None
        rss = self._route_metrics.get(route_id, {}).get("rss_bytes")
        return float(rss) / 1024 / 1024 if rss is not None else None

    def _attach_resource_peaks(self, handle: ProcessHandle, result: dict[str, Any]) -> None:
        metrics = self._route_metrics.get(handle.id)
        if not metrics:
            return
        result_metrics = result.setdefault("metrics", {})
        if not isinstance(result_metrics, dict):
            return
        for source, target in (
            ("growth_bytes", "peak_memory_growth_bytes"),
            ("rss_bytes", "peak_process_rss_bytes"),
            ("cpu_seconds", "cpu_total_seconds"),
            ("peak_cpu_cores", "peak_cpu_cores"),
        ):
            value = metrics.get(source)
            if value is not None:
                result_metrics[target] = max(result_metrics.get(target) or 0, value)

    async def handle_event(self, event: dict[str, Any]) -> None:
        """Accept launcher events after their control-channel authentication."""
        event_type = event.get("event")
        if event_type == "sample":
            self._remote_memory_current = int(event.get("current", -1))
            self._remote_memory_limit = int(event.get("limit", -1))
            runs = event.get("runs", {})
            if not isinstance(runs, dict):
                return
            for route_id, sample in runs.items():
                if route_id not in self._routes or not isinstance(sample, dict):
                    continue
                metrics = self._route_metrics.setdefault(route_id, {})
                for key in ("growth_bytes", "memory_peak_bytes", "cpu_seconds", "rss_bytes", "peak_cpu_cores"):
                    value = sample.get(key)
                    if isinstance(value, (int, float)):
                        metrics[key] = max(metrics.get(key, 0), value)
            return
        if event_type != "result":
            logger.debug("Ignoring thin remote launcher event %r", event_type)
            return
        route_id = event.get("route_id")
        result = event.get("result")
        if not isinstance(route_id, str) or not isinstance(result, dict):
            logger.warning("Ignoring malformed thin remote result event")
            return
        handle = self._routes.get(route_id)
        if handle is None:
            logger.info("Ignoring late thin remote result for %s", route_id)
            return
        await self._handle_result(handle, result)

    async def _handle_result(self, handle: ProcessHandle, result: dict[str, Any]) -> None:
        """Release a remote route only after its canonical callback succeeds.

        Local pipe delivery may consume a result once; a reconnecting remote
        launcher retains it until ACK. Keep the handle and propagate callback
        failures so that transport replay can safely try the durable commit.
        """
        async with self._result_locks.setdefault(handle.id, asyncio.Lock()):
            if handle.id not in self._routes:
                return
            if not await self._owns_session():
                raise ConnectionError("Remote API ownership was replaced")
            if handle.service is not None:
                if self.on_service_result is not None:
                    await self.on_service_result(self._service_envelope(handle, result))
            elif handle.current_execution is not None:
                result = handle.current_execution.attach_transport_metadata(result)
                self._attach_resource_peaks(handle, result)
                if self.on_result is not None:
                    await self.on_result(result)
            if not await self._owns_session():
                raise ConnectionError("Remote API ownership was replaced before ACK")
            handle.result_reported = True
            handle.process.exitcode = 0
            handle.current_execution = None
            self.processes.pop(handle.id, None)
            self.service_processes.pop(handle.id, None)
            self._routes.pop(handle.id, None)
            self._route_metrics.pop(handle.id, None)
            self._result_locks.pop(handle.id, None)

    def _build_heartbeat(self) -> dict[str, Any]:
        """Publish remote cgroup usage without inspecting the API host."""
        processes = [
            {
                "pid": handle.process.remote_pid,
                "process_id": handle.id,
                "state": handle.state.value,
                "memory_mb": float(self._route_metrics.get(handle.id, {}).get("rss_bytes", 0)) / 2**20,
                "private_dirty_kb": -1,
                "uptime_seconds": handle.uptime_seconds,
                "executions_completed": handle.executions_completed,
                "execution": {
                    "execution_id": handle.current_execution.execution_id,
                    "started_at": handle.current_execution.started_at.isoformat(),
                    "elapsed_seconds": handle.current_execution.elapsed_seconds,
                },
            }
            for handle in self.processes.values()
            if handle.current_execution is not None and handle.process.remote_pid is not None
        ]
        services = [
            {
                "pid": handle.process.remote_pid,
                "process_id": handle.id,
                "state": handle.state.value,
                "memory_mb": self.get_service_memory_mb(handle.service.attempt_id) or 0.0,
                "private_dirty_kb": -1,
                "uptime_seconds": handle.uptime_seconds,
                "service": {
                    "service_id": handle.service.service_id,
                    "attempt_id": handle.service.attempt_id,
                },
            }
            for handle in self.service_processes.values()
            if handle.service is not None and handle.process.remote_pid is not None
        ]
        return {
            "type": "worker_heartbeat",
            "worker_id": self.worker_id,
            "hostname": self.transport.hostname,
            "status": "online",
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processes": processes,
            "pool_size": len(processes),
            "idle_count": 0,
            "busy_count": len(processes),
            "service_count": len(services),
            "service_children": services,
            "requirements_installed": self._requirements_installed,
            "requirements_total": self._requirements_total,
            "memory_current_bytes": self._remote_memory_current,
            "memory_max_bytes": self._remote_memory_limit,
        }
