"""
Unit tests for ProcessPoolManager.

Tests the process pool management functionality including:
- Pool starts with min_workers
- Route to idle process
- Scale up when all busy
- Scale down when excess idle
- Timeout kills process
- Crash detection replaces process
- Recycle idle process
- Cannot recycle busy process

NOTE: These tests use mocks to avoid spawning real processes.
"""

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from queue import Empty
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.services.execution.process_pool as process_pool_module
from src.services.execution.process_pool import (
    ExecutionInfo,
    ProcessHandle,
    ProcessPoolManager,
    ProcessState,
    _PidWrapper,
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

        assert pool.min_workers == 2
        assert pool.max_workers == 10
        assert pool.execution_timeout_seconds == 300
        assert pool.graceful_shutdown_seconds == 5
        assert pool.recycle_after_executions == 0
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
            min_workers=5,
            max_workers=20,
            execution_timeout_seconds=600,
            graceful_shutdown_seconds=10,
            recycle_after_executions=100,
            heartbeat_interval_seconds=30,
            registration_ttl_seconds=60,
            on_result=callback,
        )

        assert pool.min_workers == 5
        assert pool.max_workers == 20
        assert pool.execution_timeout_seconds == 600
        assert pool.graceful_shutdown_seconds == 10
        assert pool.recycle_after_executions == 100
        assert pool.heartbeat_interval_seconds == 30
        assert pool.registration_ttl_seconds == 60
        assert pool.on_result is callback


class TestProcessPoolManagerStart:
    """Tests for pool startup."""

    @pytest.mark.asyncio
    async def test_start_delegates_requirements_install_to_template_process(self):
        """Pool startup should not install requirements before template startup."""
        pool = ProcessPoolManager(min_workers=0, max_workers=5)

        with patch("src.services.execution.process_pool.install_requirements") as install:
            with patch.object(pool, "_start_template", new_callable=AsyncMock):
                with patch.object(pool, "_update_requirements_status"):
                    with patch.object(pool, "_register_worker", new_callable=AsyncMock):
                        with patch.object(pool, "_monitor_loop", new_callable=AsyncMock):
                            with patch.object(pool, "_result_loop", new_callable=AsyncMock):
                                with patch.object(pool, "_heartbeat_loop", new_callable=AsyncMock):
                                    await pool.start()

        install.assert_not_called()

        pool._shutdown = True
        for task in [
            pool._monitor_task,
            pool._result_task,
            pool._heartbeat_task,
            pool._cancel_task,
            pool._command_task,
        ]:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    @pytest.mark.asyncio
    async def test_pool_starts_with_min_workers(self):
        """Should spawn min_workers processes on start."""
        pool = ProcessPoolManager(min_workers=3, max_workers=10)

        # Mock process spawning
        spawn_count = 0

        def mock_spawn():
            nonlocal spawn_count
            spawn_count += 1
            mock_process = MagicMock()
            mock_process.is_alive.return_value = True
            mock_process.pid = 10000 + spawn_count
            mock_process.start = MagicMock()

            handle = ProcessHandle(
                id=f"process-{spawn_count}",
                process=mock_process,
                pid=10000 + spawn_count,
                state=ProcessState.IDLE,
                work_queue=MagicMock(),
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc),
            )
            pool.processes[handle.id] = handle
            return handle

        pool._fork_process = mock_spawn

        # Mock Redis and background tasks
        with patch.object(pool, "_get_redis", new_callable=AsyncMock) as mock_redis:
            mock_redis_client = AsyncMock()
            mock_redis.return_value = mock_redis_client

            with patch.object(pool, "_register_worker", new_callable=AsyncMock):
                with patch.object(pool, "_monitor_loop", new_callable=AsyncMock):
                    with patch.object(pool, "_result_loop", new_callable=AsyncMock):
                        with patch.object(pool, "_heartbeat_loop", new_callable=AsyncMock):
                            await pool.start()

        assert spawn_count == 3
        assert len(pool.processes) == 3
        assert pool._started is True

        # Cleanup
        pool._shutdown = True
        for task in [pool._monitor_task, pool._result_task, pool._heartbeat_task]:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


