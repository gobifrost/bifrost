"""API-owned execution for lightweight platform maintenance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_api_maintenance_claims_only_its_catalogued_jobs(monkeypatch) -> None:
    from src.jobs.platform import api_maintenance

    claim = SimpleNamespace(
        id=uuid4(), job_type="oauth.refresh", memory_required_bytes=128
    )
    claimant = AsyncMock(return_value=claim)
    runner = AsyncMock(return_value=True)
    monkeypatch.setattr(api_maintenance, "claim_platform_job", claimant)
    monkeypatch.setattr(api_maintenance, "run_claimed_platform_job", runner)
    monkeypatch.setattr(
        api_maintenance, "effective_memory_sample", lambda: (100, 1_000)
    )
    executor = api_maintenance.ApiMaintenanceExecutor(
        settings=SimpleNamespace(  # type: ignore[arg-type]
            memory_pressure_threshold=0.85,
            redis_url="redis://test",
        )
    )

    assert await executor._claim_and_run_once()
    claimant.assert_awaited_once_with(
        backend="local",
        fits_bytes=750,
        job_types=frozenset(
            {
                "agent.summary_backfill",
                "artifact.retention_cleanup",
                "chat.video_generation",
                "cloudflare.capacity.apply",
                "embedding.reindex",
                "files.move",
                "model_catalog.refresh",
                "oauth.refresh",
                "run.retention",
                "sdk.video_generation",
                "solution.update_check",
                "webhook.renew",
                "workspace.file_index_reconcile",
            }
        ),
    )
    runner.assert_not_awaited()
    await executor.stop()


@pytest.mark.asyncio
async def test_api_maintenance_does_not_claim_when_api_is_at_its_memory_headroom(
    monkeypatch,
) -> None:
    from src.jobs.platform import api_maintenance

    claimant = AsyncMock()
    monkeypatch.setattr(api_maintenance, "claim_platform_job", claimant)
    monkeypatch.setattr(
        api_maintenance, "effective_memory_sample", lambda: (850, 1_000)
    )
    executor = api_maintenance.ApiMaintenanceExecutor(
        settings=SimpleNamespace(  # type: ignore[arg-type]
            memory_pressure_threshold=0.85,
            redis_url="redis://test",
        )
    )

    assert not await executor._claim_and_run_once()
    claimant.assert_not_awaited()


def test_api_owned_maintenance_is_excluded_from_executor_types() -> None:
    from src.jobs.platform.catalog import (
        api_control_platform_job_types,
        api_platform_job_types,
        executor_platform_job_types,
    )

    assert api_platform_job_types() == frozenset(
        {
            "agent.summary_backfill",
            "artifact.retention_cleanup",
            "chat.video_generation",
            "cloudflare.capacity.apply",
            "embedding.reindex",
            "files.move",
            "model_catalog.refresh",
            "oauth.refresh",
            "run.retention",
            "sdk.video_generation",
            "solution.update_check",
            "webhook.renew",
            "workspace.file_index_reconcile",
        }
    )
    assert api_control_platform_job_types() == frozenset(
        {
            "agent.summary_backfill",
            "cloudflare.capacity.apply",
            "model_catalog.refresh",
            "oauth.refresh",
            "solution.update_check",
            "webhook.renew",
            "workspace.file_index_reconcile",
        }
    )
    assert not api_platform_job_types().intersection(executor_platform_job_types())
    assert "workspace.file_index_reconcile" not in executor_platform_job_types()


@pytest.mark.asyncio
async def test_physical_demand_ignores_api_owned_maintenance(monkeypatch) -> None:
    from src.services.worker_pool import demand

    job_id = uuid4()
    active = SimpleNamespace(all=lambda: [])
    queued = SimpleNamespace(all=lambda: [(job_id, "oauth.refresh", None)])
    db = AsyncMock()
    db.execute.side_effect = [active, queued]
    monkeypatch.setattr(demand, "effective_job_max_concurrency", lambda *_args: None)

    assert await demand.pending_capacity_platform_job_ids(
        db,
        policy=SimpleNamespace(),
        now=datetime.now(timezone.utc),
    ) == []


@pytest.mark.asyncio
async def test_api_maintenance_reserves_a_control_slot_while_user_io_is_running(monkeypatch) -> None:
    from src.jobs.platform import api_maintenance

    release_user_io = asyncio.Event()
    control_completed = asyncio.Event()
    claims = [
        SimpleNamespace(id=uuid4(), job_type="files.move", memory_required_bytes=100),
        SimpleNamespace(id=uuid4(), job_type="embedding.reindex", memory_required_bytes=100),
        SimpleNamespace(id=uuid4(), job_type="chat.video_generation", memory_required_bytes=100),
        SimpleNamespace(id=uuid4(), job_type="oauth.refresh", memory_required_bytes=100),
    ]
    requests: list[frozenset[str]] = []

    async def claim_platform_job(**kwargs):
        requests.append(kwargs["job_types"])
        return claims.pop(0)

    async def run_claimed_platform_job(claim, backend):
        if claim.job_type == "oauth.refresh":
            control_completed.set()
        else:
            await release_user_io.wait()
        return True

    monkeypatch.setattr(api_maintenance, "claim_platform_job", claim_platform_job)
    monkeypatch.setattr(api_maintenance, "run_claimed_platform_job", run_claimed_platform_job)
    monkeypatch.setattr(api_maintenance, "effective_memory_sample", lambda: (0, 10_000))
    executor = api_maintenance.ApiMaintenanceExecutor(
        settings=SimpleNamespace(memory_pressure_threshold=0.85, redis_url="redis://test")
    )

    for _ in range(4):
        assert await executor._claim_and_run_once()
    await asyncio.wait_for(control_completed.wait(), timeout=1)

    from src.jobs.platform.catalog import api_control_platform_job_types

    assert requests[-1] == api_control_platform_job_types()
    release_user_io.set()
    await executor.stop()


@pytest.mark.asyncio
async def test_api_maintenance_deducts_active_claim_memory_before_claiming(monkeypatch) -> None:
    from src.jobs.platform import api_maintenance

    release = asyncio.Event()
    first = SimpleNamespace(id=uuid4(), job_type="files.move", memory_required_bytes=500)
    requested_budgets: list[int] = []

    async def claim_platform_job(**kwargs):
        requested_budgets.append(kwargs["fits_bytes"])
        return first if len(requested_budgets) == 1 else None

    async def run_claimed_platform_job(_claim, _backend):
        await release.wait()
        return True

    monkeypatch.setattr(api_maintenance, "claim_platform_job", claim_platform_job)
    monkeypatch.setattr(api_maintenance, "run_claimed_platform_job", run_claimed_platform_job)
    monkeypatch.setattr(api_maintenance, "effective_memory_sample", lambda: (100, 1_000))
    executor = api_maintenance.ApiMaintenanceExecutor(
        settings=SimpleNamespace(memory_pressure_threshold=0.85, redis_url="redis://test")
    )

    assert await executor._claim_and_run_once()
    assert not await executor._claim_and_run_once()
    assert requested_budgets == [750, 250]
    release.set()
    await executor.stop()


@pytest.mark.asyncio
async def test_api_maintenance_stop_cancels_and_settles_active_claim(monkeypatch) -> None:
    from src.jobs.platform import api_maintenance

    cancelled = asyncio.Event()
    claim = SimpleNamespace(
        id=uuid4(), job_type="files.move", memory_required_bytes=100
    )

    async def claim_platform_job(**_kwargs):
        return claim

    async def run_claimed_platform_job(_claim, _backend):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(api_maintenance, "claim_platform_job", claim_platform_job)
    monkeypatch.setattr(api_maintenance, "run_claimed_platform_job", run_claimed_platform_job)
    monkeypatch.setattr(api_maintenance, "effective_memory_sample", lambda: (0, 10_000))
    executor = api_maintenance.ApiMaintenanceExecutor(
        settings=SimpleNamespace(memory_pressure_threshold=0.85, redis_url="redis://test")
    )

    assert await executor._claim_and_run_once()
    await asyncio.sleep(0)
    await executor.stop()

    assert cancelled.is_set()
