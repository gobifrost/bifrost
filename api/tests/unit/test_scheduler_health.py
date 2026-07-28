from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.scheduler import health


def test_scheduler_heartbeat_reports_missing_fresh_and_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat_path = tmp_path / "scheduler-heartbeat"
    monkeypatch.setattr(health, "HEARTBEAT_PATH", heartbeat_path)

    assert not health.heartbeat_is_fresh(60)

    health.write_heartbeat()
    assert health.heartbeat_is_fresh(60)

    stale = time.time() - 61
    os.utime(heartbeat_path, (stale, stale))
    assert not health.heartbeat_is_fresh(60)
