"""File-backed transfer contract for isolated Solution archive inspection.

The API retains the original staged backup for password-protected data.  This
codec gives the worker a disposable source-only snapshot, then returns only an
unencrypted source archive plus non-authoritative inspection metadata.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import shutil
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from shared.solution_archive import (
    MAX_SOLUTION_ARCHIVE_ENTRIES,
    MAX_SOLUTION_UNCOMPRESSED_BYTES,
    PAYLOAD_PREFIX,
    PreviewResult,
    SolutionArchiveLimits,
    extract_workspace,
    parse_workspace,
    validate_archive_size,
    validate_zip_members,
)
from shared.worker_transfer import (
    FILE_TRANSFER_VERSION,
    MAX_CHUNK_BYTES,
    FileTransfer,
    FileTransferManifest,
    ReceivedFileTransfer,
    validate_file_transfer_chunk_size,
)


MIB = 1024 * 1024
SOLUTION_ARCHIVE_INSPECT_OPERATION = "solution.archive.inspect.v1"
SOLUTION_ARCHIVE_INSPECT_INPUT_KIND = "solution_archive_inspect_inputs"
SOLUTION_ARCHIVE_INSPECT_RESULT_KIND = "solution_archive_inspect_results"
MAX_INPUT_TRANSFER_BYTES = MAX_SOLUTION_UNCOMPRESSED_BYTES + 16 * MIB
# Inspection metadata is an independent projection of the source archive.  It
# may contain large portable manifest declarations, so it keeps the established
# 1GiB source ceiling instead of quietly narrowing accepted Solutions.
MAX_RESULT_METADATA_BYTES = MAX_SOLUTION_UNCOMPRESSED_BYTES
MAX_RESULT_SOURCE_ARCHIVE_BYTES = MAX_SOLUTION_UNCOMPRESSED_BYTES + 16 * MIB
MAX_RESULT_TRANSFER_BYTES = (
    MAX_RESULT_SOURCE_ARCHIVE_BYTES + MAX_RESULT_METADATA_BYTES + 32 * MIB
)
INSPECT_SOURCE_LIMITS = SolutionArchiveLimits(
    archive_bytes=MAX_RESULT_SOURCE_ARCHIVE_BYTES,
)
_RESULT_MANIFEST_MEMBER = "manifest.json"
_SOURCE_ARCHIVE_MEMBER = "source.zip"
_SECRETS_MEMBER = ".bifrost/secrets.enc"
_COPY_CHUNK_BYTES = MIB


class SolutionArchiveInspectError(ValueError):
    """A staged Solution archive or inspection result is invalid."""


@dataclass(frozen=True)
class SolutionArchiveInspectInput:
    path: Path
    sha256: str
    size: int


@dataclass(frozen=True)
class SolutionArchiveMember:
    """Non-authoritative ZIP member projection for API-side revalidation."""

    path: str
    size: int
    compressed_size: int
    crc: int


@dataclass
class SolutionArchiveInspection:
    """Decoded source archive; callers must close it after API-side validation."""

    preview: PreviewResult
    members: tuple[SolutionArchiveMember, ...]
    source_archive: Path
    source_index: Mapping[str, object]

    def close(self) -> None:
        self.source_archive.unlink(missing_ok=True)

    def cancel(self) -> None:
        self.close()

    def __enter__(self) -> "SolutionArchiveInspection":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def materialize_solution_archive_preview(
    inspection: SolutionArchiveInspection,
) -> PreviewResult:
    """Restore source-bearing preview fields from the returned source ZIP.

    This avoids rerunning workspace collectors in the API: the worker has
    already parsed the portable manifest and returned a bounded reference index.
    Individual source members are read only when the API needs the full bundle.
    """
    values = _preview_values(inspection.preview)
    workflows = _preview_rows(values["workflows"])
    apps = _preview_rows(values["apps"])
    index = inspection.source_index
    with zipfile.ZipFile(inspection.source_archive) as archive:
        for reference in _index_list(index, "workflows"):
            position, path = _index_position_path(reference, len(workflows))
            workflow = workflows[position]
            if workflow.get("path") != path:
                raise SolutionArchiveInspectError(
                    "Solution archive inspection workflow index is invalid."
                )
            workflow["source"] = _read_text_member(archive, path)
        for reference in _index_list(index, "apps"):
            position, path = _index_position_path(reference, len(apps))
            app = apps[position]
            if app.get("path") != path:
                raise SolutionArchiveInspectError(
                    "Solution archive inspection app index is invalid."
                )
            text_files = _index_paths(reference, "text")
            binary_files = _index_paths(reference, "binary")
            app["src_files"] = {
                item: _read_text_member(archive, _app_member_path(path, item))
                for item in text_files
            }
            app["bin_files"] = {
                item: _base64_member(archive, _app_member_path(path, item))
                for item in binary_files
            }
            logo = reference.get("logo")
            if logo is None:
                app["logo_b64"] = None
            elif isinstance(logo, str) and logo in {*text_files, *binary_files}:
                app["logo_b64"] = _base64_member(
                    archive, _app_member_path(path, logo)
                )
            else:
                raise SolutionArchiveInspectError(
                    "Solution archive inspection app logo index is invalid."
                )
        if index.get("readme") is True:
            values["readme"] = _read_text_member(archive, "README.md")
    return PreviewResult(**values)


def encode_solution_archive_inspect_inputs(
    archive_path: Path, *, chunk_size: int = MAX_CHUNK_BYTES
) -> FileTransfer:
    """Create a bounded source-only snapshot; encrypted backup data stays API-owned."""
    validate_file_transfer_chunk_size(chunk_size)
    fd, name = tempfile.mkstemp(prefix=f".{archive_path.name}.inspect-", dir=archive_path.parent)
    os.close(fd)
    transfer_path = Path(name)
    try:
        raw_size = archive_path.stat().st_size
        with zipfile.ZipFile(archive_path) as original:
            validate_archive_size(original, raw_size)
            members = validate_zip_members(original)
            with zipfile.ZipFile(transfer_path, "w", compression=zipfile.ZIP_STORED) as snapshot:
                for info in members:
                    if info.is_dir() or info.filename == _SECRETS_MEMBER or info.filename.startswith(PAYLOAD_PREFIX):
                        continue
                    if not _safe_member_path(info.filename):
                        raise SolutionArchiveInspectError("Solution archive inspection input has an unsafe path.")
                    with original.open(info) as source, snapshot.open(info.filename, "w") as output:
                        while chunk := source.read(_COPY_CHUNK_BYTES):
                            output.write(chunk)
        size, digest = _file_size_and_digest(transfer_path, MAX_INPUT_TRANSFER_BYTES)
        _validate_input_archive(transfer_path, size)
        return _transfer_from_path(
            transfer_path,
            SOLUTION_ARCHIVE_INSPECT_INPUT_KIND,
            size=size,
            digest=digest,
            chunk_size=chunk_size,
            max_total_size=MAX_INPUT_TRANSFER_BYTES,
        )
    except BaseException:
        transfer_path.unlink(missing_ok=True)
        raise


def decode_solution_archive_inspect_inputs(
    transfer: ReceivedFileTransfer,
) -> SolutionArchiveInspectInput:
    """Verify the raw staged ZIP and retain its file-backed worker path."""
    manifest = transfer.manifest
    if manifest.kind != SOLUTION_ARCHIVE_INSPECT_INPUT_KIND:
        raise SolutionArchiveInspectError("Solution archive inspection input kind is invalid.")
    if transfer.path.stat().st_size != manifest.total_size:
        raise SolutionArchiveInspectError("Solution archive inspection input size is invalid.")
    size, digest = _file_size_and_digest(transfer.path, MAX_INPUT_TRANSFER_BYTES)
    if size != manifest.total_size or digest != manifest.sha256:
        raise SolutionArchiveInspectError("Solution archive inspection input digest is invalid.")
    _validate_input_archive(transfer.path, size)
    return SolutionArchiveInspectInput(transfer.path, digest, size)


def build_solution_archive_inspect_result(
    inputs: SolutionArchiveInspectInput,
    *,
    chunk_size: int = MAX_CHUNK_BYTES,
) -> FileTransfer:
    """Inspect raw ZIP input and return an unencrypted file-backed source ZIP."""
    validate_file_transfer_chunk_size(chunk_size)
    with tempfile.TemporaryDirectory(prefix="bifrost-solution-inspect-") as temporary:
        workspace = Path(temporary) / "workspace"
        source_archive = Path(temporary) / "source.zip"
        workspace.mkdir()
        with zipfile.ZipFile(inputs.path) as archive:
            validate_archive_size(archive, inputs.size)
            members = tuple(_member(info) for info in validate_zip_members(archive))
            extract_workspace(archive, str(workspace))
        preview, source_index = _project_preview(parse_workspace(workspace))
        _write_source_archive(workspace, source_archive)
        return encode_solution_archive_inspect_results(
            inputs,
            preview,
            members,
            source_archive,
            source_index=source_index,
            chunk_size=chunk_size,
        )


def encode_solution_archive_inspect_results(
    inputs: SolutionArchiveInspectInput,
    preview: PreviewResult,
    members: Sequence[SolutionArchiveMember],
    source_archive: Path,
    *,
    source_index: Mapping[str, object] | None = None,
    chunk_size: int = MAX_CHUNK_BYTES,
) -> FileTransfer:
    """Spool a bounded inspection envelope around a source-only ZIP archive."""
    validate_file_transfer_chunk_size(chunk_size)
    _validate_source_archive(source_archive)
    source_size, source_sha256 = _file_size_and_digest(
        source_archive, MAX_RESULT_SOURCE_ARCHIVE_BYTES
    )
    metadata = _result_metadata(
        inputs,
        preview,
        members,
        source_size,
        source_sha256,
        source_index=source_index or _empty_source_index(),
    )
    metadata_bytes = _json_bytes(metadata)
    if len(metadata_bytes) > MAX_RESULT_METADATA_BYTES:
        raise SolutionArchiveInspectError(
            "Solution archive inspection metadata exceeds its byte limit."
        )
    fd, name = tempfile.mkstemp(prefix="bifrost-solution-inspect-result-")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(
            raw, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            archive.writestr(_RESULT_MANIFEST_MEMBER, metadata_bytes)
            archive.write(
                source_archive,
                _SOURCE_ARCHIVE_MEMBER,
                compress_type=zipfile.ZIP_STORED,
            )
        size, digest = _file_size_and_digest(path, MAX_RESULT_TRANSFER_BYTES)
        return _transfer_from_path(
            path,
            SOLUTION_ARCHIVE_INSPECT_RESULT_KIND,
            size=size,
            digest=digest,
            chunk_size=chunk_size,
            max_total_size=MAX_RESULT_TRANSFER_BYTES,
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def decode_solution_archive_inspect_results(
    transfer: ReceivedFileTransfer,
    *,
    expected_input: FileTransferManifest,
) -> SolutionArchiveInspection:
    """Stream a verified source ZIP into a caller-owned inspection result."""
    if expected_input.kind != SOLUTION_ARCHIVE_INSPECT_INPUT_KIND:
        raise SolutionArchiveInspectError(
            "Solution archive inspection input kind is invalid."
        )
    if (
        transfer.manifest.kind != SOLUTION_ARCHIVE_INSPECT_RESULT_KIND
        or transfer.manifest.total_size > MAX_RESULT_TRANSFER_BYTES
    ):
        raise SolutionArchiveInspectError(
            "Solution archive inspection result kind or size is invalid."
        )
    source_path: Path | None = None
    try:
        with zipfile.ZipFile(transfer.path) as archive:
            infos = archive.infolist()
            seen: set[str] = set()
            if len(infos) != 2:
                raise SolutionArchiveInspectError(
                    "Solution archive inspection result has unsupported members."
                )
            info_by_name = {info.filename: info for info in infos}
            for info in infos:
                _validate_result_info(info, seen)
            metadata_info = info_by_name.get(_RESULT_MANIFEST_MEMBER)
            source_info = info_by_name.get(_SOURCE_ARCHIVE_MEMBER)
            if metadata_info is None or source_info is None:
                raise SolutionArchiveInspectError(
                    "Solution archive inspection result has unsupported members."
                )
            if metadata_info.file_size > MAX_RESULT_METADATA_BYTES:
                raise SolutionArchiveInspectError(
                    "Solution archive inspection metadata exceeds its byte limit."
                )
            metadata = _decode_result_metadata(archive.read(metadata_info), expected_input)
            source_size, source_sha256 = _source_values(metadata)
            if (
                source_info.file_size != source_size
                or source_size > MAX_RESULT_SOURCE_ARCHIVE_BYTES
            ):
                raise SolutionArchiveInspectError(
                    "Solution archive inspection source archive is invalid."
                )
            source_path = _stream_member_to_temp(archive, source_info, source_sha256)
        _validate_source_archive(source_path)
        preview = _preview_from_json(metadata["preview"])
        members = _members_from_json(metadata["members"])
        source_index = _source_index_from_json(metadata["source_index"])
        return SolutionArchiveInspection(preview, members, source_path, source_index)
    except SolutionArchiveInspectError:
        if source_path is not None:
            source_path.unlink(missing_ok=True)
        raise
    except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        if source_path is not None:
            source_path.unlink(missing_ok=True)
        raise SolutionArchiveInspectError(
            "Solution archive inspection result is not a valid ZIP archive."
        ) from exc


def _validate_input_archive(path: Path, size: int) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            validate_archive_size(archive, size)
            validate_zip_members(archive)
    except SolutionArchiveInspectError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, ValueError) as exc:
        raise SolutionArchiveInspectError(
            "Solution archive inspection input is not a valid ZIP archive."
        ) from exc


def _write_source_archive(workspace: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as archive:
        for path in sorted(workspace.rglob("*")):
            if path.is_dir():
                continue
            if path.is_symlink() or not path.is_file():
                raise SolutionArchiveInspectError(
                    "Solution workspace contains an unsafe source path."
                )
            relative = path.relative_to(workspace).as_posix()
            if relative == _SECRETS_MEMBER or relative.startswith(PAYLOAD_PREFIX):
                continue
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as output:
                shutil.copyfileobj(source, output, _COPY_CHUNK_BYTES)


def _project_preview(preview: PreviewResult) -> tuple[PreviewResult, dict[str, object]]:
    """Remove duplicated source bodies while preserving their exact ZIP refs."""
    workflows: list[dict[str, object]] = []
    workflow_index: list[dict[str, object]] = []
    for position, value in enumerate(preview.workflows):
        row = dict(value)
        source = row.pop("source", None)
        path = row.get("path")
        if source is not None:
            if not isinstance(path, str) or not isinstance(source, str):
                raise SolutionArchiveInspectError(
                    "Solution archive inspection workflow projection is invalid."
                )
            workflow_index.append({"position": position, "path": path})
        workflows.append(row)

    apps: list[dict[str, object]] = []
    app_index: list[dict[str, object]] = []
    for position, value in enumerate(preview.apps):
        row = dict(value)
        path = row.get("path")
        source_files = row.pop("src_files", {})
        binary_files = row.pop("bin_files", {})
        logo_b64 = row.pop("logo_b64", None)
        if (
            not isinstance(path, str)
            or not isinstance(source_files, dict)
            or not isinstance(binary_files, dict)
        ):
            raise SolutionArchiveInspectError(
                "Solution archive inspection app projection is invalid."
            )
        text = _mapping_paths(source_files)
        binary = _mapping_paths(binary_files)
        logo = _logo_path(logo_b64, source_files, binary_files)
        app_index.append(
            {
                "position": position,
                "path": path,
                "text": text,
                "binary": binary,
                "logo": logo,
            }
        )
        apps.append(row)

    readme = preview.readme
    if readme is not None and not isinstance(readme, str):
        raise SolutionArchiveInspectError(
            "Solution archive inspection README projection is invalid."
        )
    return (
        PreviewResult(
            slug=preview.slug,
            name=preview.name,
            scope=preview.scope,
            version=preview.version,
            logo=preview.logo,
            workflows=workflows,
            tables=list(preview.tables),
            apps=apps,
            forms=list(preview.forms),
            agents=list(preview.agents),
            claims=list(preview.claims),
            config_schemas=list(preview.config_schemas),
            file_locations=list(preview.file_locations),
            file_policies=list(preview.file_policies),
            connection_schemas=list(preview.connection_schemas),
            events=list(preview.events),
            readme=None,
            requires_password=preview.requires_password,
        ),
        {
            "workflows": workflow_index,
            "apps": app_index,
            "readme": readme is not None,
        },
    )


def _mapping_paths(value: Mapping[object, object]) -> list[str]:
    paths = sorted(value)
    if any(not isinstance(path, str) or not _safe_member_path(path) for path in paths):
        raise SolutionArchiveInspectError(
            "Solution archive inspection app projection is invalid."
        )
    return paths


def _logo_path(
    logo_b64: object,
    source_files: Mapping[object, object],
    binary_files: Mapping[object, object],
) -> str | None:
    if logo_b64 is None:
        return None
    if not isinstance(logo_b64, str):
        raise SolutionArchiveInspectError(
            "Solution archive inspection app logo projection is invalid."
        )
    for path, value in source_files.items():
        if isinstance(path, str) and isinstance(value, str):
            if base64.b64encode(value.encode("utf-8")).decode("ascii") == logo_b64:
                return path
    for path, value in binary_files.items():
        if isinstance(path, str) and value == logo_b64:
            return path
    raise SolutionArchiveInspectError(
        "Solution archive inspection app logo projection is invalid."
    )


def _validate_source_archive(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_RESULT_SOURCE_ARCHIVE_BYTES:
        raise SolutionArchiveInspectError(
            "Solution archive inspection source archive exceeds its byte limit."
        )
    try:
        with zipfile.ZipFile(path) as archive:
            members = validate_zip_members(archive, limits=INSPECT_SOURCE_LIMITS)
    except (OSError, RuntimeError, zipfile.BadZipFile, ValueError) as exc:
        raise SolutionArchiveInspectError(
            "Solution archive inspection source archive is invalid."
        ) from exc
    if any(
        member.filename == _SECRETS_MEMBER
        or member.filename.startswith(PAYLOAD_PREFIX)
        for member in members
    ):
        raise SolutionArchiveInspectError(
            "Solution archive inspection source archive contains encrypted payloads."
        )


def _result_metadata(
    inputs: SolutionArchiveInspectInput,
    preview: PreviewResult,
    members: Sequence[SolutionArchiveMember],
    source_size: int,
    source_sha256: str,
    *,
    source_index: Mapping[str, object],
) -> dict[str, object]:
    if len(members) > MAX_SOLUTION_ARCHIVE_ENTRIES:
        raise SolutionArchiveInspectError(
            "Solution archive inspection has too many members."
        )
    return {
        "version": FILE_TRANSFER_VERSION,
        "kind": SOLUTION_ARCHIVE_INSPECT_RESULT_KIND,
        "input_sha256": inputs.sha256,
        "input_size": inputs.size,
        "source_sha256": source_sha256,
        "source_size": source_size,
        "preview": asdict(preview),
        "members": [asdict(member) for member in members],
        "source_index": dict(source_index),
    }


def _decode_result_metadata(
    raw: bytes, expected_input: FileTransferManifest
) -> dict[str, object]:
    try:
        metadata = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SolutionArchiveInspectError(
            "Solution archive inspection metadata is invalid."
        ) from exc
    if not isinstance(metadata, dict) or set(metadata) != {
        "version",
        "kind",
        "input_sha256",
        "input_size",
        "source_sha256",
        "source_size",
        "preview",
        "members",
        "source_index",
    }:
        raise SolutionArchiveInspectError(
            "Solution archive inspection metadata is invalid."
        )
    if (
        metadata["version"] != FILE_TRANSFER_VERSION
        or metadata["kind"] != SOLUTION_ARCHIVE_INSPECT_RESULT_KIND
    ):
        raise SolutionArchiveInspectError(
            "Solution archive inspection metadata is invalid."
        )
    if (
        metadata["input_sha256"] != expected_input.sha256
        or metadata["input_size"] != expected_input.total_size
    ):
        raise SolutionArchiveInspectError(
            "Solution archive inspection result does not match its input snapshot."
        )
    _source_values(metadata)
    _preview_from_json(metadata["preview"])
    _members_from_json(metadata["members"])
    _source_index_from_json(metadata["source_index"])
    return metadata


def _source_values(metadata: Mapping[str, object]) -> tuple[int, str]:
    size, digest = metadata.get("source_size"), metadata.get("source_sha256")
    if type(size) is not int or not 0 < size <= MAX_RESULT_SOURCE_ARCHIVE_BYTES:
        raise SolutionArchiveInspectError("Solution archive inspection source archive is invalid.")
    if not isinstance(digest, str) or len(digest) != 64:
        raise SolutionArchiveInspectError("Solution archive inspection source archive is invalid.")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise SolutionArchiveInspectError(
            "Solution archive inspection source archive is invalid."
        ) from exc
    return size, digest


def _preview_from_json(value: object) -> PreviewResult:
    if not isinstance(value, dict) or set(value) != {
        "slug",
        "name",
        "scope",
        "version",
        "logo",
        "workflows",
        "tables",
        "apps",
        "forms",
        "agents",
        "claims",
        "config_schemas",
        "file_locations",
        "file_policies",
        "connection_schemas",
        "events",
        "readme",
        "requires_password",
    }:
        raise SolutionArchiveInspectError("Solution archive inspection preview is invalid.")
    text_fields = ("slug", "name", "scope", "version", "logo", "readme")
    if any(
        value[field] is not None and not isinstance(value[field], str)
        for field in text_fields
    ):
        raise SolutionArchiveInspectError("Solution archive inspection preview is invalid.")
    list_fields = (
        "workflows",
        "tables",
        "apps",
        "forms",
        "agents",
        "claims",
        "config_schemas",
        "file_locations",
        "file_policies",
        "connection_schemas",
        "events",
    )
    if (
        any(not isinstance(value[field], list) for field in list_fields)
        or type(value["requires_password"]) is not bool
    ):
        raise SolutionArchiveInspectError("Solution archive inspection preview is invalid.")
    mapping_lists = set(list_fields) - {"file_locations"}
    if any(
        any(not isinstance(item, dict) for item in value[field])
        for field in mapping_lists
    ):
        raise SolutionArchiveInspectError("Solution archive inspection preview is invalid.")
    if any(not isinstance(item, str) for item in value["file_locations"]):
        raise SolutionArchiveInspectError("Solution archive inspection preview is invalid.")
    return PreviewResult(**value)


def _empty_source_index() -> dict[str, object]:
    return {"workflows": [], "apps": [], "readme": False}


def _source_index_from_json(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"workflows", "apps", "readme"}:
        raise SolutionArchiveInspectError("Solution archive inspection source index is invalid.")
    if type(value["readme"]) is not bool:
        raise SolutionArchiveInspectError("Solution archive inspection source index is invalid.")
    workflows = _validated_index_rows(value["workflows"], app=False)
    apps = _validated_index_rows(value["apps"], app=True)
    return {"workflows": workflows, "apps": apps, "readme": value["readme"]}


def _validated_index_rows(value: object, *, app: bool) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > MAX_SOLUTION_ARCHIVE_ENTRIES:
        raise SolutionArchiveInspectError("Solution archive inspection source index is invalid.")
    expected = {"position", "path"}
    if app:
        expected |= {"text", "binary", "logo"}
    rows: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != expected:
            raise SolutionArchiveInspectError(
                "Solution archive inspection source index is invalid."
            )
        position, path = raw["position"], raw["path"]
        if type(position) is not int or position < 0 or not isinstance(path, str):
            raise SolutionArchiveInspectError(
                "Solution archive inspection source index is invalid."
            )
        if not _safe_member_path(path):
            raise SolutionArchiveInspectError(
                "Solution archive inspection source index is invalid."
            )
        if app:
            text = _validated_index_paths(raw["text"])
            binary = _validated_index_paths(raw["binary"])
            logo = raw["logo"]
            if logo is not None and (not isinstance(logo, str) or logo not in {*text, *binary}):
                raise SolutionArchiveInspectError(
                    "Solution archive inspection source index is invalid."
                )
            rows.append(
                {
                    "position": position,
                    "path": path,
                    "text": text,
                    "binary": binary,
                    "logo": logo,
                }
            )
        else:
            rows.append({"position": position, "path": path})
    return rows


def _validated_index_paths(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_SOLUTION_ARCHIVE_ENTRIES:
        raise SolutionArchiveInspectError("Solution archive inspection source index is invalid.")
    if (
        any(not isinstance(path, str) or not _safe_member_path(path) for path in value)
        or len(set(value)) != len(value)
    ):
        raise SolutionArchiveInspectError("Solution archive inspection source index is invalid.")
    return list(value)


def _preview_values(preview: PreviewResult) -> dict[str, object]:
    return {
        "slug": preview.slug,
        "name": preview.name,
        "scope": preview.scope,
        "version": preview.version,
        "logo": preview.logo,
        "workflows": [dict(row) for row in preview.workflows],
        "tables": list(preview.tables),
        "apps": [dict(row) for row in preview.apps],
        "forms": list(preview.forms),
        "agents": list(preview.agents),
        "claims": list(preview.claims),
        "config_schemas": list(preview.config_schemas),
        "file_locations": list(preview.file_locations),
        "file_policies": list(preview.file_policies),
        "connection_schemas": list(preview.connection_schemas),
        "events": list(preview.events),
        "readme": preview.readme,
        "requires_password": preview.requires_password,
    }


def _preview_rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SolutionArchiveInspectError("Solution archive inspection preview is invalid.")
    return value


def _index_list(index: Mapping[str, object], key: str) -> list[dict[str, object]]:
    value = index.get(key)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SolutionArchiveInspectError("Solution archive inspection source index is invalid.")
    return value


def _index_position_path(
    reference: Mapping[str, object], length: int
) -> tuple[int, str]:
    position, path = reference.get("position"), reference.get("path")
    if (
        type(position) is not int
        or not 0 <= position < length
        or not isinstance(path, str)
        or not _safe_member_path(path)
    ):
        raise SolutionArchiveInspectError("Solution archive inspection source index is invalid.")
    return position, path


def _index_paths(reference: Mapping[str, object], key: str) -> list[str]:
    value = reference.get(key)
    return _validated_index_paths(value)


def _app_member_path(app_path: str, path: str) -> str:
    member = PurePosixPath(app_path) / PurePosixPath(path)
    value = member.as_posix()
    if not _safe_member_path(value):
        raise SolutionArchiveInspectError("Solution archive inspection app source path is invalid.")
    return value


def _read_text_member(archive: zipfile.ZipFile, path: str) -> str:
    try:
        return archive.read(path).decode("utf-8")
    except (KeyError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise SolutionArchiveInspectError(
            "Solution archive inspection source member is invalid."
        ) from exc


def _base64_member(archive: zipfile.ZipFile, path: str) -> str:
    try:
        return base64.b64encode(archive.read(path)).decode("ascii")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise SolutionArchiveInspectError(
            "Solution archive inspection source member is invalid."
        ) from exc


def _members_from_json(value: object) -> tuple[SolutionArchiveMember, ...]:
    if not isinstance(value, list) or len(value) > MAX_SOLUTION_ARCHIVE_ENTRIES:
        raise SolutionArchiveInspectError("Solution archive inspection members are invalid.")
    members: list[SolutionArchiveMember] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "path",
            "size",
            "compressed_size",
            "crc",
        }:
            raise SolutionArchiveInspectError("Solution archive inspection members are invalid.")
        path, size, compressed_size, crc = (
            raw["path"],
            raw["size"],
            raw["compressed_size"],
            raw["crc"],
        )
        if (
            not isinstance(path, str)
            or not _safe_member_path(path)
            or path in seen
            or any(
                type(number) is not int or number < 0
                for number in (size, compressed_size, crc)
            )
        ):
            raise SolutionArchiveInspectError("Solution archive inspection members are invalid.")
        seen.add(path)
        members.append(SolutionArchiveMember(path, size, compressed_size, crc))
    return tuple(members)


def _member(info: zipfile.ZipInfo) -> SolutionArchiveMember:
    return SolutionArchiveMember(
        info.filename, info.file_size, info.compress_size, info.CRC
    )


def _validate_result_info(info: zipfile.ZipInfo, seen: set[str]) -> None:
    if (
        info.is_dir()
        or info.flag_bits & 0x1
        or stat.S_ISLNK(info.external_attr >> 16)
        or info.filename in seen
        or not _safe_member_path(info.filename)
    ):
        raise SolutionArchiveInspectError(
            "Solution archive inspection result has an unsafe member."
        )
    seen.add(info.filename)


def _safe_member_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        bool(path)
        and "\\" not in path
        and not pure.is_absolute()
        and ".." not in pure.parts
    )


def _stream_member_to_temp(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, expected_sha256: str
) -> Path:
    fd, name = tempfile.mkstemp(prefix="bifrost-solution-inspect-source-", suffix=".zip")
    path = Path(name)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as destination, archive.open(info) as source:
            while chunk := source.read(_COPY_CHUNK_BYTES):
                destination.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise SolutionArchiveInspectError(
                "Solution archive inspection source digest is invalid."
            )
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _file_size_and_digest(path: Path, limit: int) -> tuple[int, str]:
    size = path.stat().st_size
    if not 0 < size <= limit:
        raise SolutionArchiveInspectError(
            "Solution archive inspection transfer exceeds its byte limit."
        )
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return size, digest.hexdigest()


def _transfer_from_path(
    path: Path,
    kind: str,
    *,
    size: int,
    digest: str,
    chunk_size: int,
    max_total_size: int,
) -> FileTransfer:
    manifest = FileTransferManifest(
        FILE_TRANSFER_VERSION,
        kind,
        size,
        digest,
        chunk_size,
        (size + chunk_size - 1) // chunk_size,
        max_total_size,
    )
    manifest.validate()
    return FileTransfer(manifest, path)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SolutionArchiveInspectError(
            "Solution archive inspection metadata is invalid."
        ) from exc
