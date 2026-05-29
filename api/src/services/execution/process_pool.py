"""
Process Pool Manager for Execution Isolation.

Each execution forks a fresh worker process from a long-lived template
and the worker exits after returning its result. There is no warm pool;
the only throttles are `max_workers` (concurrency cap) and a cgroup
memory-pressure check on the way in.

Key features:
- One-shot worker processes (fork → run one execution → exit)
- Cap concurrent forks at `max_workers`; queued executions wait on a
  condition variable that is notified when a worker exits
- Memory-pressure admission control (cgroup working-set)
- Automatic timeout handling with graceful shutdown (SIGTERM -> SIGKILL)
- Crash detection
- Heartbeat publishing for UI visibility
- Drain + template restart after pip install (so future forks pick up
  newly installed packages); also exposed as a manual "recycle" RPC

Architecture:
    ProcessPoolManager (runs in consumer process)
        |
        +-- up to max_workers concurrent one-shot children
        +-- Each child: work_queue (in) + result_queue (out)
        +-- Monitor loop checks health and timeouts
        +-- Result loop collects execution results
        +-- Heartbeat loop publishes status to Redis/WebSocket
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from queue import Empty
from typing import Any, Awaitable, Callable

import psutil
import redis.asyncio as redis

from src.config import get_settings
from src.services.execution.memory_monitor import get_cgroup_memory, has_sufficient_memory_cgroup
from src.models.contracts.notifications import NotificationCategory, NotificationCreate, NotificationStatus
from src.services.execution.simple_worker import install_requirements, RequirementsInstallResult
from src.services.notification_service import get_notification_service
from src.services.execution.template_process import TemplateProcess

logger = logging.getLogger(__name__)


async def _notify_requirements_failures(result: RequirementsInstallResult) -> None:
    """Publish a deduped admin notification when requirements failed to install.

    No-op when the install fully succeeded. Best-effort: notification errors
    are logged, never raised, so install/recycle is never blocked by it.
    """
    if result.ok:
        return

    names = [f.package for f in result.failed]
    shown = ", ".join(names[:5])
    if len(names) > 5:
        shown += f" and {len(names) - 5} more"
    title = "Workflow package install failed"
    description = (
        f"{len(result.failed)} package(s) failed to install on workers: "
        f"{shown}. Workflows importing them will fail until fixed."
    )
    try:
        service = get_notification_service()
        # Dedup is best-effort: concurrent workers can race past this check and
        # create duplicate notifications. The failure case is rare and duplicates
        # are cheap to dismiss, so no distributed lock is warranted.
        existing = await service.find_admin_notification_by_title(
            title, NotificationCategory.PACKAGE_INSTALL
        )
        if existing is not None:
            logger.info("[pool] requirements-failure notification already exists; skipping")
            return
        await service.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.PACKAGE_INSTALL,
                title=title,
                description=description[:500],
                metadata={
                    "failed": [
                        {"package": f.package, "error": f.error[:500]}
                        for f in result.failed
                    ],
                    "installed": result.installed,
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.FAILED,
        )
        logger.info(f"[pool] Notified admins of requirements install failures: {shown}")
    except Exception as e:  # noqa: BLE001 - notification must never block the pool
        logger.warning(f"[pool] Could not publish requirements-failure notification: {e}")


def _get_installed_packages() -> list[dict[str, str]]:
    """
    Get list of installed packages via pip list.

    Returns a list of dicts with 'name' and 'version' keys.
    Used to populate the packages field in Redis pool registration.
    """
    try:
        result = subprocess.run(
            ["pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"Failed to get installed packages: {e}")
    return []


class ProcessState(Enum):
    """
    State of a worker process in the pool.

    Workers are one-shot — they only ever exist in BUSY (running their
    single execution) or KILLED (terminating). IDLE is kept as a
    no-longer-used value purely for any external consumer that may parse
    the heartbeat shape; nothing in this module emits it.
    """

    IDLE = "idle"  # deprecated; unused since on-demand-only refactor
    BUSY = "busy"
    KILLED = "killed"


@dataclass
class ExecutionInfo:
    """
    Information about a currently running execution.

    Attributes:
        execution_id: Unique identifier for the execution
        started_at: When the execution started
        timeout_seconds: Execution timeout in seconds
    """

    execution_id: str
    started_at: datetime
    timeout_seconds: int

    @property
    def elapsed_seconds(self) -> float:
        """Seconds since execution started."""
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()

    @property
    def is_timed_out(self) -> bool:
        """Check if execution has exceeded its timeout. 0 = no timeout."""
        return self.timeout_seconds > 0 and self.elapsed_seconds > self.timeout_seconds


@dataclass
class ProcessHandle:
    """
    Represents a worker process managed by the pool.

    Attributes:
        id: Unique identifier for this process handle (e.g., "process-1")
        process: _PidWrapper around the forked child PID
        pid: Process ID of the forked child
        state: Current ProcessState
        work_queue: Pipe-backed send queue for execution_ids
        result_queue: Pipe-backed recv queue for results
        started_at: When the process was forked
        current_execution: Info about current execution (if BUSY)
        executions_completed: Number of executions this process has completed
    """

    id: str
    process: Any  # _PidWrapper
    pid: int | None
    state: ProcessState
    work_queue: Any  # _SendQueue from template_process
    result_queue: Any  # _RecvQueue from template_process
    started_at: datetime
    current_execution: ExecutionInfo | None = None
    executions_completed: int = 0
    # True once we have *attempted* to fire on_result for current_execution
    # (set before the await; stays True even if on_result raised). Reset when
    # a new execution is assigned. Used by the orphan sweep to detect handles
    # that were never attempted to be reported.
    result_reported: bool = False
    # Timestamp set when a kill was initiated (in _kill_process / _terminate_process).
    # Used by the orphan sweep to wait out the grace-sleep window before treating
    # a KILLED handle as truly stuck.
    killed_at: datetime | None = None

    @property
    def is_alive(self) -> bool:
        """Check if the process is still running."""
        return self.process.is_alive()

    @property
    def uptime_seconds(self) -> float:
        """Seconds since process was started."""
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()


class _PidWrapper:
    """
    Minimal wrapper around a PID to satisfy ProcessHandle.process interface.

    Forked children are not multiprocessing.Process objects — they're raw PIDs.
    This wrapper provides is_alive() and join() so ProcessHandle works uniformly.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.exitcode: int | None = None

    def is_alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def join(self, timeout: float | None = None) -> None:
        import time
        try:
            if timeout is not None:
                # Non-blocking waitpid with polling
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    pid, status = os.waitpid(self.pid, os.WNOHANG)
                    if pid != 0:
                        self.exitcode = os.waitstatus_to_exitcode(status)
                        return
                    time.sleep(0.1)
            else:
                _, status = os.waitpid(self.pid, 0)
                self.exitcode = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            pass  # Already reaped