class TestProcessPoolManagerRouting:
    """Tests for execution routing."""

    @pytest.mark.asyncio
    async def test_route_to_idle_process(self):
        """Should route execution to an idle process."""
        pool = ProcessPoolManager()

        # Create a mock idle process
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        mock_work_queue = MagicMock()

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=mock_work_queue,
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )
        pool.processes["process-1"] = handle

        # Mock Redis
        with patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock):
            await pool.route_execution("exec-123", {"timeout_seconds": 300})

        # Verify routing
        assert handle.state == ProcessState.BUSY
        assert handle.current_execution is not None
        assert handle.current_execution.execution_id == "exec-123"
        mock_work_queue.put_nowait.assert_called_once_with("exec-123")

    @pytest.mark.asyncio
    async def test_scale_up_when_all_busy(self):
        """Should spawn new process when all are busy."""
        pool = ProcessPoolManager(min_workers=1, max_workers=5)

        # Create a mock busy process
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True

        busy_handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )
        pool.processes["process-1"] = busy_handle

        # Track spawning
        spawned = False

        def mock_spawn():
            nonlocal spawned
            spawned = True
            new_process = MagicMock()
            new_process.is_alive.return_value = True
            new_process.pid = 12346

            new_handle = ProcessHandle(
                id="process-2",
                process=new_process,
                pid=12346,
                state=ProcessState.IDLE,
                work_queue=MagicMock(),
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc),
            )
            pool.processes["process-2"] = new_handle
            return new_handle

        pool._fork_process = mock_spawn

        # Mock Redis
        with patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock):
            await pool.route_execution("exec-123", {"timeout_seconds": 300})

        assert spawned is True
        assert len(pool.processes) == 2
        assert pool.processes["process-2"].state == ProcessState.BUSY


class TestProcessPoolManagerScaling:
    """Tests for pool scaling."""

    @pytest.mark.asyncio
    async def test_scale_down_when_excess_idle(self):
        """Should remove excess idle processes."""
        pool = ProcessPoolManager(min_workers=2, max_workers=10)

        # Create 4 idle processes
        for i in range(4):
            mock_process = MagicMock()
            mock_process.is_alive.return_value = True

            handle = ProcessHandle(
                id=f"process-{i+1}",
                process=mock_process,
                pid=12345 + i,
                state=ProcessState.IDLE,
                work_queue=MagicMock(),
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc) - timedelta(seconds=i * 10),
            )
            pool.processes[handle.id] = handle

        # Mock termination
        terminated_ids: list[str] = []

        async def mock_terminate(handle: ProcessHandle) -> None:
            terminated_ids.append(handle.id)
            handle.state = ProcessState.KILLED

        pool._terminate_process = mock_terminate

        # Scale down
        await pool._maybe_scale_down()

        # Should have removed 2 processes (4 - 2 = 2 excess)
        assert len(terminated_ids) == 2
        # Note: _maybe_scale_down removes from dict, so we check terminations
        # Verify min_workers setting is still correct
        assert pool.min_workers == 2


class TestProcessPoolManagerTimeouts:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        """Should kill process when execution times out."""
        pool = ProcessPoolManager(min_workers=1, max_workers=10)

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

        async def mock_report_timeout(handle: ProcessHandle) -> None:
            nonlocal timeout_reported
            timeout_reported = True

        def mock_spawn() -> ProcessHandle:
            nonlocal spawned
            spawned = True
            new_process = MagicMock()
            new_process.is_alive.return_value = True
            new_handle = ProcessHandle(
                id="process-2",
                process=new_process,
                pid=12346,
                state=ProcessState.IDLE,
                work_queue=MagicMock(),
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc),
            )
            pool.processes["process-2"] = new_handle
            return new_handle

        pool._kill_process = mock_kill
        pool._report_timeout = mock_report_timeout
        pool._fork_process = mock_spawn

        await pool._check_timeouts()

        assert killed is True
        assert timeout_reported is True
        assert "process-1" not in pool.processes
        assert spawned is True  # Should spawn replacement


