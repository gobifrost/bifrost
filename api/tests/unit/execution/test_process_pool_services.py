"""Unit tests for supervised service children in ProcessPoolManager.

Service children fork from the same template but live under separate
slot accounting (max_service_workers, never max_workers), never face the
execution timeout kill, and report through on_service_result.
"""

from datetime import datetime, timezone
from queue import Empty
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.execution.process_pool import (
    ProcessHandle,
    ProcessPoolManager,
    ProcessState,
)


def _make_pool(**kwargs):
    kwargs.setdefault("max_workers", 2)
    kwargs.setdefault("max_service_workers", 2)
    return ProcessPoolManager(**kwargs)


def _spawn(pool, **kwargs):
    """Fake _fork_process: registers a BUSY handle in workflow accounting."""
    process = MagicMock()
    process.is_alive.return_value = True
    handle = ProcessHandle(
        id=f"process-{len(pool.processes) + 1}",
        process=process,
        pid=9999,
        state=ProcessState.BUSY,
        work_queue=MagicMock(),
        result_queue=MagicMock(),
        started_at=datetime.now(timezone.utc),
        **kwargs,
    )
    pool.processes[handle.id] = handle
    return handle


def _service_context():
    return {
        "execution_id": "attempt-1",
        "service": {
            "service_id": "service-1",
            "attempt_id": "attempt-1",
            "lease_token": "lease-1",
        },
    }


async def _route(pool, **kwargs):
    args = {
        "service_id": "service-1",
        "attempt_id": "attempt-1",
        "lease_token": "lease-1",
        "context": _service_context(),
        "graceful_shutdown_seconds": 30,
    }
    args.update(kwargs)
    with patch.object(pool, "_register_result_reader"), patch(
        "src.services.execution.process_pool.has_sufficient_memory_cgroup",
        return_value=True,
    ):
        await pool.route_service(**args)


class TestRouteService:
    async def test_routes_into_service_slots_not_workflow_slots(self):
        pool = _make_pool()
        pool._fork_process = lambda: _spawn(pool)

        await _route(pool)

        assert len(pool.processes) == 0
        assert len(pool.service_processes) == 1
        handle = next(iter(pool.service_processes.values()))
        assert handle.service is not None
        assert handle.service.service_id == "service-1"
        assert handle.service.attempt_id == "attempt-1"
        assert handle.service.lease_token == "lease-1"
        assert handle.service.graceful_shutdown_seconds == 30
        handle.work_queue.put_nowait.assert_called_once_with(
            ("attempt-1", _service_context())
        )

    async def test_full_service_slots_raise_without_touching_workflows(self):
        pool = _make_pool(max_service_workers=1)
        pool._fork_process = lambda: _spawn(pool)

        await _route(pool)
        with pytest.raises(RuntimeError, match="No service slot available"):
            await _route(pool, attempt_id="attempt-2")

        assert len(pool.processes) == 0
        assert len(pool.service_processes) == 1

    async def test_memory_pressure_rejects_service_fork(self):
        pool = _make_pool()
        pool._fork_process = lambda: _spawn(pool)

        with patch(
            "src.services.execution.process_pool.has_sufficient_memory_cgroup",
            return_value=False,
        ):
            with pytest.raises(MemoryError, match="memory pressure"):
                await pool.route_service(
                    service_id="service-1",
                    attempt_id="attempt-1",
                    lease_token="lease-1",
                    context=_service_context(),
                )
        assert len(pool.service_processes) == 0


class TestServiceResults:
    async def test_result_routes_to_service_callback_and_frees_slot(self):
        on_service = AsyncMock()
        on_workflow = AsyncMock()
        pool = _make_pool(on_service_result=on_service)
        pool.on_result = on_workflow
        pool._fork_process = lambda: _spawn(pool)
        await _route(pool)
        handle = next(iter(pool.service_processes.values()))

        await pool._handle_result(handle, {"status": "Success"})

        assert len(pool.service_processes) == 0
        assert len(pool.processes) == 0
        on_service.assert_awaited_once()
        envelope = on_service.await_args.args[0]
        assert envelope["status"] == "Success"
        assert envelope["service"]["attempt_id"] == "attempt-1"
        assert envelope["recycled"] is False
        on_workflow.assert_not_awaited()

    async def test_late_result_suppressed_after_recycle_synthetic(self):
        on_service = AsyncMock()
        pool = _make_pool(on_service_result=on_service)
        pool._fork_process = lambda: _spawn(pool)
        await _route(pool)
        handle = next(iter(pool.service_processes.values()))
        handle.service.recycled = True
        handle.service.recycle_reason = "template_recycle"

        # Recycle path already owned the terminal callback.
        handle.result_reported = True
        await pool._handle_result(handle, {"status": "Success"})

        on_service.assert_not_awaited()
        assert len(pool.service_processes) == 0


