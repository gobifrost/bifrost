from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.contracts.solutions import SolutionExportOptions
from src.models.orm.solutions import Solution
from src.services.solutions import export_jobs


def test_export_options_encryption_hides_password_and_round_trips() -> None:
    options = SolutionExportOptions(
        include_configs=True,
        include_secrets=True,
        include_tables=True,
        include_files=True,
        password="correct horse battery staple",
    )

    encrypted = export_jobs.encrypt_export_options(options)

    assert "correct horse battery staple" not in encrypted
    decrypted = export_jobs.decrypt_export_options(encrypted)
    assert decrypted == options


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (
            SolutionExportOptions(
                include_configs=True,
                include_secrets=False,
                include_tables=False,
                include_files=False,
                password="pw",
            ),
            {
                "include_imports": True,
                "include_values": True,
                "include_data": False,
                "include_files": False,
            },
        ),
        (
            SolutionExportOptions(
                include_configs=False,
                include_secrets=True,
                include_tables=True,
                include_files=True,
                password="pw",
            ),
            {
                "include_imports": True,
                "include_values": True,
                "include_data": True,
                "include_files": True,
            },
        ),
        (
            SolutionExportOptions(
                include_configs=False,
                include_secrets=False,
                include_tables=True,
                include_files=False,
                password="pw",
            ),
            {
                "include_imports": True,
                "include_values": False,
                "include_data": True,
                "include_files": False,
            },
        ),
    ],
)
def test_export_options_map_to_existing_capture_flags(
    options: SolutionExportOptions,
    expected: dict[str, bool],
) -> None:
    assert export_jobs.export_options_to_capture_flags(options) == expected


def test_filter_config_values_keeps_plain_configs_without_secrets() -> None:
    options = SolutionExportOptions(
        include_configs=True,
        include_secrets=False,
        include_tables=False,
        include_files=False,
        password="pw",
    )

    assert export_jobs.filter_config_values_by_options(
        {"api_url": "https://example.test", "api_key": "secret"},
        secret_keys={"api_key"},
        options=options,
    ) == {"api_url": "https://example.test"}


def test_filter_config_values_keeps_secret_configs_without_plain_configs() -> None:
    options = SolutionExportOptions(
        include_configs=False,
        include_secrets=True,
        include_tables=False,
        include_files=False,
        password="pw",
    )

    assert export_jobs.filter_config_values_by_options(
        {"api_url": "https://example.test", "api_key": "secret"},
        secret_keys={"api_key"},
        options=options,
    ) == {"api_key": "secret"}


def test_backup_zip_requires_password_for_selected_runtime_payload() -> None:
    options = SolutionExportOptions(
        include_configs=True,
        include_secrets=False,
        include_tables=False,
        include_files=False,
        password=None,
    )

    with pytest.raises(ValueError, match="backup export requires a password"):
        export_jobs.validate_export_options_password(options)


def test_artifact_key_and_filename_are_stable() -> None:
    solution_id = uuid4()
    job_id = uuid4()
    solution = Solution(id=solution_id, slug="helpdesk-pack", version=None)

    assert (
        export_jobs.export_artifact_storage_key(solution_id, job_id)
        == f"solution-exports/{solution_id}/{job_id}.zip"
    )
    assert export_jobs.export_artifact_filename(solution) == "helpdesk-pack-unversioned.zip"


@pytest.mark.asyncio
async def test_upload_artifact_streams_zip_to_raw_s3_with_content_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "export.zip"
    artifact.write_bytes(b"zip-bytes")
    storage = SimpleNamespace(write_raw_chunks_to_s3=AsyncMock(return_value=("a" * 64, 9)))
    monkeypatch.setattr(export_jobs, "FileStorageService", lambda db: storage)

    sha256, size = await export_jobs.upload_solution_export_artifact(
        SimpleNamespace(),
        "solution-exports/solution/job.zip",
        artifact,
    )

    storage.write_raw_chunks_to_s3.assert_awaited_once()
    args, kwargs = storage.write_raw_chunks_to_s3.await_args
    assert args[0] == "solution-exports/solution/job.zip"
    assert kwargs["content_type"] == "application/zip"
    chunks = [chunk async for chunk in args[1]]
    assert chunks == [b"zip-bytes"]
    assert sha256 == "a" * 64
    assert size == 9


@pytest.mark.asyncio
async def test_delete_artifact_deletes_raw_s3_key(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = SimpleNamespace(delete_raw_from_s3=AsyncMock())
    monkeypatch.setattr(export_jobs, "FileStorageService", lambda db: storage)

    await export_jobs.delete_solution_export_artifact(
        SimpleNamespace(),
        "solution-exports/solution/job.zip",
    )

    storage.delete_raw_from_s3.assert_awaited_once_with("solution-exports/solution/job.zip")


@pytest.mark.asyncio
async def test_build_zip_overlays_stored_source_with_live_backup_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(id=uuid4(), slug="helpdesk-pack")
    options = SolutionExportOptions(
        include_configs=True,
        include_secrets=False,
        include_tables=True,
        include_files=False,
        password="pw",
    )
    bundle = SimpleNamespace(config_values={})

    class Artifact:
        async def copy_to_path(self, path: Path) -> bool:
            path.write_bytes(b"source")
            return True

    capture = SimpleNamespace(bundle_for=AsyncMock(return_value=bundle))
    overlay = AsyncMock()
    builder = AsyncMock()
    monkeypatch.setattr(export_jobs, "SolutionSourceArtifactStorage", lambda solution_id: Artifact())
    monkeypatch.setattr(export_jobs, "SolutionCaptureService", lambda db: capture)
    monkeypatch.setattr(export_jobs, "add_live_content_to_workspace_zip_file", overlay)
    monkeypatch.setattr(export_jobs, "build_workspace_zip_for_export", builder)

    dest = tmp_path / "out.zip"
    await export_jobs.build_solution_backup_zip_to_path(SimpleNamespace(), solution, options, dest)

    capture.bundle_for.assert_awaited_once_with(
        solution,
        include_imports=True,
        include_values=True,
        include_data=True,
        include_files=False,
    )
    overlay.assert_awaited_once()
    assert overlay.await_args.kwargs["password"] == "pw"
    builder.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_zip_captures_live_bundle_when_no_stored_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = Solution(id=uuid4(), slug="helpdesk-pack")
    options = SolutionExportOptions(
        include_configs=False,
        include_secrets=False,
        include_tables=True,
        include_files=False,
        password="pw",
    )
    bundle = SimpleNamespace(config_values={})

    class Artifact:
        async def copy_to_path(self, path: Path) -> bool:
            return False

    capture = SimpleNamespace(bundle_for=AsyncMock(return_value=bundle))
    overlay = AsyncMock()
    builder = AsyncMock()
    monkeypatch.setattr(export_jobs, "SolutionSourceArtifactStorage", lambda solution_id: Artifact())
    monkeypatch.setattr(export_jobs, "SolutionCaptureService", lambda db: capture)
    monkeypatch.setattr(export_jobs, "add_live_content_to_workspace_zip_file", overlay)
    monkeypatch.setattr(export_jobs, "build_workspace_zip_for_export", builder)

    dest = tmp_path / "out.zip"
    await export_jobs.build_solution_backup_zip_to_path(SimpleNamespace(), solution, options, dest)

    builder.assert_awaited_once()
    assert builder.await_args.args[0] is bundle
    assert builder.await_args.args[2] == dest
    assert builder.await_args.kwargs["password"] == "pw"
    overlay.assert_not_awaited()
