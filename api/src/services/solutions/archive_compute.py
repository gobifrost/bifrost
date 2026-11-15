"""API-side verification for the non-authoritative Solution archive inspector."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from shared.file_compute import FileCompute, decode_file_result, encode_file_transfer
from shared.solution_archive import PreviewResult, parse_workspace, validate_archive_size, validate_zip_members
from shared.solution_archive_compute import (
    SOLUTION_ARCHIVE_INSPECT_OPERATION,
    SolutionArchiveInspectError,
    SolutionArchiveInspection,
    SolutionArchiveMember,
    decode_solution_archive_inspect_results,
    encode_solution_archive_inspect_inputs,
    materialize_solution_archive_preview,
)

_COPY_CHUNK_BYTES = 1024 * 1024


@dataclass
class VerifiedSolutionArchive:
    workspace: Path
    preview: PreviewResult
    _inspection: SolutionArchiveInspection

    def close(self) -> None:
        self._inspection.close()
        if self.workspace.exists():
            shutil.rmtree(self.workspace)


@asynccontextmanager
async def inspect_solution_archive(zip_path: Path, compute: FileCompute):
    """Return an API-verified source workspace from one isolated inspection."""
    transfer = await encode_file_transfer(encode_solution_archive_inspect_inputs, zip_path)
    try:
        outcome = await compute(SOLUTION_ARCHIVE_INSPECT_OPERATION, transfer)
    except BaseException:
        transfer.close()
        raise
    def decode_and_verify(received):
        try:
            inspection = decode_solution_archive_inspect_results(received, expected_input=transfer.manifest)
            try:
                return _verify_inspection(zip_path, transfer.path, inspection)
            except BaseException:
                inspection.close()
                raise
        finally:
            transfer.close()
    verified = await decode_file_result(
        outcome, decode_and_verify, cleanup_cancelled_result=VerifiedSolutionArchive.close
    )
    try:
        yield verified
    finally:
        verified.close()


def _verify_inspection(zip_path: Path, snapshot_path: Path, inspection: SolutionArchiveInspection) -> VerifiedSolutionArchive:
    workspace = Path(tempfile.mkdtemp(prefix="bifrost-solution-verified-"))
    try:
        with zipfile.ZipFile(zip_path) as original, zipfile.ZipFile(snapshot_path) as snapshot, zipfile.ZipFile(inspection.source_archive) as returned:
            validate_archive_size(original, zip_path.stat().st_size)
            original_members = validate_zip_members(original)
            snapshot_members = validate_zip_members(snapshot)
            expected = tuple(_member(item) for item in snapshot_members)
            if tuple(inspection.members) != expected:
                raise SolutionArchiveInspectError("Solution archive inspection members do not match input.")
            original_files = {
                item.filename: item for item in snapshot_members if not item.is_dir()
            }
            returned_members = validate_zip_members(returned)
            returned_files = {
                item.filename: item for item in returned_members if not item.is_dir()
            }
            if set(returned_files) != set(original_files):
                raise SolutionArchiveInspectError("Solution archive inspection source files do not match input.")
            for name, original_info in original_files.items():
                target = _workspace_target(workspace, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with snapshot.open(original_info) as expected_file, returned.open(returned_files[name]) as actual_file, target.open("wb") as output:
                    while True:
                        expected_chunk = expected_file.read(_COPY_CHUNK_BYTES)
                        actual_chunk = actual_file.read(_COPY_CHUNK_BYTES)
                        if expected_chunk != actual_chunk:
                            raise SolutionArchiveInspectError("Solution archive inspection source file does not match input.")
                        if not actual_chunk:
                            break
                        output.write(actual_chunk)
            for info in original_members:
                if info.filename != ".bifrost/secrets.enc":
                    continue
                target = _workspace_target(workspace, info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with original.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, _COPY_CHUNK_BYTES)
            canonical = parse_workspace(workspace, include_source=False)
            _verify_source_index(workspace, canonical, inspection.source_index)
            if _metadata(canonical, password=False) != _metadata(inspection.preview, password=False):
                raise SolutionArchiveInspectError("Solution archive inspection metadata does not match input.")
        preview = materialize_solution_archive_preview(inspection)
        preview.requires_password = any(
            item.filename == ".bifrost/secrets.enc" for item in original_members
        )
        return VerifiedSolutionArchive(workspace, preview, inspection)
    except BaseException:
        if workspace.exists():
            shutil.rmtree(workspace)
        raise


def _member(info: zipfile.ZipInfo) -> SolutionArchiveMember:
    return SolutionArchiveMember(info.filename, info.file_size, info.compress_size, info.CRC)


def _workspace_target(workspace: Path, name: str) -> Path:
    target = workspace.joinpath(*Path(name).parts)
    if os.path.commonpath((workspace, target.resolve())) != str(workspace):
        raise SolutionArchiveInspectError("Solution archive inspection source path is invalid.")
    return target


def _metadata(preview: PreviewResult, *, password: bool) -> dict[str, object]:
    values = dict(preview.__dict__)
    values["workflows"] = [{k: v for k, v in row.items() if k != "source"} for row in preview.workflows]
    values["apps"] = [
        {k: v for k, v in row.items() if k not in {"src_files", "bin_files", "logo_b64"}}
        for row in preview.apps
    ]
    values["readme"] = None
    values["requires_password"] = password
    return values


def _verify_source_index(workspace: Path, canonical: PreviewResult, index) -> None:
    """A byte-identical archive must not permit omitted or reclassified inputs."""
    from bifrost.solution_definition import read_solution_definition

    expected_workflows = [
        {"position": position, "path": row["path"]}
        for position, row in enumerate(canonical.workflows)
        if _workspace_target(workspace, row["path"]).is_file()
    ]
    if index["workflows"] != expected_workflows or index["readme"] != (workspace / "README.md").is_file():
        raise SolutionArchiveInspectError("Solution archive inspection source index does not match input.")
    app_refs = index["apps"]
    if len(app_refs) != len(canonical.apps):
        raise SolutionArchiveInspectError("Solution archive inspection app source index is incomplete.")
    definitions = read_solution_definition(workspace).entries("apps")
    for position, (app, reference, definition) in enumerate(zip(canonical.apps, app_refs, definitions, strict=True)):
        if (
            reference["position"] != position
            or reference["path"] != app["path"]
            or reference["text"] != sorted(app["src_files"])
            or reference["binary"] != sorted(app["bin_files"])
        ):
            raise SolutionArchiveInspectError("Solution archive inspection app source index does not match input.")
        declared_logo = definition.get("logo")
        returned_logo = reference["logo"]
        if bool(declared_logo) != (returned_logo is not None):
            raise SolutionArchiveInspectError("Solution archive inspection app logo index does not match input.")
        if declared_logo:
            expected_logo = _workspace_target(workspace, f"{app['path']}/{declared_logo}")
            actual_logo = _workspace_target(workspace, f"{app['path']}/{returned_logo}")
            if expected_logo.read_bytes() != actual_logo.read_bytes():
                raise SolutionArchiveInspectError("Solution archive inspection app logo index does not match input.")
