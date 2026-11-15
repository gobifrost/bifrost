"""An exclusive physical worker can serve only its reserved API-owned job."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from src.jobs.platform.policy import EXTERNAL_ISOLATED_BACKEND
from src.worker.pool_main import PoolWorker
from src.worker.remote.host import ApiExecutor, Bridge, IsolatedPlatformJobReservation
from src.worker.remote.ownership import RemoteOwnerLease


def _executor(isolated: bool = True) -> ApiExecutor:
    lease = RemoteOwnerLease(uuid4(), uuid4(), uuid4(), 1)
    reservation = IsolatedPlatformJobReservation(uuid4(), 1) if isolated else None
    bridge = Bridge(Mock(), {
        "current": 1, "limit": 1024, "hostname": "isolated-test", "requirements": {},
        "handlers": () if isolated else (("workflow.execute", 2),),
        "compute_capabilities": (("app.compile", 2),),
    }, lease, isolated_job=reservation)
    return ApiExecutor(bridge, lease.worker_id)


@pytest.mark.asyncio
async def test_exclusive_worker_selects_only_its_exact_next_attempt(monkeypatch) -> None:
    worker = _executor()
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr("src.worker.remote.host.claim_platform_job", claim)

    await worker._claim_platform_job(512)

    reservation = worker.bridge.isolated_job
    assert reservation is not None
    assert worker._handlers == ()
    assert worker.supports_platform_jobs
    assert claim.await_count == 1
    assert claim.await_args.kwargs == {
        "backend": EXTERNAL_ISOLATED_BACKEND,
        "fits_bytes": 512,
        "memory_sample": (1, 1024),
        "job_types": frozenset({"application.deploy", "application.sdk_update"}),
        "executor_worker_id": worker.incarnation_id,
        "job_id": reservation.job_id,
        "expected_attempt": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("isolated", [True, False])
async def test_only_reusable_workers_start_services_or_package_maintenance(isolated) -> None:
    worker = _executor(isolated)
    with (
        patch.object(PoolWorker, "_package_loop", new_callable=AsyncMock) as packages,
        patch.object(PoolWorker, "_start_service_claim_loop", new_callable=AsyncMock) as services,
    ):
        await worker._package_loop()
        await worker._start_service_claim_loop()

    assert packages.await_count == (0 if isolated else 1)
    assert services.await_count == (0 if isolated else 1)


@pytest.mark.asyncio
async def test_exclusive_worker_stops_after_the_canonical_runner_settles(monkeypatch) -> None:
    worker = _executor()
    completed = False

    async def run(_self, _claim):
        nonlocal completed
        assert not worker._stop.is_set()
        completed = True

    monkeypatch.setattr(PoolWorker, "_run_platform_job", run)
    await worker._run_platform_job(SimpleNamespace())

    assert completed and worker._stop.is_set()


@pytest.mark.asyncio
async def test_exclusive_worker_never_starts_a_privileged_runner() -> None:
    worker = _executor()
    raw_runner = AsyncMock()
    worker.bridge.spawn = raw_runner

    with pytest.raises(RuntimeError, match="registered API-owned"):
        await worker._execute_platform_job(SimpleNamespace(job_type="solution.export"))

    raw_runner.assert_not_awaited()
