"""Bounded transfer contract for remote audit-export gzip members.

The API reads, verifies, authorizes, and filters audit rows.  The worker only
compresses one canonical bounded batch and returns a gzip JSONL member which
the API verifies against the exact rows before it reaches object storage.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from shared.audit_archive_limits import BATCH_BYTES, BATCH_ROWS
from shared.worker_transfer import (
    FILE_TRANSFER_VERSION,
    MAX_CHUNK_BYTES,
    FileTransfer,
    FileTransferManifest,
    ReceivedFileTransfer,
    validate_file_transfer_chunk_size,
)
from src.services.audit_retention.format import ArchiveRow, encode_line, line_size


MIB = 1024 * 1024
MAX_EXPORT_ROWS = BATCH_ROWS
MAX_EXPANDED_BYTES = BATCH_BYTES
MAX_INPUT_TRANSFER_BYTES = MAX_EXPANDED_BYTES + 16 * MIB
MAX_RESULT_TRANSFER_BYTES = MAX_EXPANDED_BYTES + 16 * MIB
MAX_METADATA_BYTES = 64 * 1024

AUDIT_EXPORT_OPERATION = "audit.export.v1"
AUDIT_EXPORT_INPUT_KIND = "audit_export_inputs"
AUDIT_EXPORT_RESULT_KIND = "audit_export_results"
_MANIFEST_MEMBER = "manifest.json"
_ROWS_MEMBER = "rows.jsonl"
_GZIP_MEMBER = "export.jsonl.gz"


class AuditExportComputeError(ValueError):
    """An audit-export transfer is malformed or does not match API input."""


def encode_audit_export_inputs(
    rows: Sequence[ArchiveRow], *, chunk_size: int = MAX_CHUNK_BYTES
) -> FileTransfer:
    """Spool one bounded API-selected row batch for remote compression."""
    validate_file_transfer_chunk_size(chunk_size)
    rows = _validated_rows(rows)
    metadata = {
        "version": FILE_TRANSFER_VERSION,
        "kind": AUDIT_EXPORT_INPUT_KIND,
        "row_count": len(rows),
    }
    _validate_input_metadata(metadata)
    return _write_transfer(
        AUDIT_EXPORT_INPUT_KIND,
        metadata,
        ((_ROWS_MEMBER, b"".join(encode_line(row) for row in rows)),),
        chunk_size=chunk_size,
    )


def decode_audit_export_inputs(transfer: ReceivedFileTransfer) -> tuple[ArchiveRow, ...]:
    """Decode the canonical API snapshot visible to the isolated child."""
    metadata, members = _read_transfer(
        transfer,
        expected_kind=AUDIT_EXPORT_INPUT_KIND,
        expected_members={_MANIFEST_MEMBER, _ROWS_MEMBER},
        max_total_size=MAX_INPUT_TRANSFER_BYTES,
        member_max_size=MAX_EXPANDED_BYTES,
    )
    _validate_input_metadata(metadata)
    raw = members[_ROWS_MEMBER]
    try:
        lines = raw.split(b"\n")
        if lines and not lines[-1]:
            lines.pop()
        rows = tuple(ArchiveRow.from_line(json.loads(line)) for line in lines)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        raise AuditExportComputeError("Audit export input rows are invalid.") from exc
    rows = _validated_rows(rows)
    if metadata["row_count"] != len(rows):
        raise AuditExportComputeError("Audit export input row count does not match its metadata.")
    return rows


def encode_audit_export_result(
    body: bytes, row_count: int, *, chunk_size: int = MAX_CHUNK_BYTES
) -> FileTransfer:
    """Spool one worker-produced gzip member without any worker authority data."""
    validate_file_transfer_chunk_size(chunk_size)
    if type(row_count) is not int or not 0 <= row_count <= MAX_EXPORT_ROWS:
        raise AuditExportComputeError("Audit export result row count is invalid.")
    if len(body) > MAX_RESULT_TRANSFER_BYTES:
        raise AuditExportComputeError("Audit export result exceeds the byte limit.")
    metadata = {
        "version": FILE_TRANSFER_VERSION,
        "kind": AUDIT_EXPORT_RESULT_KIND,
        "row_count": row_count,
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    _validate_result_metadata(metadata)
    return _write_transfer(
        AUDIT_EXPORT_RESULT_KIND,
        metadata,
        ((_GZIP_MEMBER, body),),
        chunk_size=chunk_size,
    )


def decode_audit_export_result(
    transfer: ReceivedFileTransfer, *, expected_rows: Sequence[ArchiveRow]
) -> bytes:
    """Verify the worker gzip member expands to exactly the API snapshot."""
    expected = _validated_rows(expected_rows)
    metadata, members = _read_transfer(
        transfer,
        expected_kind=AUDIT_EXPORT_RESULT_KIND,
        expected_members={_MANIFEST_MEMBER, _GZIP_MEMBER},
        max_total_size=MAX_RESULT_TRANSFER_BYTES,
        member_max_size=MAX_RESULT_TRANSFER_BYTES,
    )
    _validate_result_metadata(metadata)
    body = members[_GZIP_MEMBER]
    row_count, sha256 = _result_values(metadata)
    if row_count != len(expected) or sha256 != hashlib.sha256(body).hexdigest():
        raise AuditExportComputeError("Audit export result does not match the API snapshot.")
    expanded = _decompress_limited(body)
    if expanded != b"".join(encode_line(row) for row in expected):
        raise AuditExportComputeError("Audit export result does not match the API snapshot.")
    return body


def compress_audit_export_rows(rows: Sequence[ArchiveRow]) -> bytes:
    """Build one deterministic gzip JSONL member from validated rows."""
    rows = _validated_rows(rows)
    return gzip.compress(b"".join(encode_line(row) for row in rows), compresslevel=6, mtime=0)


def _write_transfer(
    kind: str,
    metadata: dict[str, object],
    members: Sequence[tuple[str, bytes]],
    *,
    chunk_size: int,
) -> FileTransfer:
    fd, name = tempfile.mkstemp(prefix="bifrost-audit-export-")
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


def _read_transfer(
    transfer: ReceivedFileTransfer,
    *,
    expected_kind: str,
    expected_members: set[str],
    max_total_size: int,
    member_max_size: int,
) -> tuple[dict[str, object], dict[str, bytes]]:
    if transfer.manifest.kind != expected_kind or transfer.manifest.total_size > max_total_size:
        raise AuditExportComputeError("Audit export transfer kind or size is invalid.")
    try:
        with zipfile.ZipFile(transfer.path) as archive:
            infos = archive.infolist()
            if len(infos) != len(expected_members):
                raise AuditExportComputeError("Audit export transfer has unsupported members.")
            seen: set[str] = set()
            total = 0
            members: dict[str, bytes] = {}
            for info in infos:
                _validate_info(info, seen)
                if info.filename not in expected_members:
                    raise AuditExportComputeError("Audit export transfer has unsupported members.")
                limit = (
                    MAX_METADATA_BYTES
                    if info.filename == _MANIFEST_MEMBER
                    else member_max_size
                )
                if info.file_size > limit:
                    raise AuditExportComputeError("Audit export transfer member exceeds the byte limit.")
                total += info.file_size
                if total > max_total_size:
                    raise AuditExportComputeError("Audit export transfer exceeds the expanded byte limit.")
                members[info.filename] = archive.read(info)
    except AuditExportComputeError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise AuditExportComputeError("Audit export transfer is not a valid ZIP archive.") from exc
    if set(members) != expected_members:
        raise AuditExportComputeError("Audit export transfer has unsupported members.")
    try:
        metadata = json.loads(members.pop(_MANIFEST_MEMBER))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditExportComputeError("Audit export transfer metadata is invalid.") from exc
    if not isinstance(metadata, dict) or metadata.get("version") != FILE_TRANSFER_VERSION or metadata.get("kind") != expected_kind:
        raise AuditExportComputeError("Audit export transfer metadata is invalid.")
    return metadata, members


def _validated_rows(rows: Sequence[ArchiveRow]) -> tuple[ArchiveRow, ...]:
    if len(rows) > MAX_EXPORT_ROWS or any(not isinstance(row, ArchiveRow) for row in rows):
        raise AuditExportComputeError("Audit export input rows are invalid.")
    if sum(line_size(row) for row in rows) > MAX_EXPANDED_BYTES:
        raise AuditExportComputeError("Audit export input exceeds the expanded byte limit.")
    return tuple(rows)


def _validate_input_metadata(metadata: dict[str, object]) -> None:
    row_count = metadata.get("row_count")
    if (
        set(metadata) != {"version", "kind", "row_count"}
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
    ):
        raise AuditExportComputeError("Audit export input metadata is invalid.")
    if not 0 <= row_count <= MAX_EXPORT_ROWS:
        raise AuditExportComputeError("Audit export input metadata is invalid.")


def _validate_result_metadata(metadata: dict[str, object]) -> None:
    row_count, sha256 = metadata.get("row_count"), metadata.get("sha256")
    if set(metadata) != {"version", "kind", "row_count", "sha256"}:
        raise AuditExportComputeError("Audit export result metadata is invalid.")
    if (
        isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or not isinstance(sha256, str)
    ):
        raise AuditExportComputeError("Audit export result metadata is invalid.")
    if not 0 <= row_count <= MAX_EXPORT_ROWS or len(sha256) != 64:
        raise AuditExportComputeError("Audit export result metadata is invalid.")


def _result_values(metadata: dict[str, object]) -> tuple[int, str]:
    _validate_result_metadata(metadata)
    row_count, sha256 = metadata["row_count"], metadata["sha256"]
    assert isinstance(row_count, int) and not isinstance(row_count, bool)
    assert isinstance(sha256, str)
    return row_count, sha256


def _validate_info(info: zipfile.ZipInfo, seen: set[str]) -> None:
    if (
        info.is_dir()
        or info.flag_bits & 0x1
        or stat.S_ISLNK(info.external_attr >> 16)
        or info.compress_type != zipfile.ZIP_STORED
        or info.filename in seen
    ):
        raise AuditExportComputeError("Audit export transfer has an unsafe or duplicate member.")
    seen.add(info.filename)


def _decompress_limited(body: bytes) -> bytes:
    try:
        with gzip.GzipFile(fileobj=BytesIO(body), mode="rb") as compressed:
            expanded = compressed.read(MAX_EXPANDED_BYTES + 1)
    except (OSError, EOFError, ValueError, TypeError, AttributeError) as exc:
        raise AuditExportComputeError("Audit export result is not a valid gzip member.") from exc
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise AuditExportComputeError("Audit export result exceeds the expanded byte limit.")
    return expanded


def _transfer_from_path(path: Path, kind: str, *, chunk_size: int) -> FileTransfer:
    limit = MAX_INPUT_TRANSFER_BYTES if kind == AUDIT_EXPORT_INPUT_KIND else MAX_RESULT_TRANSFER_BYTES
    size, digest = _file_size_and_digest(path, limit)
    manifest = FileTransferManifest(
        FILE_TRANSFER_VERSION,
        kind,
        size,
        digest,
        chunk_size,
        (size + chunk_size - 1) // chunk_size,
        limit,
    )
    manifest.validate()
    return FileTransfer(manifest, path)


def _file_size_and_digest(path: Path, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as archive:
        while chunk := archive.read(MAX_CHUNK_BYTES):
            size += len(chunk)
            if size > limit:
                raise AuditExportComputeError("Audit export transfer exceeds the size limit.")
            digest.update(chunk)
    return size, digest.hexdigest()


def _json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
