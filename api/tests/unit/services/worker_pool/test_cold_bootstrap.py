"""Cold managed capacity is measured before planning or rejecting work."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.work_dispatches import WorkDispatch
from src.services import work_dispatch
from src.services.worker_pool import controller
from tests.unit.services.worker_pool.test_worker_pool import (
    GIB, MIB, POLICY, SETTINGS, SPEC, FakeCluster,
    _accept_pool_run, _admit, _claim, _clean as _clean, _report_budget,
    _set_request, _static_executor, _store_policy, db as db_fixture,
)

db = db_fixture


@pytest.mark.parametrize("budget", [64 * MIB, 512 * MIB])
async def test_one_cold_worker_measures_before_claiming_or_rejecting(db, budget):
    policy = replace(POLICY, worker_memory_mib=1024, largest_worker_mib=1024, max_workers=1)
    await _store_policy(db.sessions, policy)
    run_id = await _accept_pool_run()
    await _set_request(db.sessions, run_id, 128 * MIB)
    # A smaller static worker is not proof the managed worker cannot fit it.
    static_id = await _static_executor("small-static")
    await _report_budget(static_id, 16 * MIB)
    cluster = FakeCluster()
    pools = [controller.WorkerPoolController(cluster, SETTINGS, SPEC) for _ in range(2)]
    results = await asyncio.gather(*(pool.launch_for_demand(policy) for pool in pools))
    assert sorted(len(ids) for ids in results) == [0, 1]
    worker_id = next(ids[0] for ids in results if ids)
    await pools[0].reconcile_worker(worker_id, policy)
    assert await _claim(worker_id) is None
    await work_dispatch.reconcile_work_dispatches()
    async with db.context() as session:
        assert await work_dispatch.largest_budget(session) is None
        assert await session.scalar(select(WorkDispatch.id).where(WorkDispatch.subject_id == UUID(run_id)))
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.memory_limit_bytes == GIB
        assert worker.resource_class == "standard"
        assert worker.reserved_for_dispatch_id is None
    assert await pools[1].launch_for_demand(policy) == []
    await _admit(db.sessions, worker_id, pool_up=False)
    assert await _claim(worker_id, fits=0, budget=0) is None
    await _report_budget(worker_id, budget)
    await pools[0].reconcile()
    async with db.context() as session:
        assert await work_dispatch.largest_budget(session) == budget
    if budget < 128 * MIB:
        await work_dispatch.reconcile_work_dispatches()
        async with db.context() as session:
            execution = await session.get(Execution, UUID(run_id))
            assert execution.status.value == "Failed"
            assert "largest executor" in execution.error_message
    else:
        assert await _claim(worker_id, fits=budget, budget=budget) is not None


async def test_retired_measurement_survives_a_controller_restart(db):
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    run_id = await _accept_pool_run()
    [first_id] = await pool.launch_for_demand(POLICY)
    await pool.reconcile_worker(first_id, POLICY)
    await _admit(db.sessions, first_id)
    async with db.context() as session:
        await session.execute(update(ExecutionWorker).where(ExecutionWorker.id == first_id).values(
            state="retired", ended_at=datetime.now(timezone.utc),
        ))
    await _set_request(db.sessions, run_id, 3 * GIB)
    restarted = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    [next_id] = await restarted.launch_for_demand(POLICY)
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, next_id)
        assert worker.resource_class == "sized"
        assert worker.memory_limit_bytes > POLICY.worker_memory_mib * MIB


@pytest.mark.parametrize("recovery", ["size", "build", "elapsed"])
async def test_failed_bootstrap_is_paced_but_recovers_after_an_outage(db, monkeypatch, recovery):
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(POLICY)
    async with db.context() as session:
        await session.execute(update(ExecutionWorker).where(ExecutionWorker.id == worker_id).values(
            state="lost", ended_at=datetime.now(timezone.utc), end_reason="start_timeout",
        ))
    restarted = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    assert await restarted.launch_for_demand(POLICY) == []
    policy = POLICY
    if recovery == "size":
        policy = replace(POLICY, worker_memory_mib=3072)
    elif recovery == "build":
        restarted.version = "next-build"
    else:
        from datetime import timedelta
        later = datetime.now(timezone.utc) + timedelta(seconds=SETTINGS.pool_start_timeout_seconds + 1)
        monkeypatch.setattr(controller, "_now", lambda: later)
    assert len(await restarted.launch_for_demand(policy)) == 1


async def test_local_platform_job_bootstraps_without_being_marked_too_large(db):
    from src.services.worker_pool.unfit import fail_platform_jobs_that_never_fit

    policy = replace(POLICY, worker_memory_mib=1024, largest_worker_mib=1024)
    job = PlatformJob(
        job_type="files.move", payload={}, memory_required_bytes=128 * MIB,
        requested_by_user_id=str(uuid4()), requested_by_email="fixture@example.invalid",
        requested_by_name="Fixture", title="Cold file move",
    )
    async with db.context() as session:
        session.add(job)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    assert len(await pool.launch_for_demand(policy)) == 1
    async with db.context() as session:
        largest = await work_dispatch.largest_budget(session)
        assert largest is None
        assert await fail_platform_jobs_that_never_fit(session, largest, largest_pod=None) == []
        assert (await session.get(PlatformJob, job.id)).status == "queued"



async def _replace_idle_capacity(db, pool, policy, request):
    """One too-small measurement yields its slot, including the warm minimum."""
    from src.services.worker_pool import claims

    [first] = await pool.launch_for_demand(policy)
    await pool.reconcile_worker(first, policy)
    await _admit(db.sessions, first, pool_up=False)
    await _report_budget(first, 32 * MIB)
    assert await pool.launch_for_demand(policy) == []
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, first)
        assert worker.state == "draining"
        assert worker.end_reason == "capacity_replacement"
    # Draining still occupies the only slot; a restarted controller must wait.
    restarted = controller.WorkerPoolController(pool.client, SETTINGS, SPEC)
    assert await restarted.launch_for_demand(policy) == []
    await claims.retire_worker(first, runs_handled=0)
    [next_id] = await restarted.launch_for_demand(policy)
    assert next_id != first
    async with db.context() as session:
        workers = (await session.scalars(select(ExecutionWorker).where(
            ExecutionWorker.state.in_(("reserved", "starting", "ready", "draining")),
        ))).all()
        assert len(workers) == 1
        assert workers[0].memory_limit_bytes > (await session.get(ExecutionWorker, first)).memory_limit_bytes
        assert workers[0].reserved_for_dispatch_id is None
    await restarted.reconcile_worker(next_id, policy)
    await _admit(db.sessions, next_id, pool_up=False)
    async with db.context() as session:
        limit = (await session.get(ExecutionWorker, next_id)).memory_limit_bytes
    available = min(3 * GIB, int(limit * SETTINGS.memory_pressure_threshold) - 256 * MIB)
    assert available >= request
    await _report_budget(next_id, available)
    return next_id, available


@pytest.mark.parametrize("warm", [0, 1])
@pytest.mark.parametrize("request_bytes", [128 * MIB, 2 * GIB])
@pytest.mark.parametrize("kind", ["workflow", "platform-job"])
async def test_one_worker_handoff_eventually_admits_work(db, warm, request_bytes, kind):
    from src.jobs.schedulers.platform_jobs import claim_platform_job

    policy = replace(POLICY, max_workers=1, warm_workers=warm, largest_worker_mib=4096)
    await _store_policy(db.sessions, policy)
    if kind == "workflow":
        run_id = await _accept_pool_run()
        await _set_request(db.sessions, run_id, request_bytes)
    else:
        job = PlatformJob(
            job_type="files.move", payload={}, memory_required_bytes=request_bytes,
            requested_by_user_id=str(uuid4()), requested_by_email="fixture@example.invalid",
            requested_by_name="Fixture", title="Measured replacement",
        )
        async with db.context() as session:
            session.add(job)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    worker_id, available = await _replace_idle_capacity(db, pool, policy, request_bytes)
    if kind == "workflow":
        assert await _claim(worker_id, fits=available, budget=available) is not None
    else:
        claim = await claim_platform_job(fits_bytes=available)
        assert claim is not None and claim.id == job.id


@pytest.mark.parametrize("changed", ["image", "version", "package_generation", "threshold"])
async def test_calibration_cannot_be_relabelled_after_startup(db, changed):
    from src.services.worker_pool.calibration import measured_budget, record_memory_calibration
    from src.services.memory_requests import MemoryBudget

    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(POLICY)
    await pool.reconcile_worker(worker_id, POLICY)
    await _admit(db.sessions, worker_id)
    async with db.context() as session:
        original = (await session.get(ExecutionWorker, worker_id)).memory_calibration
    await record_memory_calibration(worker_id, MemoryBudget(GIB, 512 * MIB, 0.85), 99)
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.memory_calibration == original
        environment = pool._memory_environment(0)
        assert measured_budget(worker, environment) is not None
        environment[changed] = "changed"
        assert measured_budget(worker, environment) is None


async def test_capacity_publication_cannot_overtake_a_locked_decision(db, monkeypatch):
    original = controller._publish_launchable
    entered, resume = asyncio.Event(), asyncio.Event()
    published = []

    async def publish(value, generation):
        published.append(value)
        entered.set()
        await resume.wait()
        await original(value, generation)

    monkeypatch.setattr(controller, "_publish_launchable", publish)
    await _accept_pool_run()
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    first = asyncio.create_task(pool.launch_for_demand(POLICY))
    await entered.wait()
    try:
        other = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
        assert await other.launch_for_demand(POLICY) == []
        assert published == [None]
    finally:
        resume.set()
        assert len(await first) == 1


@pytest.mark.parametrize("busy", ["runs", "reservation", "handler", "unknown"])
async def test_replacement_never_drains_busy_or_unreported_capacity(db, busy):
    policy = replace(POLICY, max_workers=1, warm_workers=1, largest_worker_mib=4096)
    await _store_policy(db.sessions, policy)
    run_id = await _accept_pool_run()
    await _set_request(db.sessions, run_id, 128 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    [worker_id] = await pool.launch_for_demand(policy)
    await pool.reconcile_worker(worker_id, policy)
    await _admit(db.sessions, worker_id, pool_up=False)
    await _report_budget(worker_id, 32 * MIB)
    values = {"active_runs": 1} if busy == "runs" else (
        {"memory_reserved_bytes": MIB} if busy == "reservation" else
        {"active_handler_counts": {"agent.run": 1} if busy == "handler" else None}
    )
    async with db.context() as session:
        await session.execute(update(ExecutionWorker).where(ExecutionWorker.id == worker_id).values(**values))
    assert await pool.launch_for_demand(policy) == []
    async with db.context() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "ready"


async def test_package_change_invalidates_a_retired_measurement(db):
    from sqlalchemy import delete
    from src.models.orm.package_changes import PackageChange
    from src.services.worker_pool import calibration

    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    run_id = await _accept_pool_run()
    await _set_request(db.sessions, run_id, 128 * MIB)
    [worker_id] = await pool.launch_for_demand(POLICY)
    await pool.reconcile_worker(worker_id, POLICY)
    await _admit(db.sessions, worker_id)
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        worker.state = "retired"
        worker.ended_at = datetime.now(timezone.utc)
        change = PackageChange(action="install", package="fixture", version=None, run_id=str(uuid4()))
        session.add(change)
        await session.flush()
        change_id = change.id
    try:
        [next_id] = await pool.launch_for_demand(POLICY)
        async with db.context() as session:
            worker = await session.get(ExecutionWorker, next_id)
            assert worker.memory_calibration["environment"]["package_generation"] == change_id
            assert await work_dispatch.largest_budget(session) is None
            old = await session.get(ExecutionWorker, worker_id)
            assert calibration.measured_budget(old, pool._memory_environment(change_id)) is None
    finally:
        async with db.context() as session:
            await session.execute(delete(PackageChange).where(PackageChange.id == change_id))


async def test_an_uncertain_largest_size_has_only_one_probe(db):
    policy = replace(POLICY, max_workers=4, largest_worker_mib=4096)
    await _store_policy(db.sessions, policy)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    first_run = await _accept_pool_run()
    [first_id] = await pool.launch_for_demand(policy)
    await pool.reconcile_worker(first_id, policy)
    await _admit(db.sessions, first_id)
    for run_id in [first_run, await _accept_pool_run()]:
        await _set_request(db.sessions, run_id, 8 * GIB)
    [probe_id] = await pool.launch_for_demand(policy)
    async with db.context() as session:
        probe = await session.get(ExecutionWorker, probe_id)
        assert probe.memory_limit_bytes == 4 * GIB
    assert await pool.launch_for_demand(policy) == []


async def test_failed_largest_probe_is_paced_after_standard_measurement(db, monkeypatch):
    from datetime import timedelta

    policy = replace(POLICY, max_workers=2, largest_worker_mib=4096)
    await _store_policy(db.sessions, policy)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    run_id = await _accept_pool_run()
    [first_id] = await pool.launch_for_demand(policy)
    await pool.reconcile_worker(first_id, policy)
    await _admit(db.sessions, first_id)
    await _set_request(db.sessions, run_id, 8 * GIB)
    [probe_id] = await pool.launch_for_demand(policy)
    now = datetime.now(timezone.utc)
    async with db.context() as session:
        await session.execute(update(ExecutionWorker).where(ExecutionWorker.id == probe_id).values(
            state="lost", ended_at=now, end_reason="start_timeout",
        ))
    assert await pool.launch_for_demand(policy) == []
    monkeypatch.setattr(controller, "_now", lambda: now + timedelta(seconds=SETTINGS.pool_start_timeout_seconds + 1))
    assert len(await pool.launch_for_demand(policy)) == 1


@pytest.mark.parametrize("wrong", ["version", "generation", "threshold", "limit"])
async def test_incompatible_first_measurement_is_not_saved(db, wrong):
    from src.services.worker_pool.calibration import record_memory_calibration
    from src.services.memory_requests import MemoryBudget

    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(POLICY)
    await pool.reconcile_worker(worker_id, POLICY)
    await _admit(db.sessions, worker_id, pool_up=False)
    if wrong == "version":
        async with db.context() as session:
            worker = await session.get(ExecutionWorker, worker_id)
            worker.version = "different-runtime"
    await record_memory_calibration(worker_id, MemoryBudget(
        limit_bytes=3 * GIB if wrong == "limit" else 2 * GIB,
        baseline_bytes=512 * MIB, threshold=0.8 if wrong == "threshold" else 0.85,
    ), 99 if wrong == "generation" else 0)
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert "measurement" not in worker.memory_calibration


async def test_retirement_waits_for_the_published_capacity_decision(db, monkeypatch):
    from src.services.worker_pool import claims

    policy = replace(POLICY, largest_worker_mib=2048)
    await _store_policy(db.sessions, policy)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(policy)
    await pool.reconcile_worker(worker_id, policy)
    await _admit(db.sessions, worker_id)
    static_id = await _static_executor("smaller-static")
    await _report_budget(static_id, 16 * MIB)
    original = controller._publish_launchable
    publishing, resume, retiring = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def publish(value, generation):
        assert value == GIB
        publishing.set()
        await resume.wait()
        await original(value, generation)

    async def retire():
        retiring.set()
        await claims.retire_worker(worker_id, runs_handled=0)

    monkeypatch.setattr(controller, "_publish_launchable", publish)
    decision = asyncio.create_task(pool.launch_for_demand(policy))
    await publishing.wait()
    retirement = asyncio.create_task(retire())
    await retiring.wait()
    try:
        assert not retirement.done()
    finally:
        resume.set()
        await asyncio.gather(decision, retirement)
    monkeypatch.setattr(controller, "_publish_launchable", original)
    await pool.launch_for_demand(policy)
    async with db.context() as session:
        assert await work_dispatch.largest_budget(session) == GIB


async def test_platform_job_fails_only_after_the_largest_worker_is_measured(db):
    from src.services.worker_pool.unfit import fail_platform_jobs_that_never_fit

    policy = replace(POLICY, worker_memory_mib=1024, largest_worker_mib=1024)
    await _store_policy(db.sessions, policy)
    job = PlatformJob(
        job_type="files.move", payload={}, memory_required_bytes=128 * MIB,
        requested_by_user_id=str(uuid4()), requested_by_email="fixture@example.invalid",
        requested_by_name="Fixture", title="Confirm largest capacity",
    )
    async with db.context() as session:
        session.add(job)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    [worker_id] = await pool.launch_for_demand(policy)
    await pool.reconcile_worker(worker_id, policy)
    await _admit(db.sessions, worker_id, pool_up=False)
    await _report_budget(worker_id, 64 * MIB)
    await pool.launch_for_demand(policy)
    async with db.context() as session:
        largest = await work_dispatch.largest_budget(session)
        assert largest == 64 * MIB
        failed = await fail_platform_jobs_that_never_fit(session, largest, largest_pod=None)
        assert [item.id for item in failed] == [job.id]
        assert failed[0].error_code == "too_large"


async def test_empty_warm_pool_recovers_after_transient_startup_failure(db, monkeypatch):
    from datetime import timedelta

    policy = replace(POLICY, warm_workers=1, max_workers=1)
    await _store_policy(db.sessions, policy)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    [first_id] = await pool.launch_for_demand(policy)
    now = datetime.now(timezone.utc)
    async with db.context() as session:
        await session.execute(update(ExecutionWorker).where(ExecutionWorker.id == first_id).values(
            state="lost", end_reason="start_timeout", ended_at=now,
        ))
    restarted = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    assert await restarted.launch_for_demand(policy) == []
    monkeypatch.setattr(controller, "_now", lambda: now + timedelta(seconds=SETTINGS.pool_start_timeout_seconds + 1))
    assert len(await restarted.launch_for_demand(policy)) == 1


async def test_unknown_capacity_is_published_before_provider_io(db, monkeypatch):
    from src.core.cache.redis_client import get_redis
    from src.services.memory_requests import LAUNCHABLE_KEY

    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    await _accept_pool_run()
    async with get_redis() as redis:
        await redis.set(LAUNCHABLE_KEY, str(16 * MIB))
    calls = []

    async def provider_io(worker_id, policy):
        async with get_redis() as redis:
            assert await redis.get(LAUNCHABLE_KEY) == ""
        calls.append(worker_id)

    monkeypatch.setattr(pool, "reconcile_worker", provider_io)
    await pool.reconcile()
    assert len(calls) == 1


async def test_package_commit_invalidates_cached_capacity_before_reconciliation(db):
    from sqlalchemy import delete
    from src.models.orm.package_changes import PackageChange

    await controller._publish_launchable(GIB, 0)
    async with db.context() as session:
        change = PackageChange(action="install", package="fixture", version=None, run_id=str(uuid4()))
        session.add(change)
        await session.flush()
        change_id = change.id
    try:
        async with db.context() as session:
            assert await work_dispatch.largest_budget(session) is None
    finally:
        async with db.context() as session:
            await session.execute(delete(PackageChange).where(PackageChange.id == change_id))


async def test_unpaired_capacity_write_cannot_reuse_an_older_measurement_receipt(db):
    from src.core.cache.redis_client import get_redis
    from src.services.memory_requests import LAUNCHABLE_KEY

    await controller._publish_launchable(GIB, 0)
    # An older controller can still write its guessed numeric value during a
    # rolling upgrade. It cannot reuse the new controller's receipt for it.
    async with get_redis() as redis:
        await redis.set(LAUNCHABLE_KEY, str(16 * MIB))
    async with db.context() as session:
        assert await work_dispatch.largest_budget(session) is None
