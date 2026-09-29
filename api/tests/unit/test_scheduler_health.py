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


def test_scheduler_readiness_requires_marker_and_fresh_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "HEARTBEAT_PATH", tmp_path / "heartbeat")
    monkeypatch.setattr(health, "READY_PATH", tmp_path / "ready")

    health.write_heartbeat()
    assert not health.is_ready()

    health.mark_ready()
    assert health.is_ready()

    health.clear_ready()
    assert not health.is_ready()

    health.mark_ready()
    stale = time.time() - 61
    os.utime(health.HEARTBEAT_PATH, (stale, stale))
    assert not health.is_ready()


def test_scheduler_health_cli_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "HEARTBEAT_PATH", tmp_path / "heartbeat")
    monkeypatch.setattr(health, "READY_PATH", tmp_path / "ready")

    monkeypatch.setattr("sys.argv", ["health"])
    assert health.main() == 1

    health.write_heartbeat()
    assert health.main() == 0

    monkeypatch.setattr("sys.argv", ["health", "--ready"])
    assert health.main() == 1

    health.mark_ready()
    assert health.main() == 0
