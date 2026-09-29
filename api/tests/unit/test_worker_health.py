from __future__ import annotations

from pathlib import Path

import pytest

from src.worker import health


def test_worker_readiness_marker_and_cli_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "READY_PATH", tmp_path / "ready")
    monkeypatch.setattr("sys.argv", ["health", "--ready"])

    assert not health.is_ready()
    assert health.main() == 1

    health.mark_ready()
    assert health.is_ready()
    assert health.main() == 0

    health.clear_ready()
    assert health.main() == 1
