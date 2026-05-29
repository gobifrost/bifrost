"""
Unit tests for ProcessPoolManager (on-demand / one-shot workers).

Each route_execution call forks a fresh worker; the worker exits after
returning its single result. The pool's only throttles are
`max_workers` (concurrency cap) and the cgroup memory-pressure check.

NOTE: These tests use mocks to avoid spawning real processes.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.execution.process_pool import (
    ExecutionInfo,
    ProcessHandle,
    ProcessPoolManager,
    ProcessState,
)
from src.services.execution.simple_worker import (
    FailedPackage,
    RequirementsInstallResult,
)


class TestProcessState:
    """Tests for ProcessState enum."""

    def test_all_states_defined(self):
        """Should have all required states."""
        assert ProcessState.IDLE.value == "idle"
        assert ProcessState.BUSY.value == "busy"
        assert ProcessState.KILLED.value == "killed"

    def test_states_are_distinct(self):
        """All states should have unique values."""
        values = [state.value for state in ProcessState]
        assert len(values) == len(set(values))


class TestExecutionInfo:
    """Tests for ExecutionInfo dataclass."""

    def test_basic_creation(self):
        """Should create with required fields."""
        now = datetime.now(timezone.utc)

        info = ExecutionInfo(
            execution_id="exec-123",
            started_at=now,
            timeout_seconds=300,
        )

        assert info.execution_id == "exec-123"
        assert info.started_at == now
        assert info.timeout_seconds == 300

    def test_elapsed_seconds(self):
        """Should calculate elapsed time correctly."""
        past = datetime.now(timezone.utc) - timedelta(seconds=5)

        info = ExecutionInfo(
            execution_id="exec-123",
            started_at=past,
            timeout_seconds=300,
        )

        elapsed = info.elapsed_seconds
        assert 4.9 < elapsed < 6.0

    def test_is_timed_out_before_timeout(self):
        """Should return False when within timeout."""
        now = datetime.now(timezone.utc)

        info = ExecutionInfo(
            execution_id="exec-123",
            started_at=now,
            timeout_seconds=300,
        )

        assert info.is_timed_out is False

    def test_is_timed_out_after_timeout(self):
        """Should return True when exceeds timeout."""
        past = datetime.now(timezone.utc) - timedelta(seconds=10)

        info = ExecutionInfo(
            execution_id="exec-123",
            started_at=past,
            timeout_seconds=5,
        )

        assert info.is_timed_out is True


class TestProcessHandle:
    """Tests for ProcessHandle dataclass."""

    def test_basic_creation(self):
        """Should create with required fields."""
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        mock_work_queue = MagicMock()
        mock_result_queue = MagicMock()
        now = datetime.now(timezone.utc)

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=mock_work_queue,
            result_queue=mock_result_queue,
            started_at=now,
        )

        assert handle.id == "process-1"
        assert handle.pid == 12345
        assert handle.state == ProcessState.IDLE
        assert handle.current_execution is None
        assert handle.executions_completed == 0

    def test_is_alive_property(self):
        """Should delegate to process.is_alive()."""
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )

        assert handle.is_alive is True
        mock_process.is_alive.assert_called()

    def test_uptime_seconds(self):
        """Should calculate uptime correctly."""
        mock_process = MagicMock()
        past = datetime.now(timezone.utc) - timedelta(seconds=60)

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=past,
        )

        uptime = handle.uptime_seconds
        assert 59.0 < uptime < 61.0


class TestProcessPoolManagerInit:
    """Tests for ProcessPoolManager initialization."""

    def test_default_values(self):
        """Should initialize with default values."""
        pool = ProcessPoolManager()

        assert pool.max_workers == 10
        assert pool.execution_timeout_seconds == 300
        assert pool.graceful_shutdown_seconds == 5
        assert pool.heartbeat_interval_seconds == 10
        assert pool.registration_ttl_seconds == 30
        assert pool.on_result is None
        assert len(pool.processes) == 0
        assert pool._shutdown is False
        assert pool._started is False

    def test_custom_values(self):
        """Should accept custom values."""
        callback = AsyncMock()

        pool = ProcessPoolManager(
            max_workers=20,
            execution_timeout_seconds=600,
            graceful_shutdown_seconds=10,
            heartbeat_interval_seconds=30,
            registration_ttl_seconds=60,
            on_result=callback,
        )

        assert pool.max_workers == 20
        assert pool.execution_timeout_seconds == 600
        assert pool.graceful_shutdown_seconds == 10
        assert pool.heartbeat_interval_seconds == 30
        assert pool.registration_ttl_seconds == 60
        assert pool.on_result is callback


class TestProcessPoolManagerStart:
    """Tests for pool startup."""

    @pytest.mark.asyncio
    async def test_pool_starts_with_no_workers(self):
        """Start should boot the template but not pre-spawn any workers."""
        pool = ProcessPoolManager(max_workers=10)

        spawned: list[None] = []

        def mock_spawn():
            spawned.append(None)
            return MagicMock()

        pool._fork_process = mock_spawn

        with patch.object(pool, "_get_redis", new_callable=AsyncMock) as mock_redis:
            mock_redis.return_value = AsyncMock()
            with patch.object(pool, "_start_template", new_callable=AsyncMock), \
                 patch.object(pool, "_register_worker", new_callable=AsyncMock), \
                 patch.object(pool, "_monitor_loop", new_callable=AsyncMock), \
                 patch.object(pool, "_result_loop", new_callable=AsyncMock), \
                 patch.object(pool, "_heartbeat_loop", new_callable=AsyncMock), \
                 patch.object(pool, "_cancel_listener_loop", new_callable=AsyncMock), \
                 patch.object(pool, "_command_listener_loop", new_callable=AsyncMock), \
                 patch("src.services.execution.process_pool.install_requirements"):
                await pool.start()

        assert spawned == [], "start() must not pre-spawn workers in on-demand mode"
        assert len(pool.processes) == 0
        assert pool._started is True

        # Cleanup
        pool._shutdown = True
        for task in [pool._monitor_task, pool._result_task, pool._heartbeat_task,
                     pool._cancel_task, pool._command_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected — we just cancelled the task during cleanup
                    pass


class TestProcessPoolManagerRouting:
    """Tests for execution routing."""

    @pytest.mark.asyncio
    async def test_route_forks_fresh_worker(self):
        """Every route_execution call should fork a fresh one-shot worker."""
        pool = ProcessPoolManager(max_workers=5)

        forked: list[ProcessHandle] = []

        def mock_spawn():
            new_process = MagicMock()
            new_process.is_alive.return_value = True
            new_process.pid = 12346 + len(forked)
            new_work_queue = MagicMock()
            new_handle = ProcessHandle(
                id=f"process-{len(forked) + 1}",
                process=new_process,
                pid=new_process.pid,
                state=ProcessState.BUSY,
                work_queue=new_work_queue,
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc),
            )
            pool.processes[new_handle.id] = new_handle
            forked.append(new_handle)
            return new_handle

        pool._fork_process = mock_spawn

        with patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock), \
             patch("src.services.execution.process_pool.has_sufficient_memory_cgroup", return_value=True):
            await pool.route_execution("exec-123", {"timeout_seconds": 300})

        assert len(forked) == 1
        h = forked[0]
        assert h.state == ProcessState.BUSY
        assert h.current_execution is not None
        assert h.current_execution.execution_id == "exec-123"
        h.work_queue.put_nowait.assert_called_once_with("exec-123")

    @pytest.mark.asyncio
    async def test_route_waits_for_slot_when_saturated(self):
        """When at max_workers, route_execution should wait on the slot condition."""
        pool = ProcessPoolManager(max_workers=1)

        # Fill the pool with one busy handle (NOT via _fork_process)
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        existing = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )
        pool.processes["process-1"] = existing

        # Track fork
        forked: list[ProcessHandle] = []

        def mock_spawn():
            new_process = MagicMock()
            new_process.is_alive.return_value = True
            new_handle = ProcessHandle(
                id="process-2",
                process=new_process,
                pid=12346,
                state=ProcessState.BUSY,
                work_queue=MagicMock(),
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc),
            )
            pool.processes[new_handle.id] = new_handle
            forked.append(new_handle)
            return new_handle

        pool._fork_process = mock_spawn

        with patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock), \
             patch("src.services.execution.process_pool.has_sufficient_memory_cgroup", return_value=True):
            # Kick off the route — it should park on _slot_condition.
            route_task = asyncio.create_task(
                pool.route_execution("exec-456", {"timeout_seconds": 300})
            )
            await asyncio.sleep(0.1)
            assert not route_task.done(), "route should be parked while pool full"
            assert not forked, "no fork until a slot opens"

            # Free a slot
            del pool.processes["process-1"]
            await pool._notify_slot_free()

            await asyncio.wait_for(route_task, timeout=2.0)

        assert len(forked) == 1
        assert forked[0].current_execution is not None
        assert forked[0].current_execution.execution_id == "exec-456"


class TestProcessPoolManagerTimeouts:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        """Should kill process when execution times out."""
        pool = ProcessPoolManager(max_workers=10)

        # Create a busy process with timed-out execution
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True

        timed_out_execution = ExecutionInfo(
            execution_id="exec-timeout",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=400),
            timeout_seconds=300,
        )

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=timed_out_execution,
        )
        pool.processes["process-1"] = handle

        # Track callbacks
        killed = False
        timeout_reported = False
        spawned = False

        async def mock_kill(h: ProcessHandle) -> None:
            nonlocal killed
            killed = True
            h.state = ProcessState.KILLED

        async def mock_report_timeout(h: ProcessHandle) -> None:
            nonlocal timeout_reported
            timeout_reported = True

        def mock_spawn() -> ProcessHandle:
            nonlocal spawned
            spawned = True
            raise AssertionError("timeout must not spawn replacements in on-demand mode")

        pool._kill_process = mock_kill
        pool._report_timeout = mock_report_timeout
        pool._fork_process = mock_spawn

        await pool._check_timeouts()

        assert killed is True
        assert timeout_reported is True
        assert "process-1" not in pool.processes
        # One-shot mode: timeout cleanup does not spawn a replacement;
        # the next route_execution will fork a fresh worker on demand.
        assert spawned is False


class TestProcessPoolManagerCrashDetection:
    """Tests for crash detection."""

    @pytest.mark.asyncio
    async def test_crash_detection_removes_handle(self):
        """Should detect a crashed process, fire the crash callback, and remove the handle.

        One-shot workers are not replaced here — the next execution's
        route_execution will fork on demand.
        """
        pool = ProcessPoolManager(max_workers=10)

        # Create a crashed process
        mock_process = MagicMock()
        mock_process.is_alive.return_value = False  # Crashed
        mock_process.exitcode = -9  # SIGKILL

        crashed_execution = ExecutionInfo(
            execution_id="exec-crash",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            timeout_seconds=300,
        )

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=crashed_execution,
        )
        pool.processes["process-1"] = handle

        crash_reported = False
        spawn_count = 0

        async def mock_report_crash(h: ProcessHandle) -> None:
            nonlocal crash_reported
            crash_reported = True

        def mock_spawn() -> ProcessHandle:
            nonlocal spawn_count
            spawn_count += 1
            raise AssertionError("crash detection must not spawn replacements")

        pool._report_crash = mock_report_crash
        pool._fork_process = mock_spawn

        await pool._check_process_health()

        assert crash_reported is True
        assert "process-1" not in pool.processes
        assert spawn_count == 0


class TestProcessPoolManagerHeartbeat:
    """Tests for heartbeat functionality."""

    def test_build_heartbeat(self):
        """Should build heartbeat with process state."""
        pool = ProcessPoolManager()
        pool.worker_id = "test-worker-123"

        # Create processes in different states
        idle_process = MagicMock()
        idle_process.is_alive.return_value = True

        busy_process = MagicMock()
        busy_process.is_alive.return_value = True

        pool.processes["process-1"] = ProcessHandle(
            id="process-1",
            process=idle_process,
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            executions_completed=5,
        )

        pool.processes["process-2"] = ProcessHandle(
            id="process-2",
            process=busy_process,
            pid=12346,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-busy",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
            executions_completed=10,
        )

        heartbeat = pool._build_heartbeat()

        assert heartbeat["type"] == "worker_heartbeat"
        assert heartbeat["worker_id"] == "test-worker-123"
        assert heartbeat["pool_size"] == 2
        # In on-demand mode every running handle is BUSY (the IDLE-marked
        # handle above is for test-shape parity with persistent-pool
        # heartbeats; idle_count is reported as 0 in this mode).
        assert heartbeat["idle_count"] == 0
        assert heartbeat["busy_count"] == 1
        assert len(heartbeat["processes"]) == 2

        # Find busy process info
        busy_info = next(p for p in heartbeat["processes"] if p["process_id"] == "process-2")
        assert busy_info["state"] == "busy"
        assert "execution" in busy_info
        assert busy_info["execution"]["execution_id"] == "exec-busy"


class TestProcessPoolManagerResultHandling:
    """Tests for result handling."""

    @pytest.mark.asyncio
    async def test_handle_result_removes_handle(self):
        """Should remove the handle (one-shot worker) after result."""
        pool = ProcessPoolManager()

        mock_process = MagicMock()
        mock_process.is_alive.return_value = True

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-123",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
        )
        pool.processes["process-1"] = handle

        result_data = {
            "type": "result",
            "execution_id": "exec-123",
            "success": True,
            "result": {"data": "value"},
        }

        await pool._handle_result(handle, result_data)

        assert "process-1" not in pool.processes
        assert handle.current_execution is None
        assert handle.executions_completed == 1
        assert handle.result_reported is True

    @pytest.mark.asyncio
    async def test_handle_result_notifies_slot_waiters(self):
        """Removing a handle should wake any tasks waiting on a slot."""
        pool = ProcessPoolManager(max_workers=1)

        mock_process = MagicMock()
        mock_process.is_alive.return_value = True

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-123",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
        )
        pool.processes["process-1"] = handle

        # Spawn a waiter — pool is full so it parks on _slot_condition.
        waiter = asyncio.create_task(pool._wait_for_slot(timeout=5.0))
        await asyncio.sleep(0.1)
        assert not waiter.done(), "waiter should be parked"

        # Result handling deletes the handle and notifies — waiter should wake.
        await pool._handle_result(handle, {"success": True})
        got_slot = await asyncio.wait_for(waiter, timeout=1.0)
        assert got_slot is True

    @pytest.mark.asyncio
    async def test_handle_result_calls_callback(self):
        """Should forward result to callback."""
        callback = AsyncMock()
        pool = ProcessPoolManager(on_result=callback)

        mock_process = MagicMock()
        mock_process.is_alive.return_value = True

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-123",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
        )
        pool.processes["process-1"] = handle

        result_data = {
            "type": "result",
            "execution_id": "exec-123",
            "success": True,
            "result": {"data": "value"},
        }

        await pool._handle_result(handle, result_data)

        callback.assert_called_once_with(result_data)


class TestProcessPoolManagerStatus:
    """Tests for status reporting."""

    def test_get_status(self):
        """Should return current pool status."""
        pool = ProcessPoolManager(max_workers=10)
        pool._started = True
        pool.worker_id = "test-worker"

        mock_process = MagicMock()
        mock_process.is_alive.return_value = True

        pool.processes["process-1"] = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            executions_completed=5,
        )

        status = pool.get_status()

        assert status["started"] is True
        assert status["shutdown"] is False
        assert status["worker_id"] == "test-worker"
        assert status["pool_size"] == 1
        assert len(status["processes"]) == 1
        assert status["processes"][0]["process_id"] == "process-1"
        assert status["processes"][0]["state"] == "idle"


class TestProcessPoolManagerIntegration:
    """Integration tests for full workflows."""

    @pytest.mark.asyncio
    async def test_full_execution_cycle(self):
        """Test routing and completing an execution (one-shot fork → result)."""
        callback = AsyncMock()
        pool = ProcessPoolManager(on_result=callback)

        # Mock the fork — route_execution will create a fresh BUSY handle.
        mock_work_queue = MagicMock()

        def mock_spawn() -> ProcessHandle:
            mock_process = MagicMock()
            mock_process.is_alive.return_value = True
            new_handle = ProcessHandle(
                id="process-1",
                process=mock_process,
                pid=12345,
                state=ProcessState.BUSY,
                work_queue=mock_work_queue,
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc),
            )
            pool.processes[new_handle.id] = new_handle
            return new_handle

        pool._fork_process = mock_spawn

        with patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock), \
             patch("src.services.execution.process_pool.has_sufficient_memory_cgroup", return_value=True):
            await pool.route_execution("exec-123", {"timeout_seconds": 300})

        handle = pool.processes["process-1"]
        assert handle.state == ProcessState.BUSY
        mock_work_queue.put_nowait.assert_called_once_with("exec-123")

        result_data = {
            "type": "result",
            "execution_id": "exec-123",
            "success": True,
            "result": {"output": "done"},
        }

        await pool._handle_result(handle, result_data)

        # One-shot worker: handle is removed after completion.
        assert "process-1" not in pool.processes
        callback.assert_called_once_with(result_data)


class TestAdmissionControl:
    """Tests for cgroup-based admission control."""

    @pytest.mark.asyncio
    async def test_route_execution_checks_memory_pressure(self):
        """Should reject execution when memory pressure is too high."""
        pool = ProcessPoolManager(max_workers=5)
        pool._started = True

        with patch(
            "src.services.execution.process_pool.has_sufficient_memory_cgroup",
            return_value=False,
        ):
            with patch.object(pool, '_write_context_to_redis', new_callable=AsyncMock):
                with pytest.raises(MemoryError, match="memory pressure"):
                    await pool.route_execution("exec-123", {"timeout_seconds": 300})

    @pytest.mark.asyncio
    async def test_route_execution_allows_when_memory_ok(self):
        """Should allow execution when memory is within threshold."""
        pool = ProcessPoolManager(max_workers=5)
        pool._started = True

        mock_handle = ProcessHandle(
            id="process-1",
            process=MagicMock(is_alive=MagicMock(return_value=True)),
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )

        def mock_spawn():
            pool.processes[mock_handle.id] = mock_handle
            return mock_handle

        with patch(
            "src.services.execution.process_pool.has_sufficient_memory_cgroup",
            return_value=True,
        ):
            with patch.object(pool, '_write_context_to_redis', new_callable=AsyncMock):
                with patch.object(pool, '_fork_process', side_effect=mock_spawn):
                    await pool.route_execution("exec-123", {"timeout_seconds": 300})
                    assert mock_handle.state == ProcessState.BUSY
                    assert mock_handle.current_execution is not None
                    assert mock_handle.current_execution.execution_id == "exec-123"


class TestOrphanedKilledHandleSweep:
    """Tests for _check_process_health sweeping orphaned KILLED handles."""

    @pytest.mark.asyncio
    async def test_check_process_health_sweeps_orphaned_killed_handles(self):
        """A handle whose state is KILLED but result_reported=False should
        have a synthetic orphan callback fired so the DB doesn't sit orphaned."""
        callback = AsyncMock()
        pool = ProcessPoolManager(max_workers=5, on_result=callback)

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = -9

        exec_info = ExecutionInfo(
            execution_id="exec-orphan-123",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            timeout_seconds=300,
        )

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.KILLED,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=exec_info,
            result_reported=False,
            killed_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        pool.processes["process-1"] = handle

        await pool._check_process_health()

        # Orphan callback must have fired exactly once
        callback.assert_awaited_once()
        call_args = callback.call_args[0][0]
        assert call_args["success"] is False
        assert call_args["error_type"] == "OrphanedExecution"
        assert call_args["execution_id"] == "exec-orphan-123"

        # Handle must be removed from pool
        assert "process-1" not in pool.processes

    @pytest.mark.asyncio
    async def test_check_process_health_orphan_idempotent_when_already_reported(self):
        """If result_reported is already True, the orphan callback must NOT fire
        even when state is KILLED."""
        callback = AsyncMock()
        pool = ProcessPoolManager(max_workers=5, on_result=callback)

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = -9

        exec_info = ExecutionInfo(
            execution_id="exec-already-reported",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            timeout_seconds=300,
        )

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.KILLED,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=exec_info,
            result_reported=True,
            killed_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        pool.processes["process-1"] = handle

        await pool._check_process_health()

        # Callback must not fire again
        callback.assert_not_awaited()

        # Handle still removed from pool (cleanup still happens)
        assert "process-1" not in pool.processes


    @pytest.mark.asyncio
    async def test_check_process_health_does_not_race_with_kill_grace_sleep(self):
        """
        During _kill_process's grace-sleep window, _check_process_health must NOT
        fire an orphan callback. The cancel/timeout path is about to report
        legitimately; orphan-sweeping now would duplicate it.
        """
        callback = AsyncMock()
        pool = ProcessPoolManager(
            max_workers=1, graceful_shutdown_seconds=5,
            on_result=callback,
        )
        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        handle = ProcessHandle(
            id="proc-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.KILLED,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-mid-cancel",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
            result_reported=False,
            killed_at=datetime.now(timezone.utc),  # JUST killed — inside grace window
        )
        pool.processes["proc-1"] = handle

        await pool._check_process_health()

        # The in-flight cancel will report; we must not duplicate
        callback.assert_not_awaited()
        # Handle still in pool — not orphan-removed yet
        assert "proc-1" in pool.processes


