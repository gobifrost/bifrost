from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.scheduler import control as control_module
from src.scheduler.control import CONTROL_TASK_CONCURRENCY, ControlPlane
from src.scheduler.registry import SCHEDULED_TASKS, ScheduledTaskDefinition


class FakeLease:
    owner_id = "control-test"

    def __init__(self, *, renew_result: bool = False, holds: bool = True) -> None:
        self.is_leader = False
        self.holds = holds
        self.renew_result = renew_result
        self.acquire_calls = 0
        self.release_calls = 0

    def holds_lease(self) -> bool:
        return self.is_leader and self.holds

    def seconds_remaining(self) -> float:
        return 5.0 if self.holds_lease() else 0.0

    async def try_acquire(self) -> bool:
        self.acquire_calls += 1
        if self.acquire_calls > 1:
            return False
        self.is_leader = True
        return True

    async def renew(self) -> bool:
        if not self.renew_result:
            self.is_leader = False
        return self.renew_result

    async def release(self) -> None:
        self.release_calls += 1
        self.is_leader = False


def _settings(backend: str = "local") -> SimpleNamespace:
    return SimpleNamespace(platform_build_backend=backend)


def _control(lease: FakeLease, **kwargs) -> ControlPlane:
    return ControlPlane(
        host="test",
        leadership_lease=lease,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        **kwargs,
    )


# The schedule each system task had in the scheduler before control was
# extracted. Moving control between hosts must not add, drop or retime any.
LEGACY_SCHEDULES = {
    "schedule_processor": ("cron", {"minute": "*/1"}, False),
    "summary_backfill_reconciliation": ("interval", {"seconds": 60}, True),
    "deferred_execution_promoter": ("interval", {"seconds": 60}, True),
    "workspace_bundle_preview_cleanup": ("interval", {"hours": 1}, True),
    "workflow_operation_usage_flush": ("interval", {"minutes": 15}, True),
    "logo_thumbnail_backfill": ("interval", {"minutes": 1}, True),
    "execution_cleanup": ("cron", {"minute": "*/5"}, True),
    "oauth_token_refresh": ("interval", {"minutes": 15}, True),
    "metrics_refresh": ("interval", {"minutes": 60}, True),
    "knowledge_storage_refresh": ("cron", {"hour": 2, "minute": 0}, True),
    "file_index_reconciliation": ("cron", {"hour": 1, "minute": 0}, True),
    "webhook_renewal": ("interval", {"hours": 6}, True),
    "solution_update_check": ("interval", {"hours": 6}, True),
    "model_catalog_refresh": ("interval", {"hours": 4}, True),
    "solution_export_job_cleanup": ("interval", {"hours": 1}, False),
    "artifact_retention_cleanup": ("cron", {"hour": 3, "minute": 30}, False),
    "audit_archive": ("cron", {"hour": 2, "minute": 30}, False),
    "run_retention": ("cron", {"hour": 3, "minute": 0}, False),
    "stuck_event_cleanup": ("cron", {"minute": "*/5"}, True),
    "worker_metrics_sampling": ("interval", {"seconds": 60}, True),
    "worker_metrics_cleanup": ("cron", {"hour": 4, "minute": 0}, False),
    "scheduler_diagnostics_cleanup": ("interval", {"hours": 1}, False),
}


def test_catalog_preserves_every_legacy_schedule() -> None:
    actual = {
        task.task_id: (task.trigger, task.trigger_args, task.run_at_startup)
        for task in SCHEDULED_TASKS
    }
    assert actual == LEGACY_SCHEDULES


def test_every_catalog_callback_resolves() -> None:
    for task in SCHEDULED_TASKS:
        assert callable(control_module.resolve_callback(task.callback)), task.task_id


@pytest.mark.asyncio
async def test_leader_installs_the_full_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "publish_task_states", AsyncMock())
    control = _control(FakeLease())
    await control._start_leader_services()
    try:
        scheduler = control._scheduler
        assert scheduler is not None
        assert {job.id for job in scheduler.get_jobs()} == set(LEGACY_SCHEDULES)
        startup = scheduler.get_job("execution_cleanup")
        assert startup is not None
        assert startup.next_run_time <= datetime.now(timezone.utc)
        deferred = scheduler.get_job("audit_archive")
        assert deferred is not None
        assert deferred.next_run_time.hour == 2 and deferred.next_run_time.minute == 30
        assert str(deferred.next_run_time.tzinfo) in {"UTC", "utc"}
    finally:
        await control._stop_leader_services()


