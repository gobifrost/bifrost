"""The model_catalog.refresh platform job maps outcomes to job results."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.jobs.platform import system_maintenance as mod
from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.registry import get_platform_job_definition
from src.scheduler.registry import SCHEDULED_TASKS_BY_ID
from src.services.model_catalog import CatalogRefreshRejected, CatalogRefreshResult


@pytest.fixture(autouse=True)
def job_db(monkeypatch):
    session = MagicMock(commit=AsyncMock())

    @asynccontextmanager
    async def _db():
        yield session

    monkeypatch.setattr(mod, "get_db_context", _db)
    return session


def _context():
    return MagicMock(report=AsyncMock(), log=AsyncMock())


def test_refresh_job_is_registered_and_scheduled() -> None:
    assert get_platform_job_definition("model_catalog.refresh") is mod.MODEL_CATALOG_REFRESH_DEFINITION
    assert SCHEDULED_TASKS_BY_ID["model_catalog_refresh"].execution_mode == "durable_job"


async def test_successful_refresh_commits_and_reports_counts(job_db) -> None:
    fetched = datetime(2026, 9, 29, tzinfo=timezone.utc)
    with patch(
        "src.services.model_catalog.refresh_model_catalog",
        new=AsyncMock(return_value=CatalogRefreshResult(True, 225, 8272, fetched)),
    ):
        result = await mod.run_model_catalog_refresh(_context(), mod.EmptyMaintenancePayload())

    job_db.commit.assert_awaited_once()
    assert result == {
        "changed": True,
        "provider_count": 225,
        "model_count": 8272,
        "fetched_at": fetched.isoformat(),
    }


async def test_rejected_catalog_fails_without_retry() -> None:
    with patch(
        "src.services.model_catalog.refresh_model_catalog",
        new=AsyncMock(side_effect=CatalogRefreshRejected("too small")),
    ), pytest.raises(PlatformJobFailure) as failure:
        await mod.run_model_catalog_refresh(_context(), mod.EmptyMaintenancePayload())
    assert (failure.value.code, failure.value.retryable) == ("catalog_rejected", False)


async def test_unreachable_source_fails_retryably() -> None:
    request = httpx.Request("GET", "https://models.dev/api.json")
    with patch(
        "src.services.model_catalog.refresh_model_catalog",
        new=AsyncMock(side_effect=httpx.ConnectError("offline", request=request)),
    ), pytest.raises(PlatformJobFailure) as failure:
        await mod.run_model_catalog_refresh(_context(), mod.EmptyMaintenancePayload())
    assert (failure.value.code, failure.value.retryable) == ("catalog_unreachable", True)