class TestCrashedProcessReport:
    """Tests that a SIGKILLed worker with an in-flight execution gets reported immediately.

    Conceptual note: a SIGKILL on a BUSY worker triggers Case A in
    _check_process_health (state != KILLED, is_alive == False), which calls
    _report_crash. This is the *crash* path, not the orphan path. The orphan
    path (Case B) covers KILLED-state handles where the cancel/timeout handler
    crashed before it could fire the result callback — that is covered by
    TestOrphanedKilledHandleSweep. This class proves the crash path fires the
    on_result callback immediately (no waiting) and sets result_reported=True so
    the reaper-of-last-resort never double-fires it.
    """

    @pytest.mark.asyncio
    async def test_sigkilled_worker_crashes_get_reported_within_seconds(self):
        """A BUSY worker whose process died unexpectedly (e.g. SIGKILL) should
        have on_result called with success=False and error_type='ProcessCrashError'
        synchronously within _check_process_health — no reaper wait needed."""
        callback = AsyncMock()
        pool = ProcessPoolManager(max_workers=5, on_result=callback)

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False  # process is dead
        mock_process.exitcode = -9  # SIGKILL

        exec_info = ExecutionInfo(
            execution_id="exec-sigkill-789",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=3),
            timeout_seconds=300,
        )

        handle = ProcessHandle(
            id="process-sigkill",
            process=mock_process,
            pid=99999,
            state=ProcessState.BUSY,  # still BUSY — crash was unexpected
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=exec_info,
            result_reported=False,
        )
        pool.processes["process-sigkill"] = handle

        await pool._check_process_health()

        # Callback must fire exactly once with the crash payload
        callback.assert_awaited_once()
        call_args = callback.call_args[0][0]
        assert call_args["success"] is False
        assert call_args["error_type"] == "ProcessCrashError"
        assert call_args["execution_id"] == "exec-sigkill-789"

        # Handle must be removed from the pool
        assert "process-sigkill" not in pool.processes

        # result_reported must be True — reaper-of-last-resort must not re-fire
        assert handle.result_reported is True

    @pytest.mark.asyncio
    async def test_sigkilled_worker_already_reported_does_not_double_fire(self):
        """If a crashed BUSY worker already had result_reported=True (rare race
        where the result came back on the queue before the health check ran),
        the crash callback must NOT fire again."""
        callback = AsyncMock()
        pool = ProcessPoolManager(max_workers=5, on_result=callback)

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = -9

        exec_info = ExecutionInfo(
            execution_id="exec-already-done",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            timeout_seconds=300,
        )

        handle = ProcessHandle(
            id="process-sigkill-2",
            process=mock_process,
            pid=99998,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=exec_info,
            result_reported=True,  # already reported
        )
        pool.processes["process-sigkill-2"] = handle

        await pool._check_process_health()

        # Callback must not fire — already reported
        callback.assert_not_awaited()

        # Handle still removed from pool (cleanup still happens)
        assert "process-sigkill-2" not in pool.processes