# Type alias for result callback
ResultCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _get_private_dirty_kb(pid: int) -> int:
    """
    Read Private_Dirty from /proc/{pid}/smaps_rollup.

    Returns the total private dirty memory in KB, which represents
    the unique (non-shared/COW) memory for this process.
    Returns -1 if unable to read.
    """
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Private_Dirty:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (OSError, ValueError) as e:
        # /proc not available (macOS) or process gone — caller treats -1 as unknown
        logger.debug(f"could not read smaps_rollup for pid {pid}: {e}")
    return -1


class ProcessPoolManager:
    """
    Manages a pool of one-shot worker processes for execution isolation.

    Each execution forks a fresh child from the template process and the
    child exits after returning its result. There is no warm pool — the
    `max_workers` cap is the only throttle, plus a memory-pressure check
    on the way in.

    Usage:
        pool = ProcessPoolManager(
            max_workers=10,
            on_result=handle_result,
        )
        await pool.start()

        # Route execution
        await pool.route_execution(execution_id, context)

        # Shutdown
        await pool.stop()
    """

    def __init__(
        self,
        max_workers: int = 10,
        execution_timeout_seconds: int = 300,
        graceful_shutdown_seconds: int = 5,
        heartbeat_interval_seconds: int = 10,
        registration_ttl_seconds: int = 30,
        on_result: ResultCallback | None = None,
    ):
        """
        Initialize the process pool manager.

        Args:
            max_workers: Maximum number of concurrent worker processes
            execution_timeout_seconds: Default execution timeout in seconds
            graceful_shutdown_seconds: Seconds to wait between SIGTERM and SIGKILL
            heartbeat_interval_seconds: Interval for heartbeat publications
            registration_ttl_seconds: TTL for worker registration in Redis
            on_result: Async callback for handling execution results
        """
        self.max_workers = max_workers
        self.execution_timeout_seconds = execution_timeout_seconds
        self.graceful_shutdown_seconds = graceful_shutdown_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.registration_ttl_seconds = registration_ttl_seconds
        self.on_result = on_result

        # Worker ID from HOSTNAME env var (Docker container name) or UUID
        self.worker_id = os.environ.get("HOSTNAME", str(uuid.uuid4()))

        # Process tracking
        self.processes: dict[str, ProcessHandle] = {}
        self._process_counter = 0

        # State
        self._shutdown = False
        self._started = False
        self._started_at: datetime | None = None
        self._requirements_installed: int = 0
        self._requirements_total: int = 0

        # Async tasks
        self._monitor_task: asyncio.Task[None] | None = None
        self._result_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._cancel_task: asyncio.Task[None] | None = None
        self._command_task: asyncio.Task[None] | None = None

        # Redis connection
        self._redis: redis.Redis | None = None  # type: ignore[type-arg]

        # Notified when a worker exits and frees a slot. Waiters in
        # route_execution use this to wake up the moment a slot opens
        # rather than spinning on a timeout.
        self._slot_condition = asyncio.Condition()

        # Template process for fork-based workers
        self._template: TemplateProcess | None = None

        # Serializes drain_and_restart_template so concurrent package
        # installs don't race — the second call waits for the first to
        # finish rather than trying to fork while the template is down.
        self._restart_lock = asyncio.Lock()

    async def _get_redis(self) -> redis.Redis:  # type: ignore[type-arg]
        """Get or create Redis connection."""
        if self._redis is None:
            settings = get_settings()
            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    async def _start_template(self) -> None:
        """Start the template process for fork-based workers.

        The new TemplateProcess is only assigned to self._template AFTER
        start() completes successfully. This prevents fork() from racing
        against the startup ready-handshake on the same pipe — if
        self._template were assigned beforehand, a concurrent
        route_execution → _fork_process → template.fork() could
        consume the "ready" message intended for start()'s recv.

        Raises on failure. The caller (ProcessPoolManager.start) must not
        catch this — the worker process should crash-loop so Kubernetes
        restarts it and the failure is visible.
        """
        new_template = TemplateProcess()
        await asyncio.to_thread(new_template.start)
        self._template = new_template
        logger.info(f"Template process started (PID={new_template.pid})")

    async def restart_template(self) -> None:
        """
        Restart the template process (e.g., after pip install).

        All children must be drained/killed before calling this.
        """
        if self._template is not None:
            logger.info("Shutting down template process for restart")
            await asyncio.to_thread(self._template.shutdown)

        await self._start_template()
        logger.info("Template process restarted")

    async def drain_and_restart_template(self, drain_timeout: float = 60.0) -> None:
        """
        Drain in-flight executions and restart the template process so
        subsequent forks see fresh sys.modules.

        Called after pip install (and from the manual recycle RPCs).
        While the restart lock is held, new routes block in
        `route_execution`, so no work is lost — it just waits.

        Serialized via _restart_lock so concurrent package installs
        wait for the previous restart to complete rather than racing.
        """
        async with self._restart_lock:
            # Wait for in-flight one-shot workers to finish (bounded).
            deadline = time.monotonic() + drain_timeout
            while time.monotonic() < deadline:
                if not self.processes:
                    break
                await asyncio.sleep(0.2)

            # Terminate any survivors that didn't drain in time.
            for handle in list(self.processes.values()):
                if handle.id in self.processes:
                    del self.processes[handle.id]
                await self._terminate_process(handle)

            # Wake anyone blocked on a slot — drain may have removed
            # everything from self.processes while the route-side waiter
            # was still parked.
            await self._notify_slot_free()

            # Restart template with fresh sys.modules — future forks
            # (driven by route_execution) will see newly installed packages.
            await self.restart_template()

    def _fork_process(self) -> ProcessHandle:
        """
        Create a new one-shot worker process by forking from the template.

        Requires the template process to be running. The caller must
        ensure the pool has been started (and therefore _start_template
        has completed) before invoking this method.

        Returns:
            ProcessHandle for the new forked worker. State starts at BUSY
            because every fork is claimed by the routing caller (there is
            no warm pool / idle state).

        Raises:
            RuntimeError: If the template process is not alive.
        """
        if self._template is None or not self._template.is_alive():
            raise RuntimeError(
                "Cannot fork worker: template process is not running. "
                "ProcessPoolManager.start() must complete before forking workers."
            )

        self._process_counter += 1
        process_id = f"process-{self._process_counter}"

        # Fork from template (COW memory sharing). persistent=False is
        # the only mode: child runs one execution then exits.
        child_pid, work_queue, result_queue = self._template.fork(
            worker_id=process_id,
            persistent=False,
        )

        handle = ProcessHandle(
            id=process_id,
            process=_PidWrapper(child_pid),
            pid=child_pid,
            state=ProcessState.BUSY,
            work_queue=work_queue,
            result_queue=result_queue,
            started_at=datetime.now(timezone.utc),
            current_execution=None,
            executions_completed=0,
        )

        self.processes[process_id] = handle
        logger.info(f"Created worker {process_id} (PID={handle.pid})")
        return handle

    async def start(self) -> None:
        """
        Start the pool manager and spawn initial workers.

        This method:
        1. Starts the template process (so future forks are fast)
        2. Registers in Redis
        3. Starts background tasks (monitor, result, heartbeat loops)
        """
        if self._started:
            logger.warning("ProcessPoolManager already started")
            return

        logger.info(
            f"ProcessPoolManager starting (max_workers={self.max_workers})"
        )

        self._started = True
        self._shutdown = False
        self._started_at = datetime.now(timezone.utc)

        # Install requirements once (shared filesystem — all child processes inherit)
        install_result = await asyncio.to_thread(install_requirements)
        await _notify_requirements_failures(install_result)

        # Compute requirements status for heartbeat reporting
        self._update_requirements_status()

        # Start template process (loads deps, ready to fork)
        await self._start_template()

        # Register in Redis
        await self._register_worker()

        # Start background tasks
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="pool-monitor"
        )
        self._result_task = asyncio.create_task(
            self._result_loop(),
            name="pool-results"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="pool-heartbeat"
        )
        self._cancel_task = asyncio.create_task(
            self._cancel_listener_loop(),
            name="pool-cancel-listener"
        )
        self._command_task = asyncio.create_task(
            self._command_listener_loop(),
            name="pool-command-listener"
        )

        logger.info("ProcessPoolManager started")

    async def stop(self) -> None:
        """
        Gracefully stop the pool manager and all processes.

        This method:
        1. Sets shutdown flag
        2. Cancels background tasks
        3. Terminates all processes gracefully
        4. Unregisters from Redis
        """
        if self._shutdown:
            return

        logger.info("ProcessPoolManager stopping...")
        self._shutdown = True

        # Cancel background tasks
        tasks = [
            self._monitor_task,
            self._result_task,
            self._heartbeat_task,
            self._cancel_task,
            self._command_task,
        ]
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected — we just cancelled the task; no log needed
                    pass

        # Terminate all processes
        for handle in list(self.processes.values()):
            await self._terminate_process(handle)

        # Shutdown template process
        if self._template is not None:
            self._template.shutdown()
            self._template = None

        # Unregister from Redis
        await self._unregister_worker()

        # Close Redis connection
        if self._redis:
            await self._redis.aclose()
            self._redis = None

        self.processes.clear()
        self._started = False

        logger.info("ProcessPoolManager stopped")

    async def _terminate_process(self, handle: ProcessHandle) -> None:
        """
        Terminate a process gracefully (SIGTERM -> wait -> SIGKILL).

        Args:
            handle: ProcessHandle to terminate
        """
        # Mark as KILLED immediately to prevent route_execution from sending
        # work to this process during the graceful_shutdown_seconds sleep.
        handle.state = ProcessState.KILLED
        handle.killed_at = datetime.now(timezone.utc)

        if not handle.is_alive:
            return

        pid = handle.pid
        if pid is None:
            return

        logger.info(f"Terminating process {handle.id} (PID={pid})")

        # SIGTERM for graceful shutdown
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        # Wait for graceful shutdown
        await asyncio.sleep(self.graceful_shutdown_seconds)

        # SIGKILL if still alive
        if handle.process.is_alive():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                # Process died between is_alive() check and kill — that's fine
                pass
            handle.process.join(timeout=1)

    async def _wait_for_slot(self, timeout: float = 30.0) -> bool:
        """
        Wait until `len(self.processes) < self.max_workers`.

        Used by route_execution when the pool is saturated. Returns True
        as soon as a slot opens (a worker exited and notified the
        condition), or False if `timeout` seconds elapse first.
        """
        async with self._slot_condition:
            try:
                await asyncio.wait_for(
                    self._slot_condition.wait_for(
                        lambda: len(self.processes) < self.max_workers
                    ),
                    timeout=timeout,
                )
                return True
            except asyncio.TimeoutError:
                return False

    async def route_execution(
        self,
        execution_id: str,
        context: dict[str, Any],
    ) -> None:
        """
        Fork a one-shot worker for this execution.

        Waits for a free slot under the `max_workers` cap if the pool is
        saturated. The context is written to Redis, and the execution_id
        is sent to the forked child via the work queue.

        Args:
            execution_id: Unique identifier for the execution
            context: Execution context data (written to Redis)
        """
        # Wait for any in-progress drain+restart to complete before routing.
        # Without this, executions arriving during a package-install restart
        # would hit a dead template and fail with ConnectionResetError.
        async with self._restart_lock:
            pass  # just wait for it to be released

        # Write context to Redis
        await self._write_context_to_redis(execution_id, context)

        # Admission control: check memory pressure before forking
        settings = get_settings()
        if not has_sufficient_memory_cgroup(threshold=settings.memory_pressure_threshold):
            # Clean up the context we just wrote
            r = await self._get_redis()
            await r.delete(f"bifrost:exec:{execution_id}:context")
            raise MemoryError(
                f"Cannot route execution {execution_id[:8]}: memory pressure "
                f"exceeds {settings.memory_pressure_threshold:.0%} threshold"
            )

        # Wait for a slot under the max_workers cap. Worker exits notify
        # _slot_condition, so this wakes immediately once a slot frees.
        if len(self.processes) >= self.max_workers:
            if not await self._wait_for_slot():
                raise RuntimeError("No worker slot available after timeout")

        # Fork the worker. _fork_process returns a handle already in BUSY.
        handle = self._fork_process()

        # Get timeout from context or use default
        timeout = context.get("timeout_seconds", self.execution_timeout_seconds)

        handle.current_execution = ExecutionInfo(
            execution_id=execution_id,
            started_at=datetime.now(timezone.utc),
            timeout_seconds=timeout,
        )
        handle.result_reported = False

        # Send execution_id to the child
        handle.work_queue.put_nowait(execution_id)

        logger.info(
            f"Routed {execution_id[:8]}... to {handle.id} "
            f"(timeout={timeout}s)"
        )

    async def _write_context_to_redis(
        self,
        execution_id: str,
        context: dict[str, Any],
    ) -> None:
        """
        Write execution context to Redis for worker to read.

        Args:
            execution_id: Execution ID
            context: Context data to store
        """
        r = await self._get_redis()
        context_key = f"bifrost:exec:{execution_id}:context"
        await r.setex(context_key, 3600, json.dumps(context, default=str))

    async def _monitor_loop(self) -> None:
        """
        Monitor loop for health checks and timeout handling.

        Runs every 1 second to:
        1. Check for timed-out executions and kill processes
        2. Check for crashed processes and replace them
        3. Scale down excess idle processes
        4. Periodically clean stale queue entries (every 60s)
        """
        import time as _time

        logger.info("Monitor loop started")
        last_queue_cleanup = 0.0

        while not self._shutdown:
            try:
                # Check template health — restart if crashed.
                # Skip if _restart_lock is held: drain_and_restart_template
                # intentionally kills the template and will restart it itself.
                if self._template is not None and not self._template.is_alive():
                    if not self._restart_lock.locked():
                        logger.error("Template process died — restarting")
                        async with self._restart_lock:
                            # Re-check after acquiring lock — another path may
                            # have already restarted it.
                            if self._template is None or not self._template.is_alive():
                                try:
                                    await self._start_template()
                                except Exception as e:
                                    logger.error(f"Failed to restart template: {e}")

                await self._check_timeouts()
                await self._check_process_health()

                # Periodic stale queue cleanup
                now = _time.monotonic()
                if now - last_queue_cleanup > 60:
                    last_queue_cleanup = now
                    try:
                        from src.services.execution.queue_tracker import cleanup_stale_entries
                        await cleanup_stale_entries()
                    except Exception as e:
                        logger.warning(f"Queue cleanup error: {e}")
            except Exception as e:
                logger.exception(f"Monitor loop error: {e}")

            await asyncio.sleep(1.0)

        logger.info("Monitor loop stopped")

    async def _check_timeouts(self) -> None:
        """
        Check for timed-out executions and kill their processes.

        For each BUSY process, checks if the execution has exceeded
        its timeout. If so, kills the process and spawns a replacement.
        """
        for handle in list(self.processes.values()):
            if handle.state != ProcessState.BUSY:
                continue
            if handle.current_execution is None:
                continue

            exec_info = handle.current_execution
            elapsed = exec_info.elapsed_seconds

            if exec_info.timeout_seconds > 0 and elapsed > exec_info.timeout_seconds:
                logger.warning(
                    f"Execution {exec_info.execution_id} timed out after "
                    f"{elapsed:.1f}s (timeout={exec_info.timeout_seconds}s)"
                )

                # Kill process
                await self._kill_process(handle)

                # Report timeout
                await self._report_timeout(handle)

                # Remove from pool — one-shot workers don't get replaced;
                # the next execution's route_execution will fork on demand.
                # pop() not del: a peer (_handle_result) may have removed it
                # during the _kill_process / _report_timeout awaits above.
                self.processes.pop(handle.id, None)
                await self._notify_slot_free()

    async def _kill_process(self, handle: ProcessHandle) -> None:
        """
        Kill a process (SIGTERM -> wait -> SIGKILL).

        Args:
            handle: ProcessHandle to kill
        """
        # Mark as KILLED immediately to prevent route_execution from sending
        # work to this process during the graceful_shutdown_seconds sleep.
        handle.state = ProcessState.KILLED
        handle.killed_at = datetime.now(timezone.utc)

        pid = handle.pid
        if pid is None:
            return

        # SIGTERM
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        # Wait grace period
        await asyncio.sleep(self.graceful_shutdown_seconds)

        # SIGKILL if still alive
        if handle.process.is_alive():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                # Process died between is_alive() check and kill — that's fine
                pass
            handle.process.join(timeout=1)

    async def _report_timeout(self, handle: ProcessHandle) -> None:
        """
        Report a timeout to the result callback.

        No-op if `on_result` is not configured or if `current_execution` has
        already been cleared (e.g. raced with the success path).

        Args:
            handle: ProcessHandle whose current_execution timed out
        """
        exec_info = handle.current_execution
        if self.on_result is None or exec_info is None:
            return
        handle.result_reported = True
        try:
            await self.on_result({
                "type": "result",
                "execution_id": exec_info.execution_id,
                "success": False,
                "error": f"Execution timed out after {exec_info.timeout_seconds}s",
                "error_type": "TimeoutError",
                "duration_ms": int(exec_info.elapsed_seconds * 1000),
            })
        except Exception as e:
            logger.exception(f"Error reporting timeout: {e}")

    async def _cancel_listener_loop(self) -> None:
        """
        Listen for cancellation requests via Redis pub/sub.

        Subscribes to the bifrost:cancel channel and handles cancellation
        requests by killing the process handling the target execution.

        Reconnects on any failure. The pubsub object is created inside the
        outer loop so a dropped Redis connection results in a fresh pubsub
        on the next iteration rather than looping on a dead one.
        """
        logger.info("Cancel listener loop started")

        while not self._shutdown:
            pubsub = None
            try:
                r = await self._get_redis()
                pubsub = r.pubsub()
                await pubsub.subscribe("bifrost:cancel")

                while not self._shutdown:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if message and message["type"] == "message":
                        data = json.loads(message["data"])
                        execution_id = data.get("execution_id")
                        if execution_id:
                            await self._handle_cancel_request(execution_id)
            except Exception as e:
                logger.error(f"Cancel listener error: {e}; reconnecting in 1s")
                await asyncio.sleep(1.0)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe("bifrost:cancel")
                        await pubsub.aclose()
                    except Exception as e:
                        # Already-closed pubsub or Redis disconnect — best-effort cleanup
                        logger.debug(f"cancel listener pubsub cleanup failed: {e}")

        logger.info("Cancel listener loop stopped")

    async def _command_listener_loop(self) -> None:
        """
        Listen for pool management commands via Redis pub/sub.

        Subscribes to bifrost:pool:{worker_id}:commands and handles:
        - recycle_process: Recycle a specific process by PID
        - recycle_all: Mark all processes for recycling
        - resize: Update min/max workers and scale accordingly

        Reconnects on any failure. The pubsub object is created inside the
        outer loop so a dropped Redis connection results in a fresh pubsub
        on the next iteration rather than looping on a dead one.
        """
        channel = f"bifrost:pool:{self.worker_id}:commands"
        logger.info(f"Command listener loop started on channel {channel}")

        while not self._shutdown:
            pubsub = None
            try:
                r = await self._get_redis()
                pubsub = r.pubsub()
                await pubsub.subscribe(channel)

                while not self._shutdown:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if message and message["type"] == "message":
                        data = json.loads(message["data"])
                        await self._handle_command(data)
            except Exception as e:
                logger.error(f"Command listener error: {e}; reconnecting in 1s")
                await asyncio.sleep(1.0)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(channel)
                        await pubsub.aclose()
                    except Exception as e:
                        # Already-closed pubsub or Redis disconnect — best-effort cleanup
                        logger.debug(f"command listener pubsub cleanup failed: {e}")

        logger.info("Command listener loop stopped")

    async def _handle_command(self, command: dict[str, Any]) -> None:
        """
        Dispatch a pool management command to the appropriate handler.

        Args:
            command: Command dict with 'action' field and action-specific data
        """
        action = command.get("action")
        logger.info(f"Received command: {action}")

        if action == "recycle_process":
            await self._handle_recycle_process_command(command)
        elif action == "recycle_all":
            await self._handle_recycle_all_command(command)
        else:
            logger.warning(f"Unknown command action: {action}")

    async def _handle_recycle_process_command(self, command: dict[str, Any]) -> None:
        """
        Handle recycle_process command.

        Workers are one-shot in this pool, so "recycle this specific PID"
        has no per-PID meaning — by the time the command lands the child
        may already have exited. We delegate to the same drain+restart
        path used by recycle_all so the operator-visible behavior is
        consistent: in-flight executions are allowed to finish, then the
        template is restarted so subsequent forks see fresh sys.modules.
        """
        pid = command.get("pid")
        reason = command.get("reason", "API request")
        logger.info(
            f"Processing recycle_process command for PID={pid} (reason: {reason}) "
            f"— delegating to drain+restart"
        )
        await self._recycle_via_drain(reason=reason)

    async def _handle_recycle_all_command(self, command: dict[str, Any]) -> None:
        """
        Handle recycle_all command: drain in-flight executions and
        restart the template so future forks see fresh sys.modules.

        Args:
            command: Command dict with optional 'reason' field
        """
        reason = command.get("reason", "API request")
        logger.info(f"Processing recycle_all command (reason: {reason})")
        await self._recycle_via_drain(reason=reason)

    async def _recycle_via_drain(self, reason: str) -> None:
        """
        Shared recycle path: pip-install requirements, then drain
        in-flight executions and restart the template process.

        Used by both `recycle_process` and `recycle_all` RPC commands as
        well as by the post-pip-install consumer.
        """
        # Pick up any requirements changes published to S3/Redis since
        # last start (recycle is typically triggered after a package
        # install on the API container).
        install_result = await asyncio.to_thread(install_requirements)
        await _notify_requirements_failures(install_result)
        self._update_requirements_status()

        in_flight = len(self.processes)
        try:
            from src.core.pubsub import publish_pool_scaling

            await publish_pool_scaling(
                worker_id=self.worker_id,
                action="recycle_all",
                processes_affected=in_flight,
            )
        except Exception as e:
            logger.warning(f"Failed to publish recycle scaling event: {e}")

        await self.drain_and_restart_template()
        logger.info(f"recycle_via_drain complete (reason: {reason}, drained {in_flight} processes)")

    async def _handle_cancel_request(self, execution_id: str) -> None:
        """
        Handle cancellation request for a running execution.

        Finds the process handling the execution and kills it.

        Args:
            execution_id: Execution ID to cancel
        """
        # Find process handling this execution
        for handle in list(self.processes.values()):
            if (
                handle.current_execution
                and handle.current_execution.execution_id == execution_id
            ):
                logger.info(f"Cancelling execution {execution_id[:8]}...")

                # Kill process (same as timeout)
                await self._kill_process(handle)

                # Report cancellation
                await self._report_cancellation(handle)

                # Remove from pool — one-shot worker, no replacement.
                # pop() not del: a peer (_handle_result) may have removed it
                # during the _kill_process / _report_cancellation awaits.
                self.processes.pop(handle.id, None)
                await self._notify_slot_free()

                return

        logger.debug(
            f"Cancellation request for {execution_id[:8]}... - not found in pool"
        )

    async def _report_cancellation(self, handle: ProcessHandle) -> None:
        """
        Report a cancellation to the result callback.

        No-op if `on_result` is not configured or if `current_execution` has
        already been cleared.

        Args:
            handle: ProcessHandle whose current_execution was cancelled
        """
        exec_info = handle.current_execution
        if self.on_result is None or exec_info is None:
            return
        handle.result_reported = True
        try:
            await self.on_result({
                "type": "result",
                "execution_id": exec_info.execution_id,
                "success": False,
                "error": "Execution was cancelled",
                "error_type": "CancelledError",
                "duration_ms": int(exec_info.elapsed_seconds * 1000),
            })
        except Exception as e:
            logger.exception(f"Error reporting cancellation: {e}")

    async def _check_process_health(self) -> None:
        """
        Check for crashed processes and replace them.

        Case A: Process is dead and state is NOT KILLED — unexpected crash
        (SIGSEGV, worker exit, etc.). Report crash if not already reported.

        Case B: Process is in KILLED state with a current_execution but
        result_reported is False — cancel/timeout was attempted but the result
        callback never fired. Fire a synthetic orphan callback to recover.
        """
        to_remove: list[str] = []

        # Snapshot to a list — items() iterator is unsafe across the awaits
        # below (peer coroutines like _handle_result mutate self.processes).
        for process_id, handle in list(self.processes.items()):
            # Peer may have already cleaned this id during a prior await.
            if process_id not in self.processes:
                continue
            if not handle.is_alive and handle.state != ProcessState.KILLED:
                # Case A: unexpected crash
                logger.warning(
                    f"Process {process_id} crashed "
                    f"(exit_code={handle.process.exitcode})"
                )

                if handle.current_execution and not handle.result_reported:
                    await self._report_crash(handle)

                to_remove.append(process_id)

            elif handle.state == ProcessState.KILLED and handle.current_execution:
                # Case B: KILLED — wait out the kill grace window before treating
                # as orphaned. During the legitimate cancel/timeout grace-sleep,
                # the cancel/timeout path is *about to* fire its own callback;
                # orphan-sweeping now would duplicate it.
                grace_buffer = self.graceful_shutdown_seconds + 1.0
                killed_at = handle.killed_at
                if killed_at is None or (datetime.now(timezone.utc) - killed_at).total_seconds() < grace_buffer:
                    continue  # not yet time to consider this orphaned

                if not handle.result_reported:
                    logger.warning(
                        f"Process {process_id} is KILLED but execution "
                        f"{handle.current_execution.execution_id[:8]}... was never reported — "
                        f"firing orphan callback"
                    )
                    await self._report_orphan(handle)
                to_remove.append(process_id)

        # Remove crashed/orphaned processes. One-shot workers don't get
        # replaced — the next execution's route_execution will fork.
        # Use pop() not del because a peer (e.g. _handle_result) may have
        # already removed the id during one of the awaits above. We still
        # notify slot waiters if anything was removed here, since the
        # peer-delete path also notifies and double-notify is harmless.
        if to_remove:
            removed_any = False
            for process_id in to_remove:
                if self.processes.pop(process_id, None) is not None:
                    removed_any = True
            if removed_any:
                await self._notify_slot_free()

    async def _report_orphan(self, handle: ProcessHandle) -> None:
        """
        Report an orphaned execution — KILLED state with no prior reporting.

        Used by _check_process_health to recover from races where the cancel/
        timeout path killed a process but never fired the result callback.
        No-op if `on_result` is not configured or if `current_execution` has
        already been cleared.

        Args:
            handle: ProcessHandle whose current_execution was orphaned
        """
        exec_info = handle.current_execution
        if self.on_result is None or exec_info is None:
            return
        handle.result_reported = True
        try:
            await self.on_result({
                "type": "result",
                "execution_id": exec_info.execution_id,
                "success": False,
                "error": "Execution orphaned — process was killed but result was never reported",
                "error_type": "OrphanedExecution",
                "duration_ms": int(exec_info.elapsed_seconds * 1000),
            })
        except Exception as e:
            logger.exception(f"Error reporting orphan: {e}")

    async def _report_crash(self, handle: ProcessHandle) -> None:
        """
        Report a crash to the result callback.

        No-op if `on_result` is not configured or if `current_execution` has
        already been cleared.

        Args:
            handle: ProcessHandle whose current_execution crashed
        """
        exec_info = handle.current_execution
        if self.on_result is None or exec_info is None:
            return
        handle.result_reported = True
        try:
            await self.on_result({
                "type": "result",
                "execution_id": exec_info.execution_id,
                "success": False,
                "error": "Worker process crashed unexpectedly",
                "error_type": "ProcessCrashError",
                "duration_ms": int(exec_info.elapsed_seconds * 1000),
            })
        except Exception as e:
            logger.exception(f"Error reporting crash: {e}")

    async def _result_loop(self) -> None:
        """
        Collect results from all process result queues.

        Polls result queues from all processes (non-blocking) and
        handles completed executions.
        """
        logger.info("Result loop started")

        while not self._shutdown:
            try:
                for handle in list(self.processes.values()):
                    try:
                        result = handle.result_queue.get_nowait()
                        await self._handle_result(handle, result)
                    except Empty:
                        # Hot polling loop — Empty is the common case (no work ready).
                        # Logging here would flood at 10Hz × N processes; intentionally silent.
                        pass
                    except Exception as e:
                        logger.exception(f"Result loop error for {handle.id}: {e}")
            except Exception as e:
                logger.exception(f"Result loop error: {e}")

            await asyncio.sleep(0.1)

        logger.info("Result loop stopped")

    async def _handle_result(
        self,
        handle: ProcessHandle,
        result: dict[str, Any],
    ) -> None:
        """
        Handle a result from a one-shot worker process.

        The child has already exited (or is about to); remove the handle,
        wake any waiters blocked on a free slot, then fire the callback.

        Args:
            handle: ProcessHandle that produced the result
            result: Result data from the worker
        """
        # Mark result as reported before clearing current_execution so the invariant
        # ("result_reported=True once on_result has fired") holds for external observers.
        handle.result_reported = True

        # Clear current execution
        handle.current_execution = None
        handle.executions_completed += 1

        # Remove the handle (frees a slot under max_workers) and wake any
        # waiters in route_execution that were blocked on a free slot.
        # pop() not check-then-del: race-safe against concurrent cleaners.
        self.processes.pop(handle.id, None)
        await self._notify_slot_free()

        # Forward result to callback
        if self.on_result:
            try:
                await self.on_result(result)
            except Exception as e:
                logger.exception(f"Error in result callback: {e}")

    async def _notify_slot_free(self) -> None:
        """Wake any tasks blocked in `_wait_for_slot`."""
        async with self._slot_condition:
            self._slot_condition.notify_all()

    async def _heartbeat_loop(self) -> None:
        """
        Periodic heartbeat publishing loop.

        Refreshes Redis TTL and publishes heartbeat with pool state.
        """
        logger.info(
            f"Heartbeat loop started (interval={self.heartbeat_interval_seconds}s)"
        )

        while not self._shutdown:
            try:
                # Refresh registration
                await self._refresh_registration()

                # Build and publish heartbeat
                heartbeat = self._build_heartbeat()
                await self._publish_heartbeat(heartbeat)
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

            await asyncio.sleep(self.heartbeat_interval_seconds)

        logger.info("Heartbeat loop stopped")

    async def _register_worker(self) -> None:
        """
        Register worker in Redis with TTL.

        Creates a Redis hash with worker metadata and sets TTL.
        Includes list of installed packages for API visibility.
        """
        r = await self._get_redis()
        redis_key = f"bifrost:pool:{self.worker_id}"

        # Get installed packages for API visibility
        packages = _get_installed_packages()

        # Store pool metadata in Redis hash
        await r.hset(  # type: ignore[misc]
            redis_key,
            mapping={
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "online",
                "hostname": os.environ.get("HOSTNAME", "unknown"),
                "packages": json.dumps(packages),
            }
        )
        await r.expire(redis_key, self.registration_ttl_seconds)

        logger.info(
            f"Registered pool {self.worker_id} in Redis "
            f"(TTL={self.registration_ttl_seconds}s, {len(packages)} packages)"
        )

    async def _refresh_registration(self) -> None:
        """Refresh the Redis registration TTL."""
        r = await self._get_redis()
        await r.expire(f"bifrost:pool:{self.worker_id}", self.registration_ttl_seconds)

    async def update_packages(self) -> None:
        """
        Update the packages field in Redis after a package installation.

        Called by the package install consumer after successfully installing a package.
        This ensures the /api/packages endpoint reflects newly installed packages.
        """
        if not self._started:
            logger.debug("Pool not started, skipping package update")
            return

        try:
            r = await self._get_redis()
            redis_key = f"bifrost:pool:{self.worker_id}"
            packages = _get_installed_packages()
            await r.hset(redis_key, "packages", json.dumps(packages))  # type: ignore[misc]
            logger.info(f"Updated packages in Redis: {len(packages)} packages")
        except Exception as e:
            logger.warning(f"Failed to update packages in Redis: {e}")

    async def _unregister_worker(self) -> None:
        """
        Unregister worker from Redis.

        Deletes the Redis key and publishes offline event.
        """
        try:
            r = await self._get_redis()
            await r.delete(f"bifrost:pool:{self.worker_id}")
            logger.info(f"Unregistered pool {self.worker_id} from Redis")

            # Publish offline event
            try:
                from src.core.pubsub import publish_worker_event

                await publish_worker_event({
                    "type": "worker_offline",
                    "worker_id": self.worker_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                logger.warning(f"Failed to publish worker_offline event: {e}")

        except Exception as e:
            logger.error(f"Error unregistering worker: {e}")

    def _update_requirements_status(self) -> None:
        """
        Compare installed packages against requirements.txt.

        Sets _requirements_installed and _requirements_total for heartbeat reporting.
        Called after install_requirements() at startup and after recycle_all.
        """
        try:
            from src.core.requirements_cache import get_requirements_sync

            content = get_requirements_sync()
            if not content:
                self._requirements_total = 0
                self._requirements_installed = 0
                return

            required = {
                line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip().lower()
                for line in content.strip().split("\n")
                if line.strip()
            }
            self._requirements_total = len(required)

            installed = {p["name"].lower() for p in _get_installed_packages()}
            self._requirements_installed = len(required & installed)

            missing = required - installed
            if missing:
                logger.warning(f"[pool] Missing required packages: {', '.join(sorted(missing))}")
            else:
                logger.info(
                    f"[pool] All {self._requirements_total} required packages installed"
                )
        except Exception as e:
            logger.warning(f"Failed to check requirements status: {e}")

    def _build_heartbeat(self) -> dict[str, Any]:
        """
        Build heartbeat payload with all process state.

        Returns:
            Dict with worker_id, timestamp, processes, and pool info
        """
        processes = []
        for p in self.processes.values():
            private_dirty_kb = _get_private_dirty_kb(p.pid) if p.pid else -1
            # Prefer private-dirty (USS-like) for display: RSS counts COW-shared
            # pages from the fork template, inflating per-fork memory and not
            # summing cleanly across forks. Fall back to RSS if smaps_rollup
            # is unavailable.
            if private_dirty_kb >= 0:
                memory_mb: float = private_dirty_kb / 1024
            else:
                memory_mb = self._get_process_memory(p.pid)
            info: dict[str, Any] = {
                "pid": p.pid,
                "process_id": p.id,
                "state": p.state.value,
                "memory_mb": memory_mb,
                "private_dirty_kb": private_dirty_kb,
                "uptime_seconds": p.uptime_seconds,
                "executions_completed": p.executions_completed,
            }
            if p.current_execution:
                info["execution"] = {
                    "execution_id": p.current_execution.execution_id,
                    "started_at": p.current_execution.started_at.isoformat(),
                    "elapsed_seconds": p.current_execution.elapsed_seconds,
                }
            processes.append(info)

        # In on-demand mode every handle is BUSY or KILLED — no idle pool.
        # Keep `idle_count` in the heartbeat shape for back-compat (always 0).
        idle_count = 0
        busy_count = len([p for p in self.processes.values() if p.state == ProcessState.BUSY])

        memory_current, memory_max = get_cgroup_memory()

        return {
            "type": "worker_heartbeat",
            "worker_id": self.worker_id,
            "hostname": os.environ.get("HOSTNAME", "unknown"),
            "status": "online",
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processes": processes,
            "pool_size": len(self.processes),
            "idle_count": idle_count,
            "busy_count": busy_count,
            "requirements_installed": self._requirements_installed,
            "requirements_total": self._requirements_total,
            "memory_current_bytes": memory_current,
            "memory_max_bytes": memory_max,
        }

    def _get_process_memory(self, pid: int | None) -> float:
        """
        Get memory usage for a process in MB.

        Args:
            pid: Process ID to check

        Returns:
            Memory usage in MB, or 0 if not available
        """
        if pid is None:
            return 0.0
        try:
            process = psutil.Process(pid)
            return process.memory_info().rss / 1024 / 1024
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

    async def _publish_heartbeat(self, heartbeat: dict[str, Any]) -> None:
        """
        Publish heartbeat to WebSocket channel.

        Args:
            heartbeat: Heartbeat payload to publish
        """
        try:
            from src.core.pubsub import publish_worker_heartbeat

            await publish_worker_heartbeat(heartbeat)
        except Exception as e:
            logger.warning(f"Failed to publish heartbeat: {e}")

    def get_status(self) -> dict[str, Any]:
        """
        Get current pool status.

        Returns:
            Dict with pool state and process details
        """
        return {
            "started": self._started,
            "shutdown": self._shutdown,
            "worker_id": self.worker_id,
            "pool_size": len(self.processes),
            "processes": [
                {
                    "process_id": p.id,
                    "pid": p.pid,
                    "state": p.state.value,
                    "uptime_seconds": p.uptime_seconds,
                    "executions_completed": p.executions_completed,
                    "is_alive": p.is_alive,
                    "current_execution": p.current_execution.execution_id if p.current_execution else None,
                }
                for p in self.processes.values()
            ],
        }


# Global pool instance
_pool: ProcessPoolManager | None = None


def get_process_pool() -> ProcessPoolManager:
    """
    Get the global process pool instance, creating if needed.

    Uses settings for configuration.
    """
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ProcessPoolManager(
            max_workers=settings.max_workers,
            execution_timeout_seconds=settings.execution_timeout_seconds,
            graceful_shutdown_seconds=settings.graceful_shutdown_seconds,
            heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
            registration_ttl_seconds=settings.worker_registration_ttl_seconds,
        )
    return _pool


async def shutdown_process_pool() -> None:
    """Shutdown the global process pool."""
    global _pool
    if _pool:
        await _pool.stop()
        _pool = None