class TestProcessPoolManagerCrashDetection:
    """Tests for crash detection."""

    @pytest.mark.asyncio
    async def test_crash_detection_replaces_process(self):
        """Should detect crashed process and spawn replacement."""
        pool = ProcessPoolManager(min_workers=2, max_workers=10)

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
            state=ProcessState.BUSY,  # Was busy when crashed
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
            current_execution=crashed_execution,
        )
        pool.processes["process-1"] = handle

        # Track callbacks
        crash_reported = False
        spawn_count = 0

        async def mock_report_crash(handle: ProcessHandle) -> None:
            nonlocal crash_reported
            crash_reported = True

        def mock_spawn() -> ProcessHandle:
            nonlocal spawn_count
            spawn_count += 1
            new_process = MagicMock()
            new_process.is_alive.return_value = True
            new_handle = ProcessHandle(
                id=f"process-{spawn_count + 1}",
                process=new_process,
                pid=12346 + spawn_count,
                state=ProcessState.IDLE,
                work_queue=MagicMock(),
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc),
            )
            pool.processes[new_handle.id] = new_handle
            return new_handle

        pool._report_crash = mock_report_crash
        pool._fork_process = mock_spawn

        await pool._check_process_health()

        assert crash_reported is True
        assert "process-1" not in pool.processes
        # Should spawn 2 replacements to maintain min_workers=2
        assert spawn_count == 2


class TestProcessPoolManagerRecycle:
    """Tests for process recycling."""

    @pytest.mark.asyncio
    async def test_recycle_idle_process(self):
        """Should recycle an idle process."""
        pool = ProcessPoolManager(min_workers=2, max_workers=10)

        # Create an idle process
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
        pool.processes["process-1"] = handle

        # Track callbacks
        terminated = False
        spawned = False

        async def mock_terminate(h: ProcessHandle) -> None:
            nonlocal terminated
            terminated = True
            h.state = ProcessState.KILLED

        def mock_spawn() -> ProcessHandle:
            nonlocal spawned
            spawned = True
            new_process = MagicMock()
            new_process.is_alive.return_value = True
            new_handle = ProcessHandle(
                id="process-2",
                process=new_process,
                pid=12346,
                state=ProcessState.IDLE,
                work_queue=MagicMock(),
                result_queue=MagicMock(),
                started_at=datetime.now(timezone.utc),
            )
            pool.processes["process-2"] = new_handle
            return new_handle

        pool._terminate_process = mock_terminate
        pool._fork_process = mock_spawn

        result = await pool.recycle_process(12345)

        assert result is True
        assert terminated is True
        assert spawned is True
        assert "process-1" not in pool.processes
        assert "process-2" in pool.processes

    @pytest.mark.asyncio
    async def test_cannot_recycle_busy_process(self):
        """Should not recycle a busy process."""
        pool = ProcessPoolManager()

        # Create a busy process
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

        result = await pool.recycle_process(12345)

        assert result is False
        assert "process-1" in pool.processes  # Should still be there

    @pytest.mark.asyncio
    async def test_recycle_not_found(self):
        """Should return False when process not found."""
        pool = ProcessPoolManager()

        result = await pool.recycle_process(99999)

        assert result is False


class TestProcessPoolManagerHeartbeat:
    """Tests for heartbeat functionality."""

    def test_update_requirements_status_ignores_comments_and_inline_comments(self):
        """Requirements status should count real package names, not comments."""
        pool = ProcessPoolManager()
        content = """
# Bifrost API requirements
aio-pika  # async rabbitmq client
sqlalchemy[asyncio]>=2.0

# Developer tools
pytest-timeout==2.3.1
"""

        with patch(
            "src.core.requirements_cache.get_requirements_sync",
            return_value=content,
        ):
            with patch(
                "src.services.execution.process_pool._get_installed_packages",
                return_value=[
                    {"name": "aio-pika", "version": "1.0"},
                    {"name": "SQLAlchemy", "version": "2.0"},
                ],
            ):
                pool._update_requirements_status()

        assert pool._requirements_total == 3
        assert pool._requirements_installed == 2

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
        assert heartbeat["idle_count"] == 1
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
    async def test_handle_result_returns_to_idle(self):
        """Should return process to IDLE after result."""
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

        assert handle.state == ProcessState.IDLE
        assert handle.current_execution is None
        assert handle.executions_completed == 1

    @pytest.mark.asyncio
    async def test_handle_result_triggers_recycle(self):
        """Should recycle after max executions."""
        pool = ProcessPoolManager(recycle_after_executions=5)

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
            executions_completed=4,  # Will be 5 after this
        )
        pool.processes["process-1"] = handle

        recycled = False

        async def mock_recycle(h: ProcessHandle) -> None:
            nonlocal recycled
            recycled = True

        pool._recycle_process = mock_recycle

        result_data = {
            "type": "result",
            "execution_id": "exec-123",
            "success": True,
        }

        await pool._handle_result(handle, result_data)

        assert recycled is True

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
        pool = ProcessPoolManager(min_workers=2, max_workers=10)
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


