"""
Process Pool Manager for Execution Isolation.

This module provides a pool of long-lived worker processes that can handle
concurrent executions. Unlike the one-process-per-execution model, this
approach reuses processes for multiple executions, improving efficiency.

Key features:
- Dynamic scaling between min_workers and max_workers
- Automatic timeout handling with graceful shutdown (SIGTERM -> SIGKILL)
- Crash detection and process replacement
- Heartbeat publishing for UI visibility
- Manual process recycling via API

Architecture:
    ProcessPoolManager (runs in consumer process)
        |
        +-- min_workers to max_workers processes
        +-- Each process: work_queue (in) + result_queue (out)
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
from datetime import datetime, timedelta, timezone
from enum import Enum
from queue import Empty
from typing import Any, Awaitable, Callable

import psutil
import redis.asyncio as redis

from src.config import get_settings
from src.services.execution.memory_monitor import get_cgroup_memory, has_sufficient_memory_cgroup
from src.services.execution.simple_worker import install_requirements
from src.services.execution.template_process import TemplateProcess

logger = logging.getLogger(__name__)

_CLEAN_EXIT_RESULT_GRACE = timedelta(seconds=2)


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

    States:
    - IDLE: Ready to accept work
    - BUSY: Currently executing
    - KILLED: Process was terminated (pending removal)
    """

    IDLE = "idle"
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
    pending_recycle: bool = False  # Mark for recycle after current execution
    # True once we have *attempted* to fire on_result for current_execution
    # (set before the await; stays True even if on_result raised). Reset when
    # a new execution is assigned. Used by the orphan sweep to detect handles
    # that were never attempted to be reported.
    result_reported: bool = False
    # Timestamp set when a kill was initiated (in _kill_process / _terminate_process).
    # Used by the orphan sweep to wait out the grace-sleep window before treating
    # a KILLED handle as truly stuck.
    killed_at: datetime | None = None
    # Timestamp set when health checks first observe a cleanly exited process
    # whose result has not reached the result queue yet.
    clean_exit_observed_at: datetime | None = None

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

    def _reap_if_exited(self) -> bool:
        """Reap the child if it has exited, returning True when no child remains."""
        if self.exitcode is not None:
            return True
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid == 0:
                return False
            self.exitcode = os.waitstatus_to_exitcode(status)
            return True
        except ChildProcessError:
            # Already reaped elsewhere.
            self.exitcode = 0 if self.exitcode is None else self.exitcode
            return True

    def is_alive(self) -> bool:
        if self._reap_if_exited():
            return False
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
                    if self._reap_if_exited():
                        return
                    time.sleep(0.1)
                self._reap_if_exited()
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
    Manages a pool of worker processes for execution isolation.

    The ProcessPoolManager:
    1. Spawns min_workers processes on startup
    2. Scales up to max_workers under load
    3. Routes executions to IDLE processes
    4. Monitors for timeouts and crashes
    5. Publishes heartbeats for UI visibility

    Usage:
        pool = ProcessPoolManager(
            min_workers=2,
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
        min_workers: int = 2,
        max_workers: int = 10,
        execution_timeout_seconds: int = 300,
        graceful_shutdown_seconds: int = 5,
        recycle_after_executions: int = 0,
        recycle_memory_mb: int = 0,
        heartbeat_interval_seconds: int = 10,
        registration_ttl_seconds: int = 30,
        on_result: ResultCallback | None = None,
    ):
        """
        Initialize the process pool manager.

        Args:
            min_workers: Minimum number of worker processes to maintain
            max_workers: Maximum number of worker processes
            execution_timeout_seconds: Default execution timeout in seconds
            graceful_shutdown_seconds: Seconds to wait between SIGTERM and SIGKILL
            recycle_after_executions: Recycle process after N executions (0 = never)
            recycle_memory_mb: Recycle process when RSS exceeds this MB (0 = disabled)
            heartbeat_interval_seconds: Interval for heartbeat publications
            registration_ttl_seconds: TTL for worker registration in Redis
            on_result: Async callback for handling execution results
        """
        self.min_workers = min_workers
        self.max_workers = max_workers
        self.execution_timeout_seconds = execution_timeout_seconds
        self.graceful_shutdown_seconds = graceful_shutdown_seconds
        self.recycle_after_executions = recycle_after_executions
        self.recycle_memory_mb = recycle_memory_mb
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

        # Lock for idle process waiting
        self._idle_condition = asyncio.Condition()

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
        Drain all existing workers, restart the template process, then
        respawn workers from the fresh template.

        Called after pip install so children get a fresh sys.modules
        that can see newly installed packages.

        Serialized via _restart_lock so concurrent package installs
        wait for the previous restart to complete rather than racing.
        """
        async with self._restart_lock:
            # 1. Mark all for recycle (stops routing new work to them)
            for handle in list(self.processes.values()):
                handle.pending_recycle = True

            # 2. Wait for busy processes to finish (bounded)
            deadline = time.monotonic() + drain_timeout
            while time.monotonic() < deadline:
                busy = [h for h in self.processes.values() if h.state == ProcessState.BUSY]
                if not busy:
                    break
                await asyncio.sleep(0.2)

            # 3. Terminate all remaining processes (don't spawn replacements yet)
            for handle in list(self.processes.values()):
                if handle.id in self.processes:
                    del self.processes[handle.id]
                await self._terminate_process(handle)

            # 4. Restart template with fresh sys.modules
            await self.restart_template()

            # 5. Respawn to min_workers
            for _ in range(self.min_workers):
                self._fork_process()

    def _fork_process(self, persistent: bool | None = None) -> ProcessHandle:
        """
        Create a new worker process by forking from the template.

        Requires the template process to be running. The caller must
        ensure the pool has been started (and therefore _start_template
        has completed) before invoking this method.

        Args:
            persistent: If None, inferred from self.min_workers > 0.
                        If True, child loops. If False, child runs once.

        Returns:
            ProcessHandle instance for the new forked worker.

        Raises:
            RuntimeError: If the template process is not alive.
        """
        if self._template is None or not self._template.is_alive():
            raise RuntimeError(
                "Cannot fork worker: template process is not running. "
                "ProcessPoolManager.start() must complete before forking workers."
            )

        if persistent is None:
            persistent = self.min_workers > 0

        self._process_counter += 1
        process_id = f"process-{self._process_counter}"

        # Fork from template (COW memory sharing)
        child_pid, work_queue, result_queue = self._template.fork(
            worker_id=process_id,
            persistent=persistent,
        )

        handle = ProcessHandle(
            id=process_id,
            process=_PidWrapper(child_pid),
            pid=child_pid,
            state=ProcessState.IDLE,
            work_queue=work_queue,
            result_queue=result_queue,
            started_at=datetime.now(timezone.utc),
            current_execution=None,
            executions_completed=0,
        )

        self.processes[process_id] = handle
        logger.info(f"Created worker {process_id} (PID={handle.pid}, persistent={persistent})")
        return handle

    async def start(self) -> None:
        """
        Start the pool manager and spawn initial workers.

        This method:
        1. Spawns min_workers processes
        2. Registers in Redis
        3. Starts background tasks (monitor, result, heartbeat loops)
        """
        if self._started:
            logger.warning("ProcessPoolManager already started")
            return

        logger.info(
            f"ProcessPoolManager starting with min_workers={self.min_workers}, "
            f"max_workers={self.max_workers}"
        )

        self._started = True
        self._shutdown = False
        self._started_at = datetime.now(timezone.utc)

        # Install requirements once (shared filesystem — all child processes inherit)
        await asyncio.to_thread(install_requirements)

        # Compute requirements status for heartbeat reporting
        self._update_requirements_status()

        # Start template process (loads deps, ready to fork)
        await self._start_template()

        # Spawn initial pool
        for _ in range(self.min_workers):
            self._fork_process()

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

        logger.info(
            f"ProcessPoolManager started with {len(self.processes)} workers"
        )

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

    async def _reap_process(self, handle: ProcessHandle, timeout: float = 1.0) -> None:
        """Reap a raw forked child so completed on-demand work does not become a zombie."""
        try:
            await asyncio.to_thread(handle.process.join, timeout)
        except Exception as e:
            logger.debug(f"Failed to reap process {handle.id}: {e}")

    def _get_idle_process(self) -> ProcessHandle | None:
        """
        Get an IDLE process from the pool.

        Returns:
            ProcessHandle with IDLE state, or None if no idle processes
        """
        for handle in self.processes.values():
            # Skip processes that are pending recycle - they're about to be terminated
            if handle.pending_recycle:
                continue
            if handle.state == ProcessState.IDLE and handle.is_alive:
                return handle
        return None

    async def _wait_for_idle_process(self, timeout: float = 30.0) -> ProcessHandle | None:
        """
        Wait for an idle process to become available.

        Args:
            timeout: Maximum seconds to wait

        Returns:
            ProcessHandle with IDLE state, or None if timeout
        """
        async with self._idle_condition:
            try:
                await asyncio.wait_for(
                    self._idle_condition.wait_for(lambda: self._get_idle_process() is not None),
                    timeout=timeout
                )
                return self._get_idle_process()
            except asyncio.TimeoutError:
                return None

    async def route_execution(
        self,
        execution_id: str,
        context: dict[str, Any],
    ) -> None:
        """
        Route an execution to an idle process.

        The context is written to Redis, and the execution_id is sent
        to the process via the work queue.

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

        # Find or create idle process
        idle = self._get_idle_process()
        if idle is None:
            # Scale up if possible
            if len(self.processes) < self.max_workers:
                idle = self._fork_process()
            else:
                # Wait for a process to become idle
                idle = await self._wait_for_idle_process()
                if idle is None:
                    raise RuntimeError("No idle process available after timeout")

        # Get timeout from context or use default
        timeout = context.get("timeout_seconds", self.execution_timeout_seconds)

        # Assign work
        idle.state = ProcessState.BUSY
        idle.current_execution = ExecutionInfo(
            execution_id=execution_id,
            started_at=datetime.now(timezone.utc),
            timeout_seconds=timeout,
        )
        idle.result_reported = False
        idle.clean_exit_observed_at = None

        # Send to process
        idle.work_queue.put_nowait(execution_id)

        logger.info(
            f"Routed {execution_id[:8]}... to {idle.id} "
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
                await self._maybe_scale_down()

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

                # Remove from pool
                del self.processes[handle.id]

                # Spawn replacement if below min_workers
                if len(self.processes) < self.min_workers:
                    self._fork_process()

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
        Handle recycle_process command - recycle a specific process by PID.

        Args:
            command: Command dict with 'pid' field
        """
        pid = command.get("pid")
        reason = command.get("reason", "API request")

        if pid is None:
            logger.warning("recycle_process command missing 'pid' field")
            return

        logger.info(f"Processing recycle_process command for PID={pid} (reason: {reason})")

        success = await self.recycle_process(pid=pid)
        if success:
            logger.info(f"Successfully recycled process PID={pid}")
        else:
            logger.warning(f"Failed to recycle process PID={pid} (not found or busy)")

    async def _handle_recycle_all_command(self, command: dict[str, Any]) -> None:
        """
        Handle recycle_all command - mark all processes for recycling.

        Idle processes are recycled immediately with progress updates.
        Busy processes are marked for recycling after their current execution.

        Args:
            command: Command dict with optional 'reason' field
        """
        reason = command.get("reason", "API request")
        logger.info(f"Processing recycle_all command (reason: {reason})")

        # Install requirements before spawning replacement processes
        # (recycle_all is typically triggered after a package install on the API container;
        # the worker container needs to pip install from requirements.txt)
        await asyncio.to_thread(install_requirements)
        self._update_requirements_status()

        count, idle_handles = self.mark_for_recycle()
        logger.info(f"Marked {count} processes for recycling ({len(idle_handles)} idle)")

        # Publish initial scaling event for UI feedback
        if count > 0:
            try:
                from src.core.pubsub import publish_pool_scaling

                await publish_pool_scaling(
                    worker_id=self.worker_id,
                    action="recycle_all",
                    processes_affected=count,
                )
            except Exception as e:
                logger.warning(f"Failed to publish recycle_all event: {e}")

        # Recycle idle processes with progress updates
        for i, handle in enumerate(idle_handles):
            try:
                from src.core.pubsub import publish_pool_progress
                await publish_pool_progress(
                    worker_id=self.worker_id,
                    action="recycle_all",
                    current=i + 1,
                    total=count,
                    message=f"Recycling process {i + 1} of {count}...",
                )
            except Exception as e:
                logger.warning(f"Failed to publish progress: {e}")

            # Recycle this idle process
            await self._recycle_idle_process(handle)

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

                # Remove from pool and replace
                del self.processes[handle.id]
                if len(self.processes) < self.min_workers:
                    self._fork_process()

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

        for process_id, handle in list(self.processes.items()):
            if not handle.is_alive and handle.state != ProcessState.KILLED:
                try:
                    result = handle.result_queue.get_nowait()
                    if isinstance(result, dict):
                        await self._handle_result(handle, result)
                        continue
                except Empty:
                    if (
                        handle.process.exitcode == 0
                        and handle.current_execution
                        and not handle.result_reported
                    ):
                        now = datetime.now(timezone.utc)
                        if handle.clean_exit_observed_at is None:
                            handle.clean_exit_observed_at = now

                        if now - handle.clean_exit_observed_at < _CLEAN_EXIT_RESULT_GRACE:
                            continue

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

        # Remove crashed/orphaned processes
        for process_id in to_remove:
            handle = self.processes[process_id]
            await self._reap_process(handle)
            del self.processes[process_id]

        # Spawn replacements to maintain min_workers
        while len(self.processes) < self.min_workers:
            self._fork_process()

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

    async def _maybe_scale_down(self) -> None:
        """
        Remove excess idle processes if above min_workers.

        Removes the oldest idle processes first to maintain min_workers.
        """
        idle_processes = [
            p for p in self.processes.values()
            if p.state == ProcessState.IDLE and p.is_alive
        ]

        excess = len(self.processes) - self.min_workers
        if excess <= 0:
            return

        # Sort by age (oldest first)
        idle_processes.sort(key=lambda p: p.started_at)

        # Remove oldest idle processes up to excess count
        to_remove = idle_processes[:excess]

        if not to_remove:
            return

        # Publish scaling event
        try:
            from src.core.pubsub import publish_pool_scaling
            await publish_pool_scaling(
                worker_id=self.worker_id,
                action="scale_down",
                processes_affected=len(to_remove),
            )
        except Exception as e:
            logger.warning(f"Failed to publish scale_down event: {e}")

        for i, handle in enumerate(to_remove):
            # Publish progress
            try:
                from src.core.pubsub import publish_pool_progress
                await publish_pool_progress(
                    worker_id=self.worker_id,
                    action="scale_down",
                    current=i + 1,
                    total=len(to_remove),
                    message=f"Terminating process {i + 1} of {len(to_remove)}...",
                )
            except Exception as e:
                logger.warning(f"Failed to publish progress: {e}")

            logger.info(f"Scaling down: removing idle process {handle.id}")
            await self._terminate_process(handle)
            del self.processes[handle.id]

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
        Handle a result from a worker process.

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

        # On-demand mode: child exits after one execution, just clean up
        if self.min_workers == 0:
            await self._reap_process(handle)
            if handle.id in self.processes:
                del self.processes[handle.id]
            # Forward result to callback
            if self.on_result:
                try:
                    await self.on_result(result)
                except Exception as e:
                    logger.exception(f"Error in result callback: {e}")
            return

        # Check if should recycle (pending flag or execution count threshold)
        if handle.pending_recycle:
            logger.info(f"Recycling process {handle.id} (pending recycle flag)")
            await self._recycle_process(handle)
            return

        if self.recycle_after_executions > 0:
            if handle.executions_completed >= self.recycle_after_executions:
                logger.info(
                    f"Recycling process {handle.id} after "
                    f"{handle.executions_completed} executions"
                )
                await self._recycle_process(handle)
                return

        # Memory-based recycling (safety net for Python arena fragmentation)
        if self.recycle_memory_mb > 0:
            rss_bytes = result.get("process_rss_bytes", 0)
            if rss_bytes > 0:
                rss_mb = rss_bytes / (1024 * 1024)
                if rss_mb > self.recycle_memory_mb:
                    logger.info(
                        f"Recycling {handle.id}: RSS {rss_mb:.0f}MB > "
                        f"threshold {self.recycle_memory_mb}MB"
                    )
                    await self._recycle_process(handle)
                    return

        # Return to IDLE state
        handle.state = ProcessState.IDLE

        # Notify any waiters that an idle process is available
        async with self._idle_condition:
            self._idle_condition.notify_all()

        # Forward result to callback
        if self.on_result:
            try:
                await self.on_result(result)
            except Exception as e:
                logger.exception(f"Error in result callback: {e}")

    async def _recycle_process(self, handle: ProcessHandle) -> None:
        """
        Recycle a process by terminating and spawning replacement.

        Args:
            handle: ProcessHandle to recycle
        """
        # Check if already removed (race condition with multiple recycle paths)
        if handle.id not in self.processes:
            return

        logger.info(
            f"Recycling process {handle.id} after "
            f"{handle.executions_completed} executions"
        )

        # Remove from processes dict FIRST to prevent routing to dying process
        # A new process will be spawned with a fresh work queue
        del self.processes[handle.id]

        # Spawn replacement immediately so we maintain worker count
        self._fork_process()

        # Then terminate the old process (this includes grace period wait)
        await self._terminate_process(handle)

    async def recycle_process(self, pid: int | None = None) -> bool:
        """
        Manually recycle a process.

        If pid is provided, recycles that specific process.
        If pid is None, recycles any idle process.

        Args:
            pid: Process ID to recycle, or None for any idle

        Returns:
            True if recycle was triggered, False if not found
        """
        target: ProcessHandle | None = None

        if pid is not None:
            for p in self.processes.values():
                if p.pid == pid:
                    target = p
                    break
        else:
            # Find any idle process
            target = self._get_idle_process()

        if target is None:
            return False

        if target.state == ProcessState.BUSY:
            logger.warning(f"Cannot recycle busy process {target.id}")
            return False

        await self._terminate_process(target)
        # The process might have been removed by the monitor loop during the
        # graceful shutdown wait. Only delete if still present.
        if target.id in self.processes:
            del self.processes[target.id]
        self._fork_process()

        return True

    async def _scale_down_process(self, handle: ProcessHandle) -> None:
        """
        Terminate a process as part of scale-down operation.

        Only terminates idle processes. If the process becomes busy
        before we can terminate it, skip it.

        Args:
            handle: ProcessHandle to terminate
        """
        if handle.state != ProcessState.IDLE:
            logger.debug(f"Skipping scale-down of {handle.id} - no longer idle")
            return

        if handle.id not in self.processes:
            return  # Already removed

        logger.info(f"Scale-down: terminating process {handle.id}")

        # Remove from processes dict
        del self.processes[handle.id]

        # Terminate the process
        await self._terminate_process(handle)

    def mark_for_recycle(self) -> tuple[int, list[ProcessHandle]]:
        """
        Mark all worker processes for recycling after their current execution.

        Called after package installation so workers pick up newly installed
        packages. Each process will be recycled (terminated + respawned) after
        completing its current execution. Returns list of idle processes to
        be recycled so caller can publish progress.

        Returns:
            Tuple of (total count, list of idle handles to recycle immediately)
        """
        idle_handles: list[ProcessHandle] = []
        marked_for_later = 0

        for handle in list(self.processes.values()):
            if handle.state == ProcessState.IDLE:
                # Idle process - mark for immediate recycle
                handle.pending_recycle = True
                idle_handles.append(handle)
            else:
                # Busy process - mark for recycle after current execution
                handle.pending_recycle = True
                marked_for_later += 1

        logger.info(
            f"Marked {len(self.processes)} processes for recycle "
            f"({len(idle_handles)} immediate, {marked_for_later} after execution)"
        )
        return len(self.processes), idle_handles

    async def _recycle_idle_process(self, handle: ProcessHandle) -> None:
        """Recycle an idle process immediately."""
        if handle.id not in self.processes:
            return  # Already removed
        if handle.state != ProcessState.IDLE:
            return  # No longer idle, will be recycled after execution

        logger.info(f"Recycling idle process {handle.id} (package install)")
        await self._recycle_process(handle)

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
                "pending_recycle": p.pending_recycle,
            }
            if p.current_execution:
                info["execution"] = {
                    "execution_id": p.current_execution.execution_id,
                    "started_at": p.current_execution.started_at.isoformat(),
                    "elapsed_seconds": p.current_execution.elapsed_seconds,
                }
            processes.append(info)

        idle_count = len([p for p in self.processes.values() if p.state == ProcessState.IDLE])
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
            min_workers=0,  # On-demand (JIT) mode — no warm pool
            max_workers=settings.max_workers,
            execution_timeout_seconds=settings.execution_timeout_seconds,
            graceful_shutdown_seconds=settings.graceful_shutdown_seconds,
            recycle_after_executions=settings.recycle_after_executions,
            recycle_memory_mb=settings.recycle_memory_mb,
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
