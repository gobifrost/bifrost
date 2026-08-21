import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from shared import builder_workspace_archive as shared_archives
from shared.builder_workspace_archive import (
    BUILDER_TURN_WORKSPACE_LIMITS,
    BuilderWorkspaceArchiveMismatch as SharedArchiveMismatch,
    BuilderWorkspaceArchiveTooLarge,
    hydrate_builder_turn_workspace,
)
from src.services.builder.fs_tools import WorkspaceLimits
from src.services.builder.workspace_archives import (
    BuilderWorkspaceArchiveMismatch,
    BuilderWorkspaceArchiveSource,
    hydrate_workspace_archive,
    persist_workspace_archive,
)


def _zip_bytes(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    root = tmp_path / f"archive-{len(files)}"
    workspace = root / "source"
    workspace.mkdir(parents=True)
    for rel, content in files.items():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    archive = root / "source.zip"
    from src.services.builder.scaffold import zip_workspace

    digest = zip_workspace(workspace, archive)
    return archive, digest


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(1024):
            yield chunk


async def _byte_chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.mark.asyncio
async def test_hydrate_workspace_verifies_digest_and_strips_legacy_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive, digest = _zip_bytes(
        tmp_path,
        {
            "README.md": "ok",
            "skills/bifrost-build/SKILL.md": "legacy",
            ".bifrost/agents.yaml": "agents: {}\n",
        },
    )

    monkeypatch.setattr(
        BuilderWorkspaceArchiveSource,
        "iter_chunks",
        lambda _source: _chunks(archive),
    )
    source = BuilderWorkspaceArchiveSource(
        kind="revision",
        solution_id=uuid4(),
        session_id=uuid4(),
        archive_id=uuid4(),
        expected_sha256=digest,
    )
    destination = tmp_path / "workspace"
    destination.mkdir()

    extracted = await hydrate_workspace_archive(
        source,
        destination,
        limits=WorkspaceLimits(),
    )

    assert "README.md" in extracted
    assert (destination / "README.md").read_text(encoding="utf-8") == "ok"
    assert not (destination / "skills" / "bifrost-build").exists()


@pytest.mark.asyncio
async def test_shared_hydrate_uses_builder_turn_limits_and_cleanup(
    tmp_path: Path,
) -> None:
    archive, digest = _zip_bytes(
        tmp_path,
        {
            "README.md": "ok",
            "skills/bifrost-build/SKILL.md": "legacy",
        },
    )
    destination = tmp_path / "shared-workspace"
    destination.mkdir()

    extracted = await hydrate_builder_turn_workspace(
        _chunks(archive),
        expected_sha256=digest,
        destination=destination,
        solution_id=uuid4(),
    )

    assert BUILDER_TURN_WORKSPACE_LIMITS == WorkspaceLimits()
    assert extracted == ["README.md", "skills/bifrost-build/SKILL.md"]
    assert (destination / "README.md").is_file()
    assert not (destination / "skills" / "bifrost-build").exists()


@pytest.mark.asyncio
async def test_hydrate_workspace_rejects_digest_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive, _digest = _zip_bytes(tmp_path, {"README.md": "ok"})
    monkeypatch.setattr(
        BuilderWorkspaceArchiveSource,
        "iter_chunks",
        lambda _source: _chunks(archive),
    )
    source = BuilderWorkspaceArchiveSource(
        kind="revision",
        solution_id=uuid4(),
        session_id=uuid4(),
        archive_id=uuid4(),
        expected_sha256="0" * 64,
    )

    with pytest.raises(BuilderWorkspaceArchiveMismatch):
        await hydrate_workspace_archive(
            source,
            tmp_path / "workspace",
            limits=WorkspaceLimits(),
        )


@pytest.mark.asyncio
async def test_shared_hydrate_rejects_digest_mismatch(tmp_path: Path) -> None:
    archive, _digest = _zip_bytes(tmp_path, {"README.md": "ok"})

    with pytest.raises(SharedArchiveMismatch):
        await hydrate_builder_turn_workspace(
            _chunks(archive),
            expected_sha256="0" * 64,
            destination=tmp_path / "digest-workspace",
            solution_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_shared_hydrate_rejects_oversized_compressed_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(shared_archives, "MAX_BUILDER_TURN_ARCHIVE_BYTES", 3)

    with pytest.raises(BuilderWorkspaceArchiveTooLarge):
        await hydrate_builder_turn_workspace(
            _byte_chunks(b"abcd"),
            expected_sha256="0" * 64,
            destination=tmp_path / "oversized-workspace",
            solution_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_persist_workspace_uses_turn_artifact_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("done", encoding="utf-8")
    calls: list[tuple[Path, int]] = []

    class _Storage:
        def __init__(self, turn_id, dispatch_attempt):
            assert turn_id == "turn-1"
            assert dispatch_attempt == 3

        async def write_from_path(self, path: Path, *, max_bytes: int):
            calls.append((path, max_bytes))
            with zipfile.ZipFile(path) as archive:
                assert archive.read("README.md") == b"done"
            return "a" * 64, 123

    monkeypatch.setattr(
        "src.services.builder.workspace_archives.BuilderTurnArtifactStorage",
        _Storage,
    )

    digest = await persist_workspace_archive(
        workspace=workspace,
        turn_id=cast(Any, "turn-1"),
        dispatch_attempt=3,
        max_bytes=456,
    )

    assert digest == "a" * 64
    assert calls[0][1] == 456
