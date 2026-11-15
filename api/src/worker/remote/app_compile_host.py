"""App-build adapter over the shared fenced file-compute transport."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from bifrost.app_build_identity import sdk_identity, valid_recipe
from shared.app_build_compute import decode_app_build_result, encode_app_build_inputs
from shared.file_compute import decode_file_result, encode_file_transfer
from shared.worker_transfer import FileTransfer
from shared.solution_app_compiler import AppBuildInputs, AppBuildResult, source_build_identity
from src.jobs.platform.base import PlatformJobFailure
from src.worker.remote.file_compute_host import (
    RemoteFileCompute,
)


class AppBuildFileCompute:
    """Preserve App recipe and artifact provenance checks at the API boundary."""

    def __init__(self, compute: RemoteFileCompute) -> None:
        self.compute = compute

    async def __call__(self, inputs: AppBuildInputs) -> AppBuildResult:
        transfer: FileTransfer | None = None

        def validate_result_metadata(status: Mapping[str, Any]) -> None:
            recipe = status.get("build_recipe")
            if (
                not valid_recipe(recipe)
                or recipe["sdk"] != sdk_identity(inputs.sdk_tarball)
                or recipe["variant"] != inputs.recipe["variant"]
            ):
                raise PlatformJobFailure(
                    "compile_provenance_invalid",
                    "The physical compiler returned incompatible build provenance.",
                )
            manifest = status.get("result_manifest")
            if not isinstance(manifest, Mapping) or manifest.get("kind") != "app_build_result":
                raise PlatformJobFailure(
                    "compile_transfer_invalid",
                    "The physical compiler returned the wrong transfer kind.",
                )

        try:
            transfer = await encode_file_transfer(encode_app_build_inputs, inputs)
            try:
                outcome = await self.compute(
                    "app.compile.v2", transfer, result_metadata_validator=validate_result_metadata,
                )
            except PlatformJobFailure as exc:
                if exc.code in {"memory_pressure", "timeout"}:
                    raise
                code = {
                    "file_compute_lockfile": "sdk_update_lockfile_unreproducible",
                    "file_compute_stage_conflict": "compile_stage_conflict",
                    "file_compute_stage_exists": "compile_stage_exists",
                    "file_compute_transfer_invalid": "compile_transfer_invalid",
                }.get(exc.code, "app_build_failed")
                raise PlatformJobFailure(code, exc.message) from exc
            recipe = outcome.metadata.get("build_recipe")
            result = await decode_file_result(outcome, decode_app_build_result)
            if result.build_identity != source_build_identity(replace(inputs, recipe=recipe)):
                raise PlatformJobFailure(
                    "compile_provenance_invalid",
                    "App artifacts do not match the physical compiler inputs.",
                )
            return result
        finally:
            if transfer is not None:
                transfer.close()