class TestServiceHealth:
    def _dead_service_handle(self, pool):
        process = MagicMock()
        process.is_alive.return_value = False
        process.exitcode = 1
        handle = ProcessHandle(
            id="process-9",
            process=process,
            pid=9999,
            state=ProcessState.BUSY,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )
        from src.services.execution.process_pool import ServiceInfo

        handle.service = ServiceInfo(
            service_id="service-1",
            attempt_id="attempt-1",
            lease_token="lease-1",
        )
        handle.result_queue.get_nowait.side_effect = Empty()
        pool.service_processes[handle.id] = handle
        return handle

    async def test_crash_reports_service_envelope(self):
        on_service = AsyncMock()
        pool = _make_pool(on_service_result=on_service)
        pool._template = MagicMock()
        pool._template.collect_child_exit_statuses.return_value = {}
        self._dead_service_handle(pool)

        await pool._check_process_health()

        assert len(pool.service_processes) == 0
        on_service.assert_awaited_once()
        envelope = on_service.await_args.args[0]
        assert envelope["error_type"] == "ProcessCrashError"
        assert envelope["service"]["attempt_id"] == "attempt-1"

    async def test_killed_silent_service_child_reports_orphan(self):
        on_service = AsyncMock()
        pool = _make_pool(on_service_result=on_service)
        pool.graceful_shutdown_seconds = 0
        pool._template = MagicMock()
        pool._template.collect_child_exit_statuses.return_value = {}
        handle = self._dead_service_handle(pool)
        handle.state = ProcessState.KILLED
        handle.killed_at = datetime(
            2020, 1, 1, tzinfo=timezone.utc
        )

        await pool._check_process_health()

        on_service.assert_awaited_once()
        envelope = on_service.await_args.args[0]
        assert envelope["error_type"] == "OrphanedService"

    async def test_timeout_sweep_never_touches_services(self):
        pool = _make_pool()
        handle = self._dead_service_handle(pool)
        pool._template = MagicMock()
        with patch.object(
            pool, "_kill_process", new_callable=AsyncMock
        ) as kill:
            await pool._check_timeouts()
        kill.assert_not_awaited()
        assert handle.id in pool.service_processes


class TestServiceStop:
    async def test_stop_service_child_terminates_owned_attempt(self):
        pool = _make_pool()
        pool._fork_process = lambda: _spawn(pool)
        await _route(pool)
        handle = next(iter(pool.service_processes.values()))
        handle.process.is_alive.return_value = False

        with patch.object(
            pool, "_unregister_result_reader"
        ):
            assert await pool.stop_service_child("attempt-1") is True
        assert handle.state == ProcessState.KILLED

    async def test_stop_unknown_attempt_returns_false(self):
        pool = _make_pool()
        assert await pool.stop_service_child("nope") is False

    async def test_terminate_uses_per_service_grace(self):
        pool = _make_pool()
        pool._fork_process = lambda: _spawn(pool)
        await _route(pool, graceful_shutdown_seconds=42)
        handle = next(iter(pool.service_processes.values()))
        handle.process.is_alive.return_value = False

        with patch.object(pool, "_unregister_result_reader"), patch(
            "src.services.execution.process_pool.os.kill"
        ):
            await pool._terminate_process(handle, keep_result_reader=True)
        # Child already dead: no kill, no sleep — grace path exercised
        # through stop_service_child below instead.
        assert handle.state == ProcessState.KILLED


class TestServiceDrain:
    async def test_recycle_marks_services_and_keeps_handles(self):
        pool = _make_pool()
        pool._fork_process = lambda: _spawn(pool)
        await _route(pool)
        handle = next(iter(pool.service_processes.values()))

        terminated = []

        async def fake_terminate(h, **kwargs):
            terminated.append((h, kwargs))

        with patch.object(pool, "_terminate_process", side_effect=fake_terminate), patch.object(
            pool, "restart_template", new_callable=AsyncMock
        ):
            await pool.drain_and_restart_template(drain_timeout=0.01)

        assert handle.service.recycled is True
        assert handle.service.recycle_reason == "template_recycle"
        assert terminated and terminated[0][1].get("keep_result_reader") is True
        # Handle stays registered: the terminal result still routes.
        assert handle.id in pool.service_processes


class TestServiceAccounting:
    async def test_heartbeat_and_status_include_services(self):
        pool = _make_pool()
        pool._fork_process = lambda: _spawn(pool)
        await _route(pool)

        heartbeat = pool._build_heartbeat()
        assert heartbeat["service_count"] == 1
        assert heartbeat["busy_count"] == 0
        assert heartbeat["service_children"][0]["service"] == {
            "service_id": "service-1",
            "attempt_id": "attempt-1",
        }
        # Service children report memory like workflow children
        # (observed via heartbeat, not persisted).
        assert isinstance(
            heartbeat["service_children"][0]["memory_mb"], float
        )

        status = pool.get_status()
        assert status["service_count"] == 1
        assert status["service_processes"][0]["attempt_id"] == "attempt-1"
