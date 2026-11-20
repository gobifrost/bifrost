"""Focused pure Solution archive guard coverage."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from shared.solution_archive import PAYLOAD_PREFIX, safe_extract_path, validate_zip_members


def test_extracts_source_but_leaves_encrypted_payloads_in_original_archive(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.md", "source")
        archive.writestr(".bifrost/secrets.enc", "encrypted metadata")
        archive.writestr(f"{PAYLOAD_PREFIX}file.bin.enc", b"encrypted payload")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    safe_extract_path(archive_path, str(workspace))

    assert (workspace / "README.md").read_text() == "source"
    assert not (workspace / ".bifrost/file-payloads/file.bin.enc").exists()


def test_conflicting_duplicate_members_remain_refused(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.md", "first")
        archive.writestr("README.md", "second")

    with zipfile.ZipFile(archive_path) as archive, pytest.raises(
        ValueError, match="conflicting duplicate"
    ):
        validate_zip_members(archive)
