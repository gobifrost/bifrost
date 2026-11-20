"""Bounded, source-only transport for the existing per-file preview compiler."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import PurePosixPath

from shared.file_compute import FileCompute, decode_file_result, encode_file_transfer
from shared.worker_transfer import FileTransfer, ReceivedFileTransfer
from shared.json_compute_transfer import write_json_transfer, read_json_transfer
from src.services.app_compiler import AppCompilerInfrastructureError, CompileResult, _validate_compile_input
from src.services.app_resource_limits import DEFAULT_APP_ARTIFACT_LIMITS
from src.services.node_subprocess import APP_COMPILER_NODE_POLICY

OPERATION = "app.preview.compile.v1"
INPUT_KIND = "app_preview_inputs"
RESULT_KIND = "app_preview_results"
MAX_INPUT_BYTES = APP_COMPILER_NODE_POLICY.max_input_bytes + 1024 * 1024
MAX_RESULT_BYTES = APP_COMPILER_NODE_POLICY.max_output_bytes + 1024 * 1024




def _validated_files(files: object) -> list[dict]:
    if not isinstance(files, list):
        raise ValueError("Preview compiler omitted source files")
    _validate_compile_input(files)
    for item in files:
        path = PurePosixPath(item["path"])
        if path.is_absolute() or ".." in path.parts or "\\" in item["path"] or not item["path"].endswith((".ts", ".tsx")):
            raise ValueError("Preview compiler source path is invalid")
    return files


def encode_inputs(files: list[dict]) -> FileTransfer:
    return write_json_transfer({"files": _validated_files(files)}, INPUT_KIND, MAX_INPUT_BYTES)


def decode_inputs(transfer: ReceivedFileTransfer) -> list[dict]:
    return _validated_files(read_json_transfer(transfer, INPUT_KIND, MAX_INPUT_BYTES).get("files"))


def encode_results(results: list[CompileResult], input_digest: str) -> FileTransfer:
    return write_json_transfer({"input_sha256": input_digest, "results": [asdict(item) for item in results]}, RESULT_KIND, MAX_RESULT_BYTES)


def decode_results(transfer: ReceivedFileTransfer, *, input_digest: str, expected_paths: set[str]) -> list[CompileResult]:
    value = read_json_transfer(transfer, RESULT_KIND, MAX_RESULT_BYTES)
    raw = value.get("results")
    if value.get("input_sha256") != input_digest or not isinstance(raw, list) or len(raw) != len(expected_paths):
        raise AppCompilerInfrastructureError("Preview compiler result does not match its source snapshot")
    results = []
    seen = set()
    total = 0
    for item in raw:
        if not isinstance(item, dict) or item.get("path") not in expected_paths or item["path"] in seen or type(item.get("success")) is not bool:
            raise AppCompilerInfrastructureError("Preview compiler returned invalid source results")
        seen.add(item["path"])
        compiled, error = item.get("compiled"), item.get("error")
        if item["success"]:
            if not isinstance(compiled, str) or error is not None:
                raise AppCompilerInfrastructureError("Preview compiler omitted output")
            size = len(compiled.encode())
            total += size
            if size > DEFAULT_APP_ARTIFACT_LIMITS.max_file_bytes or total > DEFAULT_APP_ARTIFACT_LIMITS.max_total_bytes:
                raise AppCompilerInfrastructureError("Preview compiler output exceeds artifact limits")
        elif not isinstance(error, str) or not error or compiled is not None:
            raise AppCompilerInfrastructureError("Preview compiler omitted its authored error")
        default, named = item.get("default_export"), item.get("named_exports")
        if (default is not None and not isinstance(default, str)) or not isinstance(named, list) or not all(isinstance(n, str) for n in named):
            raise AppCompilerInfrastructureError("Preview compiler returned invalid exports")
        results.append(CompileResult(item["path"], item["success"], compiled, error, default, named))
    return results


async def compile_preview_using(files: list[dict], compute: FileCompute) -> list[CompileResult]:
    transfer = await encode_file_transfer(encode_inputs, files)
    try:
        outcome = await compute(OPERATION, transfer)
        return await decode_file_result(outcome, lambda received: decode_results(received,
            input_digest=transfer.manifest.sha256, expected_paths={item["path"] for item in files}))
    finally:
        transfer.close()