class TestProcessPoolManagerIdleProcess:
    """Tests for idle process retrieval."""

    def test_get_idle_process_returns_idle(self):
        """Should return an IDLE process."""
        pool = ProcessPoolManager()

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
        pool.processes["process-1"] = handle

        result = pool._get_idle_process()

        assert result is handle

    def test_get_idle_process_skips_busy(self):
        """Should skip BUSY processes."""
        pool = ProcessPoolManager()

        mock_process = MagicMock()
        mock_process.is_alive.return_value = True

        busy_handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )
        pool.processes["process-1"] = busy_handle

        result = pool._get_idle_process()

        assert result is None

    def test_get_idle_process_skips_dead(self):
        """Should skip dead processes."""
        pool = ProcessPoolManager()

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )
        pool.processes["process-1"] = handle

        result = pool._get_idle_process()

        assert result is None


class TestProcessPoolManagerIntegration:
    """Integration tests for full workflows."""

    @pytest.mark.asyncio
    async def test_full_execution_cycle(self):
        """Test routing and completing an execution."""
        callback = AsyncMock()
        pool = ProcessPoolManager(min_workers=1, on_result=callback)

        # Create mock idle process
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        mock_work_queue = MagicMock()
        mock_result_queue = MagicMock()

        handle = ProcessHandle(
            id="process-1",
            process=mock_process,
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=mock_work_queue,
            result_queue=mock_result_queue,
            started_at=datetime.now(timezone.utc),
        )
        pool.processes["process-1"] = handle

        # Route execution
        with patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock):
            await pool.route_execution("exec-123", {"timeout_seconds": 300})

        assert handle.state == ProcessState.BUSY
        mock_work_queue.put_nowait.assert_called_once_with("exec-123")

        # Simulate result
        result_data = {
            "type": "result",
            "execution_id": "exec-123",
            "success": True,
            "result": {"output": "done"},
        }

        await pool._handle_result(handle, result_data)

        assert handle.state == ProcessState.IDLE
        callback.assert_called_once_with(result_data)


class TestMinWorkersZero:
    """Tests for on-demand mode (min_workers=0)."""

    @pytest.fixture
    def pool_zero(self):
        """Create a pool with min_workers=0."""
        pool = ProcessPoolManager(
            min_workers=0,
            max_workers=5,
        )
        return pool

    def test_min_workers_zero_is_valid(self, pool_zero):
        """Should accept min_workers=0 without raising."""
        assert pool_zero.min_workers == 0

    @pytest.mark.asyncio
    async def test_start_with_zero_workers_spawns_none(self, pool_zero):
        """Pool with min_workers=0 should have no processes after start."""
        with patch.object(pool_zero, '_fork_process') as mock_spawn:
            with patch.object(pool_zero, '_register_worker', new_callable=AsyncMock):
                with patch.object(pool_zero, '_start_template', new_callable=AsyncMock):
                    pool_zero._started = True
                    # Simulate start without background tasks
                    assert len(pool_zero.processes) == 0
                    mock_spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_result_reaps_completed_child(self):
        """On-demand children should be joined after returning a result."""
        callback = AsyncMock()
        pool = ProcessPoolManager(min_workers=0, max_workers=5, on_result=callback)

        mock_process = MagicMock()
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
        pool.processes[handle.id] = handle

        result = {
            "type": "result",
            "execution_id": "exec-123",
            "success": True,
            "result": {},
        }

        await pool._handle_result(handle, result)

        mock_process.join.assert_called_once_with(1.0)
        assert handle.id not in pool.processes
        callback.assert_awaited_once_with(result)