# ---------------------------------------------------------------------------
# _notify_requirements_failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_install_notifies_admins():
    """A result with failed packages creates a deduped admin notification."""
    from src.services.execution.process_pool import _notify_requirements_failures

    result = RequirementsInstallResult(
        attempted=["anthropic", "xhtml2pdf"],
        installed=["anthropic"],
        failed=[FailedPackage(package="xhtml2pdf", error="Unknown compiler(s): cc")],
    )

    svc = AsyncMock()
    svc.find_admin_notification_by_title.return_value = None  # no existing dup
    with patch(
        "src.services.execution.process_pool.get_notification_service",
        return_value=svc,
    ):
        await _notify_requirements_failures(result)

    svc.create_notification.assert_awaited_once()
    kwargs = svc.create_notification.await_args.kwargs
    assert kwargs["for_admins"] is True
    assert kwargs["user_id"] == "system"
    assert "xhtml2pdf" in kwargs["request"].description


@pytest.mark.asyncio
async def test_failed_install_dedups_existing_notification():
    from src.services.execution.process_pool import _notify_requirements_failures

    result = RequirementsInstallResult(
        attempted=["xhtml2pdf"],
        installed=[],
        failed=[FailedPackage(package="xhtml2pdf", error="boom")],
    )
    svc = AsyncMock()
    svc.find_admin_notification_by_title.return_value = object()  # dup exists
    with patch(
        "src.services.execution.process_pool.get_notification_service",
        return_value=svc,
    ):
        await _notify_requirements_failures(result)

    svc.create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_install_does_not_notify():
    from src.services.execution.process_pool import _notify_requirements_failures

    result = RequirementsInstallResult(
        attempted=["anthropic"], installed=["anthropic"], failed=[]
    )
    with patch(
        "src.services.execution.process_pool.get_notification_service",
    ) as get_svc:
        await _notify_requirements_failures(result)

    get_svc.assert_not_called()


