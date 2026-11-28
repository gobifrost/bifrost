"""Least-privilege App compile recipe for shared file-backed stages.

This module keeps the App-specific child recipe and receipt validation. The
shared registry owns lifecycle, fencing, transfers, subprocess cleanup and
stage admission without importing compile dependencies.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.app_build_compute import (
    MAX_INPUT_TRANSFER_BYTES,
    MAX_RESULT_TRANSFER_BYTES,
)
from shared.subprocess_env import package_tool_env
from shared.worker_transfer import FileTransferManifest, ReceivedFileTransfer
from src.worker.remote.file_operation import (
    BoundFileOperationStages,
    FileOperationChild,
    FileOperationDefinition,
    FileOperationResult,
    FileOperationStage,
    NamedFileOperationStages,
)


APP_COMPILE_OPERATION = "app.compile.v2"


@dataclass(frozen=True)
class _CompilerDependencies:
    build_recipe: Any
    valid_recipe: Any
    reproducible_dependency_build_error: type[Exception]
    app_build_memory_error: type[Exception]
    subprocess_memory_error: Any
    decode_app_build_inputs: Any
    encode_app_build_result: Any
    app_build_inputs: Any
    compile_app: Any
    output_tail: Any


def _compiler_dependencies() -> _CompilerDependencies:
    """Import compiler-only dependencies inside the isolated child path."""
    from bifrost.app_build_identity import build_recipe, valid_recipe
    from bifrost.app_lockfile import ReproducibleDependencyBuildError
    from shared.app_build_compute import decode_app_build_inputs, encode_app_build_result
    from shared.app_build_errors import AppBuildMemoryError, subprocess_memory_error
    from shared.solution_app_compiler import AppBuildInputs, compile_app, output_tail

    return _CompilerDependencies(
        build_recipe=build_recipe,
        valid_recipe=valid_recipe,
        reproducible_dependency_build_error=ReproducibleDependencyBuildError,
        app_build_memory_error=AppBuildMemoryError,
        subprocess_memory_error=subprocess_memory_error,
        decode_app_build_inputs=decode_app_build_inputs,
        encode_app_build_result=encode_app_build_result,
        app_build_inputs=AppBuildInputs,
        compile_app=compile_app,
        output_tail=output_tail,
    )


def _manifest(value: object, *, max_total_size: int) -> FileTransferManifest:
    if not isinstance(value, Mapping):
        raise ValueError("App compile input manifest is required")
    return FileTransferManifest.from_dict(value, max_total_size=max_total_size)


def _validate_input(manifest: FileTransferManifest) -> None:
    if manifest.kind != "app_build_inputs":
        raise ValueError("App compile input must be an input transfer")


def _child_instruction(stage: FileOperationStage) -> dict[str, object]:
    assert stage.input_transfer is not None
    return {
        "input_path": str(stage.input_transfer.path),
        "input_manifest": stage.manifest.to_dict(),
        "output_path": str(stage.output_path),
    }


def _child() -> FileOperationChild:
    return FileOperationChild(
        args=(sys.executable, "-m", "src.worker.remote.app_compile"),
        kwargs={
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
            "env": package_tool_env(),
        },
    )


def _decode_success(
    receipt: Mapping[str, Any], stage: FileOperationStage
) -> FileOperationResult:
    manifest = _manifest(
        receipt.get("result_manifest"), max_total_size=MAX_RESULT_TRANSFER_BYTES
    )
    if (
        manifest.kind != "app_build_result"
        or not stage.output_path.is_file()
        or stage.output_path.stat().st_size != manifest.total_size
    ):
        raise ValueError("App compile child did not produce a valid result archive")
    recipe = receipt.get("build_recipe")
    if not _compiler_dependencies().valid_recipe(recipe):
        raise ValueError("App compile child returned an invalid build recipe")
    return FileOperationResult(manifest=manifest, fields={"build_recipe": recipe})


def app_compile_operation() -> FileOperationDefinition:
    """Return the code-whitelisted recipe used by the fixed App wire protocol."""
    return FileOperationDefinition(
        name=APP_COMPILE_OPERATION,
        label="App compile",
        input_max_bytes=MAX_INPUT_TRANSFER_BYTES,
        validate_input=_validate_input,
        child_instruction=_child_instruction,
        decode_success=_decode_success,
        child=_child,
        output_name="result.zip",
        temp_prefix="bifrost-appcompile-",
    )


class AppCompileStages:
    """The fixed ``app.compile.v2`` view of the launcher's shared registry."""

    def __init__(self, stages: NamedFileOperationStages) -> None:
        self._stages_view: BoundFileOperationStages = stages.operation(
            APP_COMPILE_OPERATION
        )

    def prepare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._stages_view.prepare(payload)

    def add_chunk(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._stages_view.add_chunk(payload)

    async def start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self._stages_view.start(payload)

    def status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._stages_view.status(payload)

    def result_chunk(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._stages_view.result_chunk(payload)

    async def release(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self._stages_view.release(payload)


def _compile_instruction(instruction: Mapping[str, object]) -> dict[str, object]:
    """Child entrypoint: run one compiler without inheriting platform env."""
    dependencies = _compiler_dependencies()
    try:
        input_path, output_path = (
            Path(str(instruction["input_path"])),
            Path(str(instruction["output_path"])),
        )
        manifest = _manifest(
            instruction["input_manifest"], max_total_size=MAX_INPUT_TRANSFER_BYTES
        )
        _validate_input(manifest)
        environment = package_tool_env()
        transfer = ReceivedFileTransfer(manifest=manifest, path=input_path)
        inputs = dependencies.decode_app_build_inputs(transfer, environment=environment)
        if not dependencies.valid_recipe(inputs.recipe):
            raise ValueError("App compile input has an invalid requested recipe")
        actual_recipe = dependencies.build_recipe(
            inputs.sdk_tarball,
            variant=inputs.recipe["variant"],
            environment=environment,
        )
        result = dependencies.compile_app(
            dependencies.app_build_inputs(
                app_id=inputs.app_id,
                source_files=inputs.source_files,
                dependencies=inputs.dependencies,
                sdk_tarball=inputs.sdk_tarball,
                recipe=actual_recipe,
                environment=environment,
                require_lockfile=inputs.require_lockfile,
                package_name=inputs.package_name,
            )
        )
        with dependencies.encode_app_build_result(result) as transfer_out:
            output_manifest = transfer_out.manifest
            os.replace(transfer_out.path, output_path)
        return {
            "state": "succeeded",
            "result_manifest": output_manifest.to_dict(),
            "build_recipe": actual_recipe,
        }
    except dependencies.reproducible_dependency_build_error as exc:
        return _error("lockfile", str(exc))
    except subprocess.TimeoutExpired as exc:
        return _error("timeout", str(exc) or "App compile timed out")
    except dependencies.app_build_memory_error as exc:
        return _error("memory", str(exc))
    except MemoryError as exc:
        return _error("memory", str(exc) or "App compile ran out of memory")
    except Exception as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            memory = dependencies.subprocess_memory_error(
                exc.returncode, exc.stderr, exc.stdout
            )
            if memory is not None:
                return _error("memory", str(memory))
            return _error("buildfailure", dependencies.output_tail(exc))
        return _error("buildfailure", str(exc))


def _error(category: str, message: str) -> dict[str, object]:
    return {
        "state": "failed",
        "error": {"category": category, "message": message[:4096]},
    }


def main() -> None:
    instruction = json.load(sys.stdin)
    if not isinstance(instruction, dict):
        raise ValueError("App compile instruction must be an object")
    print(
        json.dumps(_compile_instruction(instruction), separators=(",", ":")), flush=True
    )


if __name__ == "__main__":
    main()
