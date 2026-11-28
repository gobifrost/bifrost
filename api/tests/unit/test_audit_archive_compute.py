"""Pure contracts for the bounded remote audit archive transfer."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from shared import audit_archive_compute as codec
from shared.worker_transfer import FileTransfer, FileTransferReceiver, ReceivedFileTransfer
from src.services.audit_retention.format import ArchiveRow, build_segments


def _row(*, action: str = "audit.compute") -> ArchiveRow:
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


def test_round_trip_derives_segment_identity_from_api_rows(tmp_path: Path) -> None:
    rows = (_row(),)
    with codec.encode_audit_archive_inputs(rows, chunk_size=13) as inputs:
        received_inputs = _receive(inputs, temp_dir=tmp_path)
    with received_inputs:
        assert codec.decode_audit_archive_inputs(received_inputs) == rows

    with codec.encode_audit_archive_results(build_segments(rows), chunk_size=17) as outputs:
        received_outputs = _receive(outputs, temp_dir=tmp_path)
    with received_outputs:
        [segment] = codec.decode_audit_archive_results(received_outputs, expected_rows=rows)

    assert segment.rows == rows
    assert segment.organization_id is None
    assert segment.key.startswith("_audit/v1/org=global/day=2020-01-02/")


def test_checksum_valid_worker_body_cannot_change_canonical_rows(tmp_path: Path) -> None:
    expected = (_row(),)
    altered = (replace(expected[0], action="altered"),)
    with codec.encode_audit_archive_results(build_segments(altered)) as outputs:
        received = _receive(outputs, temp_dir=tmp_path)
    with received, pytest.raises(codec.AuditArchiveComputeError, match="does not match"):
        codec.decode_audit_archive_results(received, expected_rows=expected)


def test_input_expanded_limit_is_enforced_before_spooling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codec, "MAX_INPUT_EXPANDED_BYTES", 1)
    with pytest.raises(codec.AuditArchiveComputeError, match="expanded byte limit"):
        codec.encode_audit_archive_inputs((_row(),))
