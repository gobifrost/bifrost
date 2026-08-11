from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.services.builder import global_workspace


def _write(root: Path, path: str, content: bytes) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_validation_accepts_source_changes_but_not_manifest_changes(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    _write(base, ".bifrost/agents.yaml", b"agents: []\n")
    _write(candidate, ".bifrost/agents.yaml", b"agents: []\n")
    _write(base, "workflows/example.py", b"VALUE = 1\n")
    _write(candidate, "workflows/example.py", b"VALUE = 2\n")

    assert global_workspace.validate_global_workspace(base, candidate) == []

    _write(candidate, ".bifrost/agents.yaml", b"agents:\n  - changed\n")
    errors = global_workspace.validate_global_workspace(base, candidate)
    assert errors == [".bifrost manifests are read-only: .bifrost/agents.yaml"]


def test_validation_reports_python_syntax_without_executing_code(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    base.mkdir()
    _write(candidate, "workflows/broken.py", b"def broken(:\n    pass\n")

    errors = global_workspace.validate_global_workspace(base, candidate)

    assert len(errors) == 1
    assert errors[0].startswith("workflows/broken.py line 1: invalid Python:")


@pytest.mark.asyncio
async def test_compensation_preserves_a_concurrent_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {
        "ours.py": b"proposed",
        "concurrent.py": b"changed by somebody else",
    }
    restore_maps = AsyncMock()
    db = AsyncMock()
    monkeypatch.setattr(global_workspace, "_repo_map", AsyncMock(return_value=current))
    monkeypatch.setattr(global_workspace, "_restore_maps", restore_maps)

    conflicts = await global_workspace._compensate_live_repo(
        db,
        before={"ours.py": b"before", "concurrent.py": b"before"},
        proposed={"ours.py": b"proposed", "concurrent.py": b"proposed"},
        updated_by="rollback@example.com",
        limits=global_workspace.WorkspaceLimits(),
    )

    assert conflicts == ["concurrent.py"]
    restore = restore_maps.await_args.kwargs["restore"]
    assert restore == {
        "ours.py": b"before",
        "concurrent.py": b"changed by somebody else",
    }
    db.commit.assert_awaited_once()