@pytest.mark.asyncio
async def test_trigger_services_run_only_while_lease_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = FakeLease()
    control = _control(lease)
    control._start_leader_services = AsyncMock()  # type: ignore[method-assign]
    control._stop_leader_services = AsyncMock()  # type: ignore[method-assign]

    waits = 0

    async def wait_then_stop(stop_event: asyncio.Event, _seconds: float) -> None:
        nonlocal waits
        waits += 1
        if waits == 2:
            stop_event.set()

    monkeypatch.setattr(control_module, "wait_or_stop", wait_then_stop)

    await control._leadership_loop()

    control._start_leader_services.assert_awaited_once()
    assert control._stop_leader_services.await_count >= 1
    assert lease.acquire_calls == 2
    assert lease.release_calls >= 1


@pytest.mark.asyncio
async def test_renewal_that_outlives_the_lease_surrenders_leadership() -> None:
    lease = FakeLease(renew_result=True)
    lease.is_leader = True

    async def hung_renew() -> bool:
        await asyncio.Future()
        return True

    lease.renew = hung_renew  # type: ignore[method-assign]
    lease.seconds_remaining = lambda: 0.05  # type: ignore[method-assign]
    control = _control(lease)

    assert await control._renew_within_lease() is False


@pytest.mark.asyncio
async def test_stopping_leader_services_cancels_running_callback() -> None:
    control = _control(FakeLease())
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def running_callback() -> None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    apscheduler = AsyncIOScheduler()
    apscheduler.add_job(running_callback, next_run_time=datetime.now(timezone.utc))
    apscheduler.start()
    control._scheduler = apscheduler

    await asyncio.wait_for(started.wait(), timeout=1)
    await control._stop_leader_services()
    await asyncio.wait_for(cancelled.wait(), timeout=1)


@pytest.mark.asyncio
async def test_failed_scheduler_shutdown_is_reported() -> None:
    control = _control(FakeLease())
    apscheduler = MagicMock()
    apscheduler.shutdown.side_effect = RuntimeError("scheduler still active")
    control._scheduler = apscheduler

    with pytest.raises(RuntimeError, match="scheduler still active"):
        await control._stop_leader_services()

    assert control._scheduler is None
    apscheduler.shutdown.assert_called_once_with(wait=False)


@pytest.mark.asyncio
async def test_callback_does_not_start_without_a_current_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_run = AsyncMock()
    monkeypatch.setattr(control_module, "start_scheduler_run", start_run)
    lease = FakeLease(holds=False)
    lease.is_leader = True
    callback = AsyncMock()

    await _control(lease)._run_scheduled_task("schedule_processor", callback)

    start_run.assert_not_awaited()
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_callbacks_share_a_bounded_number_of_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control_module, "start_scheduler_run", AsyncMock())
    monkeypatch.setattr(control_module, "finish_scheduler_run", AsyncMock())
    lease = FakeLease()
    lease.is_leader = True
    control = _control(lease)
    running = 0
    peak = 0
    release = asyncio.Event()

    async def callback() -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1

    tasks = [
        asyncio.create_task(control._run_scheduled_task(f"task-{n}", callback))
        for n in range(CONTROL_TASK_CONCURRENCY + 3)
    ]
    for _ in range(10):
        await asyncio.sleep(0)
    assert peak == CONTROL_TASK_CONCURRENCY
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_unexpected_control_exit_is_reported_but_stop_is_not() -> None:
    failures: list[str] = []
    control = _control(FakeLease(), on_failure=lambda task: failures.append(task.get_name()))

    async def exits() -> None:
        return

    control.running = True
    task = asyncio.create_task(exits(), name="control-leadership")
    task.add_done_callback(control._task_done)
    await task
    await asyncio.sleep(0)
    assert failures == ["control-leadership"]

    failures.clear()
    control._leadership_loop = AsyncMock()  # type: ignore[method-assign]
    control.running = False
    await control.start()
    await control.stop()
    await asyncio.sleep(0)
    assert failures == []


@pytest.mark.asyncio
async def test_build_reconciler_runs_only_for_kubernetes_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.schedulers import kubernetes_jobs

    async def idle(stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    monkeypatch.setattr(kubernetes_jobs, "kubernetes_build_loop", idle)

    for backend, expected in (("local", {"control-leadership"}), (
        "kubernetes",
        {"control-leadership", "kubernetes-build-controller"},
    )):
        control = ControlPlane(
            host="test",
            leadership_lease=FakeLease(),  # type: ignore[arg-type]
            settings=_settings(backend),  # type: ignore[arg-type]
        )
        control._leadership_loop = AsyncMock()  # type: ignore[method-assign]
        await control.start()
        assert {task.get_name() for task in control.tasks} == expected
        await control.stop()


def test_catalog_definitions_are_hashable_values() -> None:
    first = ScheduledTaskDefinition("a", "A", "Hourly", trigger_args={"hours": 1})
    assert hash(first) == hash(ScheduledTaskDefinition("a", "A", "Hourly", trigger_args={"hours": 2}))
