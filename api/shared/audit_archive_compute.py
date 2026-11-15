"""Bounded transfer contract for remote audit archive compression.

The API selects and snapshots audit rows; an isolated worker only turns that
snapshot into gzip segments.  The result is always checked against the API's
own snapshot before it can be stored or used to delete a row.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timezone
from pathlib import Path
from uuid import UUID

from shared.audit_archive_limits import BATCH_BYTES, BATCH_ROWS
from shared.worker_transfer import (
    FILE_TRANSFER_VERSION,
    MAX_CHUNK_BYTES,
    FileTransfer,
    FileTransferManifest,
    ReceivedFileTransfer,
    validate_file_transfer_chunk_size,
)
from src.services.audit_retention.format import (
    ArchiveRow,
    ArchiveVerifyError,
    Segment,
    encode_line,
    line_size,
    verify_segment,
)


MIB = 1024 * 1024
# The database selector and isolated codec share these pure bounds without
# importing the database archiver into the child process.
MAX_ARCHIVE_ROWS = BATCH_ROWS
MAX_INPUT_EXPANDED_BYTES = BATCH_BYTES
# The selected API snapshot maximum has an explicit 16 MiB
# envelope allowance bounds stored ZIP framing and a many-segment gzip result.
MAX_INPUT_TRANSFER_BYTES = MAX_INPUT_EXPANDED_BYTES + 16 * MIB
MAX_RESULT_TRANSFER_BYTES = MAX_INPUT_EXPANDED_BYTES + 16 * MIB
MAX_METADATA_BYTES = 64 * 1024
MAX_RESULT_MEMBERS = MAX_ARCHIVE_ROWS

AUDIT_ARCHIVE_INPUT_KIND = "audit_archive_inputs"
AUDIT_ARCHIVE_RESULT_KIND = "audit_archive_results"
AUDIT_ARCHIVE_OPERATION = "audit.archive.v1"
_MANIFEST_MEMBER = "manifest.json"
_ROWS_MEMBER = "rows.jsonl"
_SEGMENT_PREFIX = "segments/"


class AuditArchiveComputeError(ValueError):
    """An archive compute transfer is malformed or exceeds its budget."""


@dataclass(frozen=True)
class ArchiveSegmentExpectation:
    organization_id: UUID | None
    day: date
    rows: tuple[ArchiveRow, ...]


def encode_audit_archive_inputs(
    rows: Sequence[ArchiveRow], *, chunk_size: int = MAX_CHUNK_BYTES
) -> FileTransfer:
    """Spool a bounded canonical row snapshot for the isolated compressor."""
    validate_file_transfer_chunk_size(chunk_size)
    ordered = _validated_rows(rows)
    metadata = {
        "version": FILE_TRANSFER_VERSION,
        "kind": AUDIT_ARCHIVE_INPUT_KIND,
        "row_count": len(ordered),
    }
    _validate_input_metadata(metadata)
    fd, name = tempfile.mkstemp(prefix="bifrost-audit-archive-input-")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(
            raw, "w", compression=zipfile.ZIP_STORED, allowZip64=False
        ) as archive:
            archive.writestr(_MANIFEST_MEMBER, _json_bytes(metadata))
            with archive.open(_ROWS_MEMBER, "w") as output:
                for row in ordered:
                    output.write(encode_line(row))
        return _transfer_from_path(path, AUDIT_ARCHIVE_INPUT_KIND, chunk_size=chunk_size)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def decode_audit_archive_inputs(transfer: ReceivedFileTransfer) -> tuple[ArchiveRow, ...]:
    """Decode the canonical snapshot supplied by the API to the child."""
    metadata, archive = _open_archive(
        transfer,
        expected_kind=AUDIT_ARCHIVE_INPUT_KIND,
        expected_members={_MANIFEST_MEMBER, _ROWS_MEMBER},
        max_total_size=MAX_INPUT_TRANSFER_BYTES,
    )
    try:
        _validate_input_metadata(metadata)
        info = archive.getinfo(_ROWS_MEMBER)
        if info.file_size > MAX_INPUT_EXPANDED_BYTES:
            raise AuditArchiveComputeError("Audit archive input exceeds the expanded byte limit.")
        raw = archive.read(_ROWS_MEMBER)
    finally:
        archive.close()
    if len(raw) != info.file_size:
        raise AuditArchiveComputeError("Audit archive input is incomplete.")
    try:
        lines = raw.split(b"\n")
        if lines and not lines[-1]:
            lines.pop()
        rows = tuple(ArchiveRow.from_line(json.loads(line)) for line in lines)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        raise AuditArchiveComputeError("Audit archive input rows are invalid.") from exc
    rows = _validated_rows(rows)
    if metadata["row_count"] != len(rows):
        raise AuditArchiveComputeError("Audit archive input row count does not match its metadata.")
    return rows


def encode_audit_archive_results(
    segments: Sequence[Segment], *, chunk_size: int = MAX_CHUNK_BYTES
) -> FileTransfer:
    """Spool only gzip bodies made by the worker, never worker delete metadata."""
    validate_file_transfer_chunk_size(chunk_size)
    if len(segments) > MAX_RESULT_MEMBERS:
        raise AuditArchiveComputeError("Audit archive result has too many segments.")
    if sum(sum(line_size(row) for row in segment.rows) for segment in segments) > MAX_INPUT_EXPANDED_BYTES:
        raise AuditArchiveComputeError("Audit archive result exceeds the expanded byte limit.")
    metadata = {
        "version": FILE_TRANSFER_VERSION,
        "kind": AUDIT_ARCHIVE_RESULT_KIND,
        "segment_count": len(segments),
    }
    _validate_result_metadata(metadata)
    return _write_transfer(
        AUDIT_ARCHIVE_RESULT_KIND,
        metadata,
        [(_segment_member(index), segment.body) for index, segment in enumerate(segments)],
        chunk_size=chunk_size,
    )


def decode_audit_archive_results(
    transfer: ReceivedFileTransfer, *, expected_rows: Sequence[ArchiveRow]
) -> tuple[Segment, ...]:
    """Derive identities from API rows and validate every worker gzip body."""
    expectations = expected_archive_segments(expected_rows)
    expected_members = {_MANIFEST_MEMBER, *(_segment_member(i) for i in range(len(expectations)))}
    metadata, archive = _open_archive(
        transfer,
        expected_kind=AUDIT_ARCHIVE_RESULT_KIND,
        expected_members=expected_members,
        max_total_size=MAX_RESULT_TRANSFER_BYTES,
    )
    try:
        _validate_result_metadata(metadata)
        if metadata["segment_count"] != len(expectations):
            raise AuditArchiveComputeError("Audit archive result segment count does not match the API snapshot.")
        segments: list[Segment] = []
        for index, expected in enumerate(expectations):
            info = archive.getinfo(_segment_member(index))
            if info.file_size > MAX_RESULT_TRANSFER_BYTES:
                raise AuditArchiveComputeError("Audit archive result segment exceeds the size limit.")
            body = archive.read(info)
            if len(body) != info.file_size:
                raise AuditArchiveComputeError("Audit archive result segment is incomplete.")
            sha256 = hashlib.sha256(body).hexdigest()
            try:
                verify_segment(body, sha256=sha256, expected_rows=expected.rows)
            except ArchiveVerifyError as exc:
                raise AuditArchiveComputeError(
                    "Audit archive result does not match the API snapshot."
                ) from exc
            segments.append(Segment(expected.organization_id, expected.day, expected.rows, body, sha256))
        return tuple(segments)
    finally:
        archive.close()


def expected_archive_segments(rows: Sequence[ArchiveRow]) -> tuple[ArchiveSegmentExpectation, ...]:
    """Group the API snapshot without compressing it in the API process."""
    groups: dict[tuple[UUID | None, date], list[ArchiveRow]] = {}
    for row in _validated_rows(rows):
        groups.setdefault(
            (row.organization_id, row.created_at.astimezone(timezone.utc).date()), []
        ).append(row)
    return tuple(
        ArchiveSegmentExpectation(org, day, tuple(sorted(members, key=lambda row: (row.created_at, row.id))))
        for (org, day), members in sorted(groups.items(), key=lambda item: (item[0][1], str(item[0][0])))
    )


def _write_transfer(
    kind: str,
    metadata: dict[str, object],
    members: Sequence[tuple[str, bytes]],
    *,
    chunk_size: int,
) -> FileTransfer:
    fd, name = tempfile.mkstemp(prefix="bifrost-audit-archive-result-")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(
            raw, "w", compression=zipfile.ZIP_STORED, allowZip64=False
        ) as archive:
            archive.writestr(_MANIFEST_MEMBER, _json_bytes(metadata))
            for member, body in members:
                archive.writestr(member, body)
        return _transfer_from_path(path, kind, chunk_size=chunk_size)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _transfer_from_path(path: Path, kind: str, *, chunk_size: int) -> FileTransfer:
    limit = MAX_INPUT_TRANSFER_BYTES if kind == AUDIT_ARCHIVE_INPUT_KIND else MAX_RESULT_TRANSFER_BYTES
    size, digest = _file_size_and_digest(path, limit=limit)
    manifest = FileTransferManifest(
        version=FILE_TRANSFER_VERSION,
        kind=kind,
        total_size=size,
        sha256=digest,
        chunk_size=chunk_size,
        chunk_count=(size + chunk_size - 1) // chunk_size,
        max_total_size=limit,
    )
    manifest.validate()
    return FileTransfer(manifest, path)


def _open_archive(
    transfer: ReceivedFileTransfer,
    *,
    expected_kind: str,
    expected_members: set[str],
    max_total_size: int,
) -> tuple[dict[str, object], zipfile.ZipFile]:
    if transfer.manifest.kind != expected_kind or transfer.manifest.total_size > max_total_size:
        raise AuditArchiveComputeError("Audit archive transfer kind or size is invalid.")
    archive: zipfile.ZipFile | None = None
    try:
        archive = zipfile.ZipFile(transfer.path)
        infos = archive.infolist()
        if len(infos) != len(expected_members):
            raise AuditArchiveComputeError("Audit archive transfer has unsupported members.")
        seen: set[str] = set()
        for info in infos:
            _validate_archive_info(info, seen)
        if seen != expected_members:
            raise AuditArchiveComputeError("Audit archive transfer has unsupported members.")
        manifest_info = archive.getinfo(_MANIFEST_MEMBER)
        if manifest_info.file_size > MAX_METADATA_BYTES:
            raise AuditArchiveComputeError("Audit archive transfer metadata exceeds the size limit.")
        metadata = json.loads(archive.read(_MANIFEST_MEMBER))
        if not isinstance(metadata, dict):
            raise AuditArchiveComputeError("Audit archive transfer metadata is invalid.")
        if metadata.get("version") != FILE_TRANSFER_VERSION or metadata.get("kind") != expected_kind:
            raise AuditArchiveComputeError("Audit archive transfer metadata version does not match.")
        return metadata, archive
    except AuditArchiveComputeError:
        if archive is not None:
            archive.close()
        raise
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        if archive is not None:
            archive.close()
        raise AuditArchiveComputeError("Audit archive transfer is not a valid ZIP archive.") from exc


def _validate_archive_info(info: zipfile.ZipInfo, seen: set[str]) -> None:
    if (
        info.is_dir()
        or info.flag_bits & 0x1
        or stat.S_ISLNK(info.external_attr >> 16)
        or info.compress_type != zipfile.ZIP_STORED
        or info.filename in seen
    ):
        raise AuditArchiveComputeError("Audit archive transfer has an unsafe or duplicate member.")
    seen.add(info.filename)


def _validated_rows(rows: Sequence[ArchiveRow]) -> tuple[ArchiveRow, ...]:
    if len(rows) > MAX_ARCHIVE_ROWS:
        raise AuditArchiveComputeError("Audit archive input has too many rows.")
    if any(not isinstance(row, ArchiveRow) for row in rows):
        raise AuditArchiveComputeError("Audit archive input rows are invalid.")
    if sum(line_size(row) for row in rows) > MAX_INPUT_EXPANDED_BYTES:
        raise AuditArchiveComputeError("Audit archive input exceeds the expanded byte limit.")
    return tuple(rows)


def _validate_input_metadata(metadata: dict[str, object]) -> None:
    if set(metadata) != {"version", "kind", "row_count"} or type(metadata.get("row_count")) is not int:
        raise AuditArchiveComputeError("Audit archive input metadata is invalid.")
    if not 0 <= metadata["row_count"] <= MAX_ARCHIVE_ROWS:
        raise AuditArchiveComputeError("Audit archive input metadata is invalid.")


def _validate_result_metadata(metadata: dict[str, object]) -> None:
    if set(metadata) != {"version", "kind", "segment_count"} or type(metadata.get("segment_count")) is not int:
        raise AuditArchiveComputeError("Audit archive result metadata is invalid.")
    if not 0 <= metadata["segment_count"] <= MAX_RESULT_MEMBERS:
        raise AuditArchiveComputeError("Audit archive result metadata is invalid.")


def _json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _segment_member(index: int) -> str:
    return f"{_SEGMENT_PREFIX}{index}.jsonl.gz"


def _file_size_and_digest(path: Path, *, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as archive:
        while data := archive.read(MAX_CHUNK_BYTES):
            total += len(data)
            if total > limit:
                raise AuditArchiveComputeError("Audit archive transfer exceeds the size limit.")
            digest.update(data)
    return total, digest.hexdigest()
