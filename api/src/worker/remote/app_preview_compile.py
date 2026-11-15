"""Code-whitelisted pure per-file App preview compiler."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shared.app_preview_compute import (
    INPUT_KIND, RESULT_KIND, MAX_INPUT_BYTES, MAX_RESULT_BYTES, OPERATION,
)
from shared.subprocess_env import package_tool_env
from shared.worker_transfer import FileTransferManifest, ReceivedFileTransfer
from src.worker.remote.file_operation import (
    FileOperationChild,
    FileOperationDefinition,
    FileOperationResult,
    FileOperationStage,
)


def _manifest(value: object, *, max_total_size: int) -> FileTransferManifest:
    if not isinstance(value, Mapping):
        raise ValueError("App preview transfer manifest is invalid")
    return FileTransferManifest.from_dict(value, max_total_size=max_total_size)


def _validate_input(manifest: FileTransferManifest) -> None:
    if manifest.kind != INPUT_KIND:
        raise ValueError("App preview compute input must be an input transfer")


def _child_instruction(stage: FileOperationStage) -> dict[str, object]:
    assert stage.input_transfer is not None
    return {
        "input_path": str(stage.input_transfer.path),
        "input_manifest": stage.manifest.to_dict(),
        "output_path": str(stage.output_path),
    }


def _child() -> FileOperationChild:
    return FileOperationChild(
        args=(sys.executable, "-m", "src.worker.remote.app_preview_compile"),
        kwargs={
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
            # Node needs its package-tool environment, never the
            # launcher's database, object-store, API, or provider credentials.
            "env": package_tool_env(),
        },
    )


def _decode_success(receipt: Mapping[str, Any], stage: FileOperationStage) -> FileOperationResult:
    manifest = _manifest(receipt.get("result_manifest"), max_total_size=MAX_RESULT_BYTES)
    if manifest.kind != RESULT_KIND or not stage.output_path.is_file() or stage.output_path.stat().st_size != manifest.total_size:
        raise ValueError("App preview child did not produce a valid result archive")
    return FileOperationResult(manifest=manifest)


def app_preview_operation() -> FileOperationDefinition:
    """Return the fixed recipe registered by the launcher for app.preview.compile.v1."""
    return FileOperationDefinition(
        name=OPERATION,
        label="App preview",
        input_max_bytes=MAX_INPUT_BYTES,
        validate_input=_validate_input,
        child_instruction=_child_instruction,
        decode_success=_decode_success,
        child=_child,
        output_name="result.json",
        temp_prefix="bifrost-apppreview-",
        workspace_max_bytes=MAX_RESULT_BYTES,
    )


async def _preview_instruction(instruction: Mapping[str, object]) -> dict[str, object]:
    try:
        from shared.app_preview_compute import decode_inputs, encode_results
        from src.services.app_compiler import AppCompilerService

        input_path, output_path = Path(str(instruction["input_path"])), Path(str(instruction["output_path"]))
        manifest = _manifest(instruction["input_manifest"], max_total_size=MAX_INPUT_BYTES)
        _validate_input(manifest)
        files = decode_inputs(ReceivedFileTransfer(manifest, input_path))
        results = await AppCompilerService().compile_batch(files)
        with encode_results(results, manifest.sha256) as transfer:
            output_manifest = transfer.manifest
            os.replace(transfer.path, output_path)
        return {"state": "succeeded", "result_manifest": output_manifest.to_dict()}
    except Exception as exc:
        return {"state": "failed", "error": {"category": "buildfailure", "message": str(exc)[:4096]}}


def main() -> None:
    instruction = json.load(sys.stdin)
    if not isinstance(instruction, dict):
        raise ValueError("App preview compute instruction must be an object")
    print(json.dumps(asyncio.run(_preview_instruction(instruction)), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
