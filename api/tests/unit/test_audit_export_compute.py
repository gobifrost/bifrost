"""Pure contract tests for the bounded remote audit-export transfer."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import gzip
from pathlib import Path
from uuid import uuid4

import pytest

from shared import audit_export_compute as codec
from shared.file_compute import FileComputeResult
from shared.worker_transfer import FileTransfer, FileTransferReceiver, ReceivedFileTransfer
from src.jobs.platform.base import PlatformJobFailure
from src.services.audit_retention.format import ArchiveRow, encode_line
from src.services.audit_retention import export
from src.services.audit_retention.export import _export_batches, _remote_gzip_member


def _row(*, action: str = "audit.export") -> ArchiveRow:
    return ArchiveRow(
        id=uuid4(),
        created_at=datetime(2020, 1, 2, tzinfo=UTC),
        organization_id=None,
        user_id=None,
        impersonated_by_user_id=None,
        impersonation_session_id=None,
        action=action,
        resource_type=None,
        resource_id=None,
        outcome="success",
        source="test",
        operation_id=None,
        surface=None,
        execution_id=None,
        ip_address=None,
        user_agent=None,
        details=None,
        actor_email=None,
        actor_name=None,
        impersonated_by_email=None,
        impersonated_by_name=None,
        organization_name=None,
    )


def _receive(transfer: FileTransfer, *, temp_dir: Path) -> ReceivedFileTransfer:
    receiver = FileTransferReceiver(transfer.manifest, temp_dir=temp_dir)
    for chunk in transfer.iter_chunks():
        receiver.add_chunk(chunk)
    return receiver.finish()


def test_round_trip_preserves_exact_api_ordered_rows(tmp_path: Path) -> None:
    rows = (_row(action="audit.first"), _row(action="audit.second"))
    with codec.encode_audit_export_inputs(rows, chunk_size=13) as inputs:
        received_inputs = _receive(inputs, temp_dir=tmp_path)
    with received_inputs:
        decoded_rows = codec.decode_audit_export_inputs(received_inputs)

    with codec.encode_audit_export_result(
        codec.compress_audit_export_rows(decoded_rows), len(decoded_rows), chunk_size=17
    ) as output:
        received_output = _receive(output, temp_dir=tmp_path)
    with received_output:
        body = codec.decode_audit_export_result(received_output, expected_rows=rows)

    assert codec.AUDIT_EXPORT_OPERATION == "audit.export.v1"
    assert body == codec.compress_audit_export_rows(rows)


def test_valid_gzip_cannot_change_api_authorized_rows(tmp_path: Path) -> None:
    expected = (_row(),)
    altered = (replace(expected[0], action="audit.altered"),)
    with codec.encode_audit_export_result(
        codec.compress_audit_export_rows(altered), len(altered)
    ) as output:
        received = _receive(output, temp_dir=tmp_path)

    with received, pytest.raises(codec.AuditExportComputeError, match="does not match"):
        codec.decode_audit_export_result(received, expected_rows=expected)


def test_sequential_gzip_members_remain_one_standard_gzip_stream() -> None:
    first, second = _row(action="audit.first"), _row(action="audit.second")

    exported = codec.compress_audit_export_rows((first,)) + codec.compress_audit_export_rows((second,))

    assert gzip.decompress(exported) == encode_line(first) + encode_line(second)


def test_export_batches_preserve_order_and_split_before_remote_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _row(action="audit.first"), _row(action="audit.second")
    monkeypatch.setattr(export, "BATCH_BYTES", max(len(encode_line(row)) for row in (first, second)))

    batches = _export_batches((first, second))

    assert batches == [[first], [second]]


@pytest.mark.asyncio
async def test_api_rejects_worker_gzip_for_rows_it_did_not_authorize() -> None:
    expected = (_row(action="audit.expected"),)
    altered = (_row(action="audit.altered"),)

    async def file_compute(operation: str, _inputs: FileTransfer) -> FileComputeResult:
        assert operation == codec.AUDIT_EXPORT_OPERATION
        output = codec.encode_audit_export_result(
            codec.compress_audit_export_rows(altered), len(altered)
        )
        return FileComputeResult(ReceivedFileTransfer(output.manifest, output.path), {})

    with pytest.raises(PlatformJobFailure, match="could not be verified") as failure:
        await _remote_gzip_member(file_compute, expected)

    assert failure.value.code == "audit_export_compute_invalid_result"
