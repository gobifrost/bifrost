from datetime import datetime, timezone
from importlib.util import find_spec

import pytest

from shared.execution_timeseries import (
    ExecutionTimeSeriesData,
    build_execution_bucket_starts,
)


def test_hourly_window_has_24_oldest_first_buckets() -> None:
    now = datetime(2026, 7, 23, 17, 45, tzinfo=timezone.utc)

    starts = build_execution_bucket_starts("24h", "UTC", now)

    assert len(starts) == 24
    assert starts[0] == datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
    assert starts[-1] == datetime(2026, 7, 23, 17, 0, tzinfo=timezone.utc)


def test_runtime_includes_iana_timezone_database() -> None:
    assert find_spec("tzdata") is not None

    starts = build_execution_bucket_starts(
        "7d",
        "America/Indianapolis",
        datetime(2026, 7, 23, 17, 45, tzinfo=timezone.utc),
    )

    assert len(starts) == 7


def test_daily_window_uses_local_midnights_across_dst() -> None:
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)

    starts = build_execution_bucket_starts("7d", "America/New_York", now)

    assert len(starts) == 7
    assert starts[-2] == datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)
    assert starts[-1] == datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid timezone"):
        build_execution_bucket_starts(
            "7d",
            "Not/A_Timezone",
            datetime(2026, 7, 23, tzinfo=timezone.utc),
        )


def test_empty_series_has_no_success_rate() -> None:
    series = ExecutionTimeSeriesData(
        window="30d",
        timezone="UTC",
        buckets=(),
        success_count=0,
        failed_count=0,
    )

    assert series.total_count == 0
    assert series.success_rate is None
