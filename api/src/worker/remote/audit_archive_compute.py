"""Code-whitelisted pure child recipe for audit archive compression."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shared.audit_archive_compute import (
    AUDIT_ARCHIVE_INPUT_KIND,
    AUDIT_ARCHIVE_OPERATION,
    AUDIT_ARCHIVE_RESULT_KIND,
    MAX_INPUT_TRANSFER_BYTES,
    MAX_RESULT_TRANSFER_BYTES,
)
from shared.worker_transfer import FileTransferManifest, ReceivedFileTransfer
from src.worker.remote.file_operation import (
    FileOperationChild,
    FileOperationDefinition,
    FileOperationResult,
    FileOperationStage,
)


def _manifest(value: object, *, max_total_size: int) -> FileTransferManifest:
    if not isinstance(value, Mapping):
        raise ValueError("Audit archive transfer manifest is invalid")
    return FileTransferManifest.from_dict(value, max_total_size=max_total_size)


def _validate_input(manifest: FileTransferManifest) -> None:
    if manifest.kind != AUDIT_ARCHIVE_INPUT_KIND:
        raise ValueError("Audit archive compute input must be an input transfer")


def _child_instruction(stage: FileOperationStage) -> dict[str, object]:
    assert stage.input_transfer is not None
    return {
        "input_path": str(stage.input_transfer.path),
        "input_manifest": stage.manifest.to_dict(),
        "output_path": str(stage.output_path),
    }


def _child() -> FileOperationChild:
    return FileOperationChild(
        args=(sys.executable, "-m", "src.worker.remote.audit_archive_compute"),
        kwargs={
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
            # Keep the compressor free of the launcher's credential-bearing
            # environment.  It receives only paths to fenced transfer files.
            "env": {"PATH": os.defpath},
        },
    )


def _decode_success(receipt: Mapping[str, Any], stage: FileOperationStage) -> FileOperationResult:
    manifest = _manifest(receipt.get("result_manifest"), max_total_size=MAX_RESULT_TRANSFER_BYTES)
    if (
        manifest.kind != AUDIT_ARCHIVE_RESULT_KIND
        or not stage.output_path.is_file()
        or stage.output_path.stat().st_size != manifest.total_size
    ):
        raise ValueError("Audit archive child did not produce a valid result archive")
    return FileOperationResult(manifest=manifest)


def audit_archive_operation() -> FileOperationDefinition:
    """Return the fixed pure-compressor recipe for launcher registration."""
    return FileOperationDefinition(
        name=AUDIT_ARCHIVE_OPERATION,
        label="Audit archive compression",
        input_max_bytes=MAX_INPUT_TRANSFER_BYTES,
        validate_input=_validate_input,
        child_instruction=_child_instruction,
        decode_success=_decode_success,
        child=_child,
        output_name="result.zip",
        temp_prefix="bifrost-auditarchive-",
    )


def _compress_instruction(instruction: Mapping[str, object]) -> dict[str, object]:
    """Child entrypoint: decode rows, gzip them, and emit only result bytes."""
    try:
        # These imports stay inside the child path so registration does not
        # pull persistence, provider, or worker-host concerns into the recipe.
        from shared.audit_archive_compute import (
            decode_audit_archive_inputs,
            encode_audit_archive_results,
        )
        from src.services.audit_retention.format import build_segments

        input_path = Path(str(instruction["input_path"]))
        output_path = Path(str(instruction["output_path"]))
        manifest = _manifest(instruction["input_manifest"], max_total_size=MAX_INPUT_TRANSFER_BYTES)
        _validate_input(manifest)
        rows = decode_audit_archive_inputs(ReceivedFileTransfer(manifest, input_path))
        with encode_audit_archive_results(build_segments(rows)) as transfer:
            output_manifest = transfer.manifest
            os.replace(transfer.path, output_path)
        return {"state": "succeeded", "result_manifest": output_manifest.to_dict()}
    except Exception as exc:
        return {"state": "failed", "error": {"category": "compute", "message": str(exc)[:4096]}}


def main() -> None:
    instruction = json.load(sys.stdin)
    if not isinstance(instruction, dict):
        raise ValueError("Audit archive compute instruction must be an object")
    print(json.dumps(_compress_instruction(instruction), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
