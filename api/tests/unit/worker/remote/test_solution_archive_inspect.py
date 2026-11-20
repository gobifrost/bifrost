"""Pure worker-recipe coverage for file-backed Solution archive inspection."""

from __future__ import annotations

import zipfile
from pathlib import Path

from shared import solution_archive_compute as codec
from shared.worker_transfer import FileTransferManifest, ReceivedFileTransfer
from src.worker.remote import solution_archive_inspect


def test_operation_is_fixed_and_returns_source_without_encrypted_payloads(tmp_path: Path) -> None:
    archive_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("bifrost.solution.yaml", "slug: demo\nname: Demo\n")
        archive.writestr("README.md", "# Demo\n")
        archive.writestr(".bifrost/secrets.enc", b"encrypted")
        archive.writestr(".bifrost/file-payloads/example.bin.enc", b"payload")
        archive.writestr("apps/demo/index.tsx", "export default null\n")

    operation = solution_archive_inspect.solution_archive_inspect_operation()
    assert operation.name == codec.SOLUTION_ARCHIVE_INSPECT_OPERATION
    assert operation.input_max_bytes == codec.MAX_INPUT_TRANSFER_BYTES
    assert set(operation.child().kwargs["env"]) == {"PATH"}

    with codec.encode_solution_archive_inspect_inputs(archive_path) as inputs:
        output_path = tmp_path / "result.zip"
        receipt = solution_archive_inspect._inspect_instruction(
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
        FileTransferManifest.from_dict(
            manifest, max_total_size=codec.MAX_RESULT_TRANSFER_BYTES
        ),
        output_path,
    )
    with received:
        inspection = codec.decode_solution_archive_inspect_results(
            received, expected_input=inputs.manifest
        )
    with inspection:
        assert inspection.preview.slug == "demo"
        assert inspection.preview.requires_password is False
        assert not ({member.path for member in inspection.members} & {
            ".bifrost/secrets.enc",
            ".bifrost/file-payloads/example.bin.enc",
        })
        with zipfile.ZipFile(inspection.source_archive) as source:
            assert "apps/demo/index.tsx" in source.namelist()
            assert ".bifrost/secrets.enc" not in source.namelist()
            assert ".bifrost/file-payloads/example.bin.enc" not in source.namelist()