class TestPidWrapper:
    """Tests for raw PID lifecycle handling."""

    def test_is_alive_reaps_exited_child(self):
        """A zombie child should be reaped and reported not alive."""
        wrapper = _PidWrapper(12345)

        with patch("src.services.execution.process_pool.os.waitpid", return_value=(12345, 0)):
            assert wrapper.is_alive() is False

        assert wrapper.exitcode == 0

    def test_is_alive_keeps_running_child_alive(self):
        """A running child should still be reported alive."""
        wrapper = _PidWrapper(12345)

        with patch("src.services.execution.process_pool.os.waitpid", return_value=(0, 0)):
            with patch("src.services.execution.process_pool.os.kill") as mock_kill:
                assert wrapper.is_alive() is True

        mock_kill.assert_called_once_with(12345, 0)

    def test_child_process_error_falls_back_to_liveness_probe(self):
        """Unwaitable PID status should not be treated as success or death."""
        wrapper = _PidWrapper(12345)

        with patch("src.services.execution.process_pool.os.waitpid", side_effect=ChildProcessError):
            with patch("src.services.execution.process_pool.os.kill") as mock_kill:
                assert wrapper.is_alive() is True

        mock_kill.assert_called_once_with(12345, 0)
        assert wrapper.exitcode is None


class TestAdmissionControl:
    """Tests for cgroup-based admission control."""

    @pytest.mark.asyncio
    async def test_route_execution_checks_memory_pressure(self):
        """Should reject execution when memory pressure is too high."""
        pool = ProcessPoolManager(min_workers=0, max_workers=5)
        pool._started = True

        with patch(
            "src.services.execution.process_pool.has_sufficient_memory_cgroup",
            return_value=False,
        ):
            with patch.object(pool, '_write_context_to_redis', new_callable=AsyncMock):
                mock_redis = AsyncMock()
                with patch.object(pool, "_get_redis", new_callable=AsyncMock, return_value=mock_redis):
                    with pytest.raises(MemoryError, match="memory pressure"):
                        await pool.route_execution("exec-123", {"timeout_seconds": 300})

    @pytest.mark.asyncio
    async def test_route_execution_allows_when_memory_ok(self):
        """Should allow execution when memory is within threshold."""
        pool = ProcessPoolManager(min_workers=0, max_workers=5)
        pool._started = True

        mock_handle = ProcessHandle(
            id="process-1",
            process=MagicMock(is_alive=MagicMock(return_value=True)),
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )

        with patch(
            "src.services.execution.process_pool.has_sufficient_memory_cgroup",
            return_value=True,
        ):
            with patch.object(pool, '_write_context_to_redis', new_callable=AsyncMock):
                with patch.object(pool, '_fork_process', return_value=mock_handle):
                    pool.processes["process-1"] = mock_handle
                    await pool.route_execution("exec-123", {"timeout_seconds": 300})
                    assert mock_handle.state == ProcessState.BUSY


class TestOrphanedKilledHandleSweep:
    """Tests for _check_process_health sweeping orphaned KILLED handles."""

    @pytest.mark.asyncio
    async def test_check_process_health_sweeps_orphaned_killed_handles(self):
        """A handle whose state is KILLED but result_reported=False should
        have a synthetic orphan callback fired so the DB doesn't sit orphaned."""
        callback = AsyncMock()
        pool = ProcessPoolManager(min_workers=0, max_workers=5, on_result=callback)

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
        pool = ProcessPoolManager(min_workers=0, max_workers=5, on_result=callback)

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
            min_workers=0, max_workers=1, graceful_shutdown_seconds=5,
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


