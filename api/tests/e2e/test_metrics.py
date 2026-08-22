"""
Integration tests for daily metrics upsert functionality.

Tests ensure that multiple updates to the same date produce a single row
for both org-specific and global metrics.
"""

import pytest
import pytest_asyncio
import asyncio
from datetime import date
from uuid import uuid4

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ExecutionMetricsDaily
from src.models.orm import Organization
from src.models.enums import ExecutionStatus
from src.core.metrics import _upsert_daily_metrics

METRICS_TEST_DATE = date(2000, 1, 1)


@pytest_asyncio.fixture
async def test_organization(db_session: AsyncSession) -> Organization:
    """Create a test organization in the database."""
    org = Organization(
        id=uuid4(),
        name="Test Metrics Org",
        domain="metrics-test.com",
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


@pytest_asyncio.fixture
async def clean_metrics(db_session: AsyncSession):
    """Clean up metrics table before and after test."""
    today = METRICS_TEST_DATE
    # Clean before test
    await db_session.execute(
        delete(ExecutionMetricsDaily).where(ExecutionMetricsDaily.date == today)
    )
    await db_session.commit()
    yield
    # Clean after test
    await db_session.execute(
        delete(ExecutionMetricsDaily).where(ExecutionMetricsDaily.date == today)
    )
    await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
class TestDailyMetricsUpsert:
    """Test daily metrics upsert behavior."""

    async def test_global_metrics_single_row_after_multiple_updates(
        self, db_session: AsyncSession, clean_metrics
    ):
        """
        Multiple updates for global metrics (org_id=None) on the same day
        should result in exactly one row with accumulated values.
        """
        # Debug: ensure we are using the container DB host
        # This assert helps catch misconfigured URLs inside the test container
        assert "pgbouncer" in str(db_session.bind.sync_engine.url), str(
            db_session.bind.sync_engine.url
        )

        today = METRICS_TEST_DATE

        # First update - success
        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=None,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=1000,
            peak_memory_bytes=100_000_000,
            cpu_total_seconds=0.5,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        # Second update - success
        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=None,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=2000,
            peak_memory_bytes=200_000_000,
            cpu_total_seconds=1.0,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        # Third update - failure
        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=None,
            status=ExecutionStatus.FAILED.value,
            duration_ms=500,
            peak_memory_bytes=50_000_000,
            cpu_total_seconds=0.2,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        # Verify only one global row exists for today
        result = await db_session.execute(
            select(func.count())
            .select_from(ExecutionMetricsDaily)
            .where(
                ExecutionMetricsDaily.date == today,
                ExecutionMetricsDaily.organization_id.is_(None),
            )
        )
        count = result.scalar()
        assert count == 1, f"Expected 1 global row, found {count}"

        # Verify accumulated values
        result = await db_session.execute(
            select(ExecutionMetricsDaily).where(
                ExecutionMetricsDaily.date == today,
                ExecutionMetricsDaily.organization_id.is_(None),
            )
        )
        row = result.scalar_one()

        assert row.execution_count == 3
        assert row.success_count == 2
        assert row.failed_count == 1
        assert row.total_duration_ms == 3500  # 1000 + 2000 + 500
        assert row.avg_duration_ms == 1166
        assert row.max_duration_ms == 2000
        assert row.peak_memory_bytes == 200_000_000
        assert row.total_cpu_seconds == pytest.approx(1.7, rel=0.01)

    async def test_org_specific_metrics_single_row_after_multiple_updates(
        self, db_session: AsyncSession, test_organization: Organization, clean_metrics
    ):
        """
        Multiple updates for an org on the same day should result in
        exactly one row with accumulated values.
        """
        today = METRICS_TEST_DATE

        # First update
        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=test_organization.id,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=1500,
            peak_memory_bytes=150_000_000,
            cpu_total_seconds=0.8,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        # Second update
        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=test_organization.id,
            status=ExecutionStatus.TIMEOUT.value,
            duration_ms=30000,
            peak_memory_bytes=500_000_000,
            cpu_total_seconds=5.0,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        # Verify only one org row exists for today
        result = await db_session.execute(
            select(func.count())
            .select_from(ExecutionMetricsDaily)
            .where(
                ExecutionMetricsDaily.date == today,
                ExecutionMetricsDaily.organization_id == test_organization.id,
            )
        )
        count = result.scalar()
        assert count == 1, f"Expected 1 org row, found {count}"

        # Verify accumulated values
        result = await db_session.execute(
            select(ExecutionMetricsDaily).where(
                ExecutionMetricsDaily.date == today,
                ExecutionMetricsDaily.organization_id == test_organization.id,
            )
        )
        row = result.scalar_one()

        assert row.execution_count == 2
        assert row.success_count == 1
        assert row.timeout_count == 1
        assert row.total_duration_ms == 31500
        assert row.avg_duration_ms == 15750
        assert row.max_duration_ms == 30000
        assert row.peak_memory_bytes == 500_000_000

    async def test_org_and_global_metrics_are_separate(
        self, db_session: AsyncSession, test_organization: Organization, clean_metrics
    ):
        """
        Org-specific and global metrics should be tracked separately.
        """
        today = METRICS_TEST_DATE

        # Update org-specific metrics
        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=test_organization.id,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=1000,
            peak_memory_bytes=100_000_000,
            cpu_total_seconds=0.5,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        # Update global metrics separately
        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=None,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=1000,
            peak_memory_bytes=100_000_000,
            cpu_total_seconds=0.5,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        # Verify org row exists
        result = await db_session.execute(
            select(ExecutionMetricsDaily).where(
                ExecutionMetricsDaily.date == today,
                ExecutionMetricsDaily.organization_id == test_organization.id,
            )
        )
        org_row = result.scalar_one_or_none()
        assert org_row is not None
        assert org_row.execution_count == 1

        # Verify global row also exists
        result = await db_session.execute(
            select(ExecutionMetricsDaily).where(
                ExecutionMetricsDaily.date == today,
                ExecutionMetricsDaily.organization_id.is_(None),
            )
        )
        global_row = result.scalar_one_or_none()
        assert global_row is not None
        assert global_row.execution_count == 1

    async def test_average_duration_calculated_correctly(
        self, db_session: AsyncSession, clean_metrics
    ):
        """
        Average duration should be calculated as total_duration_ms / execution_count.
        """
        today = METRICS_TEST_DATE

        # Three updates with different durations
        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=None,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=1000,
            peak_memory_bytes=None,
            cpu_total_seconds=None,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=None,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=2000,
            peak_memory_bytes=None,
            cpu_total_seconds=None,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        await _upsert_daily_metrics(
            db=db_session,
            today=today,
            org_id=None,
            status=ExecutionStatus.SUCCESS.value,
            duration_ms=3000,
            peak_memory_bytes=None,
            cpu_total_seconds=None,
            time_saved=0,
            value=0.0,
        )
        await db_session.commit()

        result = await db_session.execute(
            select(ExecutionMetricsDaily).where(
                ExecutionMetricsDaily.date == today,
                ExecutionMetricsDaily.organization_id.is_(None),
            )
        )
        row = result.scalar_one()

        # Average should be (1000 + 2000 + 3000) / 3 = 2000
        assert row.avg_duration_ms == 2000
        assert row.total_duration_ms == 6000
        assert row.execution_count == 3

    async def test_concurrent_global_updates_preserve_count_and_average(
        self, db_session: AsyncSession, async_session_factory, clean_metrics
    ):
        """Concurrent completions atomically update the shared global row."""
        today = METRICS_TEST_DATE
        durations = [101, 202, 303, 404]

        async def record(duration_ms: int) -> None:
            async with async_session_factory() as session:
                await _upsert_daily_metrics(
                    db=session,
                    today=today,
                    org_id=None,
                    status=ExecutionStatus.SUCCESS.value,
                    duration_ms=duration_ms,
                    peak_memory_bytes=None,
                    cpu_total_seconds=None,
                    time_saved=0,
                    value=0.0,
                )
                await session.commit()

        await asyncio.gather(*(record(duration) for duration in durations))
        db_session.expire_all()

        result = await db_session.execute(
            select(ExecutionMetricsDaily).where(
                ExecutionMetricsDaily.date == today,
                ExecutionMetricsDaily.organization_id.is_(None),
            )
        )
        row = result.scalar_one()
        assert row.execution_count == len(durations)
        assert row.total_duration_ms == sum(durations)
        assert row.avg_duration_ms == sum(durations) // len(durations)