class TestBurstRaceRegression:
    """Regression tests for issue #316 follow-up:

    Under concurrent burst load, _check_process_health, _check_timeouts, and
    _handle_cancel_request all hold a handle reference across an await
    (_report_crash / _kill_process / _report_cancellation) and then issue an
    unconditional `del self.processes[id]`. If a peer coroutine
    (_handle_result, another health-check pass, a cancellation) removes the
    same id during that await, the `del` raises KeyError and aborts the
    cleanup before `_notify_slot_free()` runs — leaving slot waiters parked
    until the 30s _wait_for_slot timeout.
    """

    @pytest.mark.asyncio
    async def test_check_process_health_concurrent_delete_does_not_raise(self):
        """A concurrent _handle_result for the same id must not break
        _check_process_health's cleanup loop with KeyError / RuntimeError.

        Reporter symptom on prod (`KeyError: 'process-N'`) matches the post-
        iteration `del self.processes[process_id]` path at line 1164 when a
        peer coroutine (result loop, cancel handler) deleted the same id
        during the `_report_crash` await mid-iteration.
        """
        crash_started = asyncio.Event()
        crash_release = asyncio.Event()

        async def slow_report_crash(_h):
            crash_started.set()
            await crash_release.wait()

        pool = ProcessPoolManager(max_workers=5)
        # Patch _report_crash so we get a deterministic suspension point
        # without needing on_result (whose await is what trips the race in
        # prod, but is equivalent to suspending inside _report_crash here).
        pool._report_crash = slow_report_crash  # type: ignore[method-assign]

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = -9

        exec_info = ExecutionInfo(
            execution_id="exec-race",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            timeout_seconds=300,
        )
        handle = ProcessHandle(
            id="process-race",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=exec_info,
            result_reported=False,
        )
        pool.processes["process-race"] = handle

        health_task = asyncio.create_task(pool._check_process_health())
        await crash_started.wait()

        # Simulate _handle_result deleting the id during the await.
        del pool.processes["process-race"]

        crash_release.set()

        # Must complete without raising. Pre-fix this raises either
        # `KeyError` (at the post-iteration `del`) or `RuntimeError:
        # dictionary changed size during iteration` (at the iterator
        # advance after the await).
        await asyncio.wait_for(health_task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_check_process_health_notifies_waiters_even_on_concurrent_delete(self):
        """If a concurrent delete races the cleanup loop, slot waiters must
        still be notified — otherwise route_execution parks for 30s.

        Mirrors the reporter's `No worker slot available after timeout` at
        30s on a 30-burst test: the KeyError aborted cleanup so the
        `_notify_slot_free()` after the cleanup loop never ran, leaving
        `_wait_for_slot` parked until its own 30s timeout.
        """
        crash_started = asyncio.Event()
        crash_release = asyncio.Event()

        async def slow_report_crash(_h):
            crash_started.set()
            await crash_release.wait()

        pool = ProcessPoolManager(max_workers=1)
        pool._report_crash = slow_report_crash  # type: ignore[method-assign]

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = -9

        exec_info = ExecutionInfo(
            execution_id="exec-race-2",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            timeout_seconds=300,
        )
        handle = ProcessHandle(
            id="process-race-2",
            process=mock_process,
            pid=12346,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=exec_info,
            result_reported=False,
        )
        pool.processes["process-race-2"] = handle

        waiter = asyncio.create_task(pool._wait_for_slot(timeout=2.0))
        await asyncio.sleep(0.05)
        assert not waiter.done(), "waiter must be parked while pool is full"

        health_task = asyncio.create_task(pool._check_process_health())
        await crash_started.wait()

        # Race: peer deletes the id during _report_crash's await. In real
        # code, the peer is _handle_result which ALSO notifies on its own
        # removal — simulate that complete behavior here. The cleanup loop
        # must not crash; the waiter must wake from one of the notifies.
        pool.processes.pop("process-race-2", None)
        await pool._notify_slot_free()

        crash_release.set()
        await asyncio.wait_for(health_task, timeout=2.0)

        got_slot = await asyncio.wait_for(waiter, timeout=1.0)
        assert got_slot is True, (
            "waiter never woke — _notify_slot_free was skipped because "
            "cleanup aborted with KeyError"
        )
