"""Code-whitelisted pure static workspace preflight analyzer."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shared.workspace_preflight_compute import (
    INPUT_KIND, RESULT_KIND, MAX_INPUT_BYTES, OPERATION, MAX_RESULT_BYTES, MAX_SOURCE_BYTES,
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
        raise ValueError("Workspace preflight transfer manifest is invalid")
    return FileTransferManifest.from_dict(value, max_total_size=max_total_size)


def _validate_input(manifest: FileTransferManifest) -> None:
    if manifest.kind != INPUT_KIND:
        raise ValueError("Workspace preflight compute input must be an input transfer")


def _child_instruction(stage: FileOperationStage) -> dict[str, object]:
    assert stage.input_transfer is not None
    return {
        "input_path": str(stage.input_transfer.path),
        "input_manifest": stage.manifest.to_dict(),
        "output_path": str(stage.output_path),
    }


def _child() -> FileOperationChild:
    return FileOperationChild(
        args=(sys.executable, "-m", "src.worker.remote.workspace_preflight"),
        kwargs={
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
            # The analyzer needs no platform environment, never the
            # launcher's database, object-store, API, or provider credentials.
            "env": {"PATH": os.defpath},
        },
    )


def _decode_success(receipt: Mapping[str, Any], stage: FileOperationStage) -> FileOperationResult:
    manifest = _manifest(receipt.get("result_manifest"), max_total_size=MAX_RESULT_BYTES)
    if manifest.kind != RESULT_KIND or not stage.output_path.is_file() or stage.output_path.stat().st_size != manifest.total_size:
        raise ValueError("Workspace preflight child did not produce a valid result archive")
    return FileOperationResult(manifest=manifest)


def workspace_preflight_operation() -> FileOperationDefinition:
    """Return the fixed recipe registered by the launcher for workspace.preflight.v1."""
    return FileOperationDefinition(
        name=OPERATION,
        label="Workspace preflight",
        input_max_bytes=MAX_INPUT_BYTES,
        validate_input=_validate_input,
        child_instruction=_child_instruction,
        decode_success=_decode_success,
        child=_child,
        output_name="result.json",
        temp_prefix="bifrost-workspacepreflight-",
        workspace_max_bytes=MAX_SOURCE_BYTES + MAX_RESULT_BYTES,
    )


async def _preflight_instruction(instruction: Mapping[str, object]) -> dict[str, object]:
    try:
        from shared.workspace_preflight_compute import decode_inputs, encode_results, analyze

        input_path, output_path = Path(str(instruction["input_path"])), Path(str(instruction["output_path"]))
        manifest = _manifest(instruction["input_manifest"], max_total_size=MAX_INPUT_BYTES)
        _validate_input(manifest)
        inputs = decode_inputs(ReceivedFileTransfer(manifest, input_path))
        results = analyze(inputs, workspace=output_path.parent)
        with encode_results(results, manifest.sha256, temp_dir=output_path.parent) as transfer:
            output_manifest = transfer.manifest
            os.replace(transfer.path, output_path)
        return {"state": "succeeded", "result_manifest": output_manifest.to_dict()}
    except Exception as exc:
        return {"state": "failed", "error": {"category": "buildfailure", "message": str(exc)[:4096]}}


def main() -> None:
    instruction = json.load(sys.stdin)
    if not isinstance(instruction, dict):
        raise ValueError("Workspace preflight compute instruction must be an object")
    print(json.dumps(asyncio.run(_preflight_instruction(instruction)), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
