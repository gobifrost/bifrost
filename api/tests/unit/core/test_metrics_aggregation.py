"""Regression tests for low-contention execution metric aggregation."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.core.metrics import _upsert_daily_metrics
from src.models.enums import ExecutionStatus


@pytest.mark.asyncio
async def test_daily_metrics_update_uses_one_database_statement() -> None:
    """Do not hold the shared daily row across a select/update round trip."""
    session = AsyncMock()

    await _upsert_daily_metrics(
        db=session,
        today=date.today(),
        org_id=None,
        status=ExecutionStatus.SUCCESS.value,
        duration_ms=123,
        peak_memory_bytes=456,
        cpu_total_seconds=0.25,
        time_saved=7,
        value=8.0,
    )

    session.execute.assert_awaited_once()
