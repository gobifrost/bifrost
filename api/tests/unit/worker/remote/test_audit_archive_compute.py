"""Pure worker recipe contract for audit archive compression."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from shared import audit_archive_compute as codec
from shared.worker_transfer import FileTransferManifest, ReceivedFileTransfer
from src.services.audit_retention.format import ArchiveRow
from src.worker.remote import audit_archive_compute


def _row() -> ArchiveRow:
    return ArchiveRow(
        id=uuid4(), created_at=datetime(2020, 1, 2, tzinfo=UTC), organization_id=None,
        user_id=None, impersonated_by_user_id=None, impersonation_session_id=None,
        action="audit.compute", resource_type=None, resource_id=None, outcome="success",
        source="test", operation_id=None, surface=None, execution_id=None, ip_address=None,
        user_agent=None, details=None, actor_email=None, actor_name=None,
        impersonated_by_email=None, impersonated_by_name=None, organization_name=None,
    )


def test_operation_is_fixed_and_child_compresses_only_transfer_rows(tmp_path: Path) -> None:
    operation = audit_archive_compute.audit_archive_operation()
    assert operation.name == codec.AUDIT_ARCHIVE_OPERATION
    assert operation.input_max_bytes == codec.MAX_INPUT_TRANSFER_BYTES
    assert set(operation.child().kwargs["env"]) == {"PATH"}

    rows = (_row(),)
    with codec.encode_audit_archive_inputs(rows) as inputs:
        output_path = tmp_path / "result.zip"
        receipt = audit_archive_compute._compress_instruction(
            {
                "input_path": str(inputs.path),
                "input_manifest": inputs.manifest.to_dict(),
                "output_path": str(output_path),
            }
        )
    assert receipt["state"] == "succeeded"
    manifest = receipt["result_manifest"]
    assert isinstance(manifest, dict)
    received = ReceivedFileTransfer(
        FileTransferManifest.from_dict(manifest, max_total_size=codec.MAX_RESULT_TRANSFER_BYTES),
        output_path,
    )
    with received:
        [segment] = codec.decode_audit_archive_results(received, expected_rows=rows)
    assert segment.rows == rows
