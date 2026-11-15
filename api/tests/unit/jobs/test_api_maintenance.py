"""API-owned execution for lightweight platform maintenance."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_api_maintenance_claims_only_its_catalogued_jobs(monkeypatch) -> None:
    from src.jobs.platform import api_maintenance

    claim = SimpleNamespace(id=uuid4())
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
                "model_catalog.refresh",
                "oauth.refresh",
                "solution.update_check",
                "webhook.renew",
                "workspace.file_index_reconcile",
            }
        ),
    )
    runner.assert_awaited_once_with(claim)


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
        api_platform_job_types,
        executor_platform_job_types,
    )

    assert api_platform_job_types() == frozenset(
        {
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

    assert await demand.pending_local_platform_job_ids(
        db,
        policy=SimpleNamespace(),
        now=datetime.now(timezone.utc),
    ) == []