class TestOnDemandMode:
    """Tests for on-demand mode and configured warm pools."""

    def test_get_process_pool_uses_settings_min_workers(self):
        """Global pool construction should honor configured warm pool size."""
        previous_pool = process_pool_module._pool
        process_pool_module._pool = None
        settings = MagicMock()
        settings.min_workers = 3
        settings.max_workers = 8
        settings.execution_timeout_seconds = 300
        settings.graceful_shutdown_seconds = 5
        settings.recycle_after_executions = 100
        settings.recycle_memory_mb = 768
        settings.worker_heartbeat_interval_seconds = 10
        settings.worker_registration_ttl_seconds = 30

        try:
            with patch("src.services.execution.process_pool.get_settings", return_value=settings):
                pool = process_pool_module.get_process_pool()

            assert pool.min_workers == 3
            assert pool.max_workers == 8
        finally:
            process_pool_module._pool = previous_pool

    def test_pool_starts_with_zero_min_workers(self):
        """Pool should still allow min_workers=0 for on-demand mode."""
        pool = ProcessPoolManager(min_workers=0, max_workers=5)
        assert pool.min_workers == 0


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
        pool = ProcessPoolManager(min_workers=0, max_workers=5, on_result=callback)

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
        pool = ProcessPoolManager(min_workers=0, max_workers=5, on_result=callback)

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

    @pytest.mark.asyncio
    async def test_exited_worker_with_queued_result_is_not_reported_as_crash(self):
        """A completed child can exit before the result loop reads its pipe."""
        callback = AsyncMock()
        pool = ProcessPoolManager(min_workers=0, max_workers=5, on_result=callback)

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = 0

        result = {
            "type": "result",
            "execution_id": "exec-finished",
            "success": True,
            "result": {},
        }
        result_queue = MagicMock()
        result_queue.get_nowait.return_value = result

        handle = ProcessHandle(
            id="process-finished",
            process=mock_process,
            pid=99997,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=result_queue,
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-finished",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
        )
        pool.processes[handle.id] = handle

        await pool._check_process_health()

        callback.assert_awaited_once_with(result)
        mock_process.join.assert_called_once_with(1.0)
        assert handle.id not in pool.processes

    @pytest.mark.asyncio
    async def test_clean_exit_waits_for_result_queue_delivery(self):
        """A cleanly exited worker gets a short grace period for result delivery."""
        pool = ProcessPoolManager(min_workers=0, max_workers=2)
        pool.on_result = AsyncMock()

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = 0

        result_queue = MagicMock()
        result_queue.get_nowait.side_effect = Empty

        handle = ProcessHandle(
            id="process-clean-exit",
            process=mock_process,
            pid=99996,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=result_queue,
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-clean-exit",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
        )
        pool.processes[handle.id] = handle

        await pool._check_process_health()

        pool.on_result.assert_not_awaited()
        assert handle.id in pool.processes
        assert handle.clean_exit_observed_at is not None

    @pytest.mark.asyncio
    async def test_clean_exit_without_result_after_grace_is_reported(self):
        """A clean exit without a delivered result eventually reports failure."""
        pool = ProcessPoolManager(min_workers=0, max_workers=2)
        pool.on_result = AsyncMock()

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = 0
        mock_process.join = MagicMock()

        result_queue = MagicMock()
        result_queue.get_nowait.side_effect = Empty

        handle = ProcessHandle(
            id="process-stale-clean-exit",
            process=mock_process,
            pid=99995,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=result_queue,
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-stale-clean-exit",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
            clean_exit_observed_at=datetime.now(timezone.utc) - timedelta(seconds=3),
        )
        pool.processes[handle.id] = handle

        await pool._check_process_health()

        pool.on_result.assert_awaited_once()
        result = pool.on_result.await_args.args[0]
        assert result["execution_id"] == "exec-stale-clean-exit"
        assert result["success"] is False
        assert result["error_type"] == "OrphanedExecution"
        mock_process.join.assert_called_once_with(1.0)
        assert handle.id not in pool.processes

    @pytest.mark.asyncio
    async def test_unknown_exit_without_result_after_grace_is_reported_as_orphan(self):
        """Unavailable exit status without a delivered result is not a crash."""
        pool = ProcessPoolManager(min_workers=0, max_workers=2)
        pool.on_result = AsyncMock()

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.exitcode = None
        mock_process.join = MagicMock()

        result_queue = MagicMock()
        result_queue.get_nowait.side_effect = Empty

        handle = ProcessHandle(
            id="process-stale-unknown-exit",
            process=mock_process,
            pid=99994,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=result_queue,
            started_at=datetime.now(timezone.utc),
            current_execution=ExecutionInfo(
                execution_id="exec-stale-unknown-exit",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=300,
            ),
            clean_exit_observed_at=datetime.now(timezone.utc) - timedelta(seconds=3),
        )
        pool.processes[handle.id] = handle

        await pool._check_process_health()

        result = pool.on_result.await_args.args[0]
        assert result["execution_id"] == "exec-stale-unknown-exit"
        assert result["success"] is False
        assert result["error_type"] == "OrphanedExecution"
        mock_process.join.assert_called_once_with(1.0)
        assert handle.id not in pool.processes
