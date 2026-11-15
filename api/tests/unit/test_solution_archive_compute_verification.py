"""API verification of non-authoritative Solution inspection output."""

from __future__ import annotations

import asyncio
import threading
import shutil
import zipfile
from pathlib import Path

import pytest

from shared import solution_archive_compute as codec
from shared.file_compute import FileComputeResult, decode_file_result
from shared.worker_transfer import ReceivedFileTransfer
from src.services.solutions import archive_compute


def _archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("bifrost.solution.yaml", "slug: demo\nname: Demo\n")
        archive.writestr("workflows/demo.py", "VALUE = 'original'\n")


def _inspection(path: Path) -> tuple[codec.SolutionArchiveInspection, Path]:
    with codec.encode_solution_archive_inspect_inputs(path) as inputs:
        decoded = codec.decode_solution_archive_inspect_inputs(ReceivedFileTransfer(inputs.manifest, inputs.path))
        snapshot = path.with_name("snapshot.zip")
        shutil.copyfile(inputs.path, snapshot)
        with codec.build_solution_archive_inspect_result(decoded) as result:
            return (codec.decode_solution_archive_inspect_results(
                ReceivedFileTransfer(result.manifest, result.path), expected_input=inputs.manifest
            ), snapshot)


def test_verification_uses_original_archive_bytes(tmp_path: Path) -> None:
    original = tmp_path / "original.zip"
    _archive(original)
    inspection, snapshot = _inspection(original)
    verified = archive_compute._verify_inspection(original, snapshot, inspection)
    try:
        assert verified.preview.name == "Demo"
        assert (verified.workspace / "workflows/demo.py").read_text() == "VALUE = 'original'\n"
    finally:
        verified.close()


@pytest.mark.parametrize("kind", ["metadata", "substitute", "missing"])
def test_verification_rejects_tampered_worker_result(tmp_path: Path, kind: str) -> None:
    original = tmp_path / "original.zip"
    _archive(original)
    inspection, snapshot = _inspection(original)
    if kind == "metadata":
        inspection.preview.name = "tampered"
    else:
        replacement = tmp_path / "replacement.zip"
        with zipfile.ZipFile(inspection.source_archive) as source, zipfile.ZipFile(replacement, "w") as output:
            for info in source.infolist():
                if kind == "missing" and info.filename == "workflows/demo.py":
                    continue
                data = source.read(info)
                if kind == "substitute" and info.filename == "workflows/demo.py":
                    data = b"VALUE = 'tampered'\n"
                output.writestr(info, data)
        replacement.replace(inspection.source_archive)
    with pytest.raises(codec.SolutionArchiveInspectError):
        archive_compute._verify_inspection(original, snapshot, inspection)
    inspection.close()


@pytest.mark.asyncio
async def test_cancelled_decode_closes_late_owned_result(tmp_path: Path) -> None:
    original = tmp_path / "original.zip"
    _archive(original)
    with codec.encode_solution_archive_inspect_inputs(original) as transfer:
        outcome = FileComputeResult(ReceivedFileTransfer(transfer.manifest, transfer.path), {})
        entered, release = threading.Event(), threading.Event()

        class Owned:
            closed = False

            def close(self) -> None:
                self.closed = True

        owned = Owned()

        def decode(_received: ReceivedFileTransfer) -> Owned:
            entered.set()
            release.wait()
            return owned

        task = asyncio.create_task(
            decode_file_result(outcome, decode, cleanup_cancelled_result=Owned.close)
        )
        while not entered.is_set():
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        while not owned.closed:
            await asyncio.sleep(0)


@pytest.mark.parametrize("field", ["workflows", "apps", "readme", "classification"])
def test_source_index_cannot_omit_or_reclassify_verified_files(tmp_path, monkeypatch, field):
    from copy import deepcopy
    from types import SimpleNamespace
    from bifrost import solution_definition
    from shared.solution_archive import PreviewResult

    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows/demo.py").write_text("VALUE = 1")
    (tmp_path / "README.md").write_text("Demo")
    canonical = PreviewResult(
        workflows=[{"path": "workflows/demo.py"}],
        apps=[{"path": "apps/demo", "src_files": {"index.tsx": ""}, "bin_files": {}}],
    )
    monkeypatch.setattr(solution_definition, "read_solution_definition", lambda _path: SimpleNamespace(entries=lambda _kind: [{}]))
    index = {
        "workflows": [{"position": 0, "path": "workflows/demo.py"}],
        "apps": [{"position": 0, "path": "apps/demo", "text": ["index.tsx"], "binary": [], "logo": None}],
        "readme": True,
    }
    archive_compute._verify_source_index(tmp_path, canonical, index)
    altered = deepcopy(index)
    if field == "classification":
        altered["apps"][0]["text"] = []
        altered["apps"][0]["binary"] = ["index.tsx"]
    else:
        altered[field] = False if field == "readme" else []
    with pytest.raises(codec.SolutionArchiveInspectError):
        archive_compute._verify_source_index(tmp_path, canonical, altered)


def test_password_flag_and_encrypted_blob_are_derived_from_api_original(tmp_path):
    original = tmp_path / "backup.zip"
    _archive(original)
    with zipfile.ZipFile(original, "a") as archive:
        archive.writestr(".bifrost/secrets.enc", b"API-only encrypted metadata")
    inspection, snapshot = _inspection(original)
    assert inspection.preview.requires_password is False
    with zipfile.ZipFile(snapshot) as source:
        assert ".bifrost/secrets.enc" not in source.namelist()
    verified = archive_compute._verify_inspection(original, snapshot, inspection)
    try:
        assert verified.preview.requires_password is True
        assert (verified.workspace / ".bifrost/secrets.enc").read_bytes() == b"API-only encrypted metadata"
    finally:
        verified.close()
