"""Code-whitelisted pure inspection recipe for staged Solution archives."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shared.solution_archive_compute import (
    MAX_INPUT_TRANSFER_BYTES,
    MAX_RESULT_TRANSFER_BYTES,
    SOLUTION_ARCHIVE_INSPECT_INPUT_KIND,
    SOLUTION_ARCHIVE_INSPECT_OPERATION,
    SOLUTION_ARCHIVE_INSPECT_RESULT_KIND,
)
from shared.solution_archive import MAX_SOLUTION_UNCOMPRESSED_BYTES
from shared.worker_transfer import FileTransferManifest, ReceivedFileTransfer
from src.worker.remote.file_operation import (
    FileOperationChild,
    FileOperationDefinition,
    FileOperationResult,
    FileOperationStage,
)


def _manifest(value: object, *, max_total_size: int) -> FileTransferManifest:
    if not isinstance(value, Mapping):
        raise ValueError("Solution archive inspection transfer manifest is invalid")
    return FileTransferManifest.from_dict(value, max_total_size=max_total_size)


def _validate_input(manifest: FileTransferManifest) -> None:
    if manifest.kind != SOLUTION_ARCHIVE_INSPECT_INPUT_KIND:
        raise ValueError("Solution archive inspection input must be an input transfer")


def _child_instruction(stage: FileOperationStage) -> dict[str, object]:
    assert stage.input_transfer is not None
    return {
        "input_path": str(stage.input_transfer.path),
        "input_manifest": stage.manifest.to_dict(),
        "output_path": str(stage.output_path),
    }


def _child() -> FileOperationChild:
    return FileOperationChild(
        args=(sys.executable, "-m", "src.worker.remote.solution_archive_inspect"),
        kwargs={
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
            # The child receives only staged files, never API credentials.
            "env": {"PATH": os.defpath},
        },
    )


def _decode_success(
    receipt: Mapping[str, Any], stage: FileOperationStage
) -> FileOperationResult:
    manifest = _manifest(
        receipt.get("result_manifest"), max_total_size=MAX_RESULT_TRANSFER_BYTES
    )
    if (
        manifest.kind != SOLUTION_ARCHIVE_INSPECT_RESULT_KIND
        or not stage.output_path.is_file()
        or stage.output_path.stat().st_size != manifest.total_size
    ):
        raise ValueError("Solution archive inspection child did not produce a valid result archive")
    return FileOperationResult(manifest=manifest)


def solution_archive_inspect_operation() -> FileOperationDefinition:
    """Return the fixed pure recipe registered by the physical launcher."""
    return FileOperationDefinition(
        name=SOLUTION_ARCHIVE_INSPECT_OPERATION,
        label="Solution archive inspection",
        input_max_bytes=MAX_INPUT_TRANSFER_BYTES,
        validate_input=_validate_input,
        child_instruction=_child_instruction,
        decode_success=_decode_success,
        child=_child,
        output_name="result.zip",
        temp_prefix="bifrost-solutioninspect-",
        workspace_max_bytes=2 * MAX_SOLUTION_UNCOMPRESSED_BYTES + MAX_RESULT_TRANSFER_BYTES,
    )


def _inspect_instruction(instruction: Mapping[str, object]) -> dict[str, object]:
    """Child entrypoint: inspect only the supplied staged ZIP transfer."""
    try:
        from shared.solution_archive_compute import (
            build_solution_archive_inspect_result,
            decode_solution_archive_inspect_inputs,
        )

        input_path = Path(str(instruction["input_path"]))
        output_path = Path(str(instruction["output_path"]))
        manifest = _manifest(
            instruction["input_manifest"], max_total_size=MAX_INPUT_TRANSFER_BYTES
        )
        _validate_input(manifest)
        inputs = decode_solution_archive_inspect_inputs(
            ReceivedFileTransfer(manifest, input_path)
        )
        with build_solution_archive_inspect_result(inputs) as transfer:
            output_manifest = transfer.manifest
            os.replace(transfer.path, output_path)
        return {"state": "succeeded", "result_manifest": output_manifest.to_dict()}
    except Exception as exc:
        return {
            "state": "failed",
            "error": {"category": "compute", "message": str(exc)[:4096]},
        }


def main() -> None:
    instruction = json.load(sys.stdin)
    if not isinstance(instruction, dict):
        raise ValueError("Solution archive inspection instruction must be an object")
    print(json.dumps(_inspect_instruction(instruction), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
