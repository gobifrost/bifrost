"""Storage adapter for the pure inline-App bundle compiler.

Request-scoped preview and save callers intentionally use this local adapter.
Durable publication snapshots source in the API and calls the same compiler via
the named file-compute operation instead.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from shared.app_bundle_compiler import (
    DEFAULT_EXTERNALS as DEFAULT_EXTERNALS,
    SCHEMA_VERSION,
    TAILWIND_OUTPUT_CSS as TAILWIND_OUTPUT_CSS,
    BundleManifest,
    BundleMessage,
    BundleResult,
    compile_app_bundle,
    generate_app_tailwind,
    run_esbuild,
    write_bifrost_package,
    write_entry,
    _PLATFORM_EXPORT_NAMES,
)
from shared.app_bundle_compute import (
    AppBundleInputs,
    decode_app_bundle_results,
    encode_app_bundle_inputs,
)
from shared.file_compute import FileCompute, decode_file_result, encode_file_transfer
from shared.worker_transfer import FileTransfer
from src.core.malloc import trim_malloc
from src.services.app_resource_limits import (
    is_compiler_text_source,
    validate_app_source_metadata,
    validate_app_source_write,
)
from src.services.app_storage import AppStorageService
from src.services.repo_storage import RepoStorage


Mode = Literal["preview", "live"]


class BundlerService:
    """Materialize source and persist local, request-scoped bundle results."""

    def __init__(self) -> None:
        self._app_storage = AppStorageService()
        self._repo = RepoStorage()

    async def build(
        self, app_id: str, repo_prefix: str, mode: Mode, dependencies: dict[str, str] | None = None
    ) -> BundleResult:
        try:
            with tempfile.TemporaryDirectory(prefix="bifrost-bundle-source-") as tmp:
                source_dir = Path(tmp)
                sources = await self._materialize_source(source_dir, repo_prefix)
                if not sources:
                    return BundleResult(False, errors=[BundleMessage(text=f"No source files for app {app_id} at {repo_prefix}")])
                compiled = await compile_app_bundle(
                    {path: (source_dir / path).read_bytes() for path in sources}, dependencies, mode
                )
            if not compiled.result.success or compiled.result.manifest is None:
                return compiled.result
            await self.write_compilation(app_id, mode, compiled.result, compiled.artifacts)
            return compiled.result
        finally:
            trim_malloc()

    async def write_compilation(
        self, app_id: str, mode: Mode, result: BundleResult, artifacts: dict[str, bytes]
    ) -> None:
        """Persist an already validated pure-compiler success result."""
        if not result.success or result.manifest is None:
            raise ValueError("Cannot persist an unsuccessful App bundle")
        if set(artifacts) != set(result.manifest.outputs):
            raise ValueError("App bundle artifacts do not match its manifest")
        for path, body in artifacts.items():
            if mode == "preview":
                await self._app_storage.write_preview_file(app_id, path, body)
            else:
                await self._write_live(app_id, path, body)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "entry": result.manifest.entry,
            "css": result.manifest.css,
            "outputs": result.manifest.outputs,
            "duration_ms": result.manifest.duration_ms,
            "dependencies": result.manifest.dependencies,
        }
        data = json.dumps(manifest, indent=2).encode()
        if mode == "preview":
            await self._app_storage.write_preview_file(app_id, "manifest.json", data)
        else:
            await self._write_live(app_id, "manifest.json", data)

    async def source_snapshot(
        self, repo_prefix: str
    ) -> tuple[dict[str, bytes], dict[str, str]]:
        """Read bounded source plus its authoritative object-version witnesses."""
        if not repo_prefix.endswith("/"):
            repo_prefix += "/"
        metadata = await self._repo.list_with_metadata(repo_prefix)
        source_metadata = {
            key[len(repo_prefix):]: item
            for key, item in metadata.items()
            if key[len(repo_prefix):] and not key[len(repo_prefix):].endswith("/")
            and key[len(repo_prefix):] != "app.yaml" and ".tmp." not in key[len(repo_prefix):]
        }
        sizes = {path: item.size for path, item in source_metadata.items()}
        validate_app_source_metadata(sizes)
        files: dict[str, bytes] = {}
        for path in sizes:
            body = await self._repo.read(repo_prefix + path)
            if is_compiler_text_source(path):
                validate_app_source_write(path, body)
            files[path] = body
        versions = {path: str(item.version) for path, item in source_metadata.items()}
        if await self.source_versions(repo_prefix) != versions:
            raise ValueError("App source changed while creating its publish snapshot; retry.")
        return files, versions

    async def source_files(self, repo_prefix: str) -> dict[str, bytes]:
        """Read and validate the source snapshot that a durable publish freezes."""
        files, _versions = await self.source_snapshot(repo_prefix)
        return files

    async def source_versions(self, repo_prefix: str) -> dict[str, str]:
        """Return the object-store concurrency witnesses without reading bodies."""
        if not repo_prefix.endswith("/"):
            repo_prefix += "/"
        metadata = await self._repo.list_with_metadata(repo_prefix)
        versions = {
            key[len(repo_prefix):]: str(item.version)
            for key, item in metadata.items()
            if key[len(repo_prefix):] and not key[len(repo_prefix):].endswith("/")
            and key[len(repo_prefix):] != "app.yaml" and ".tmp." not in key[len(repo_prefix):]
        }
        validate_app_source_metadata({
            key[len(repo_prefix):]: item.size
            for key, item in metadata.items()
            if key[len(repo_prefix):] and not key[len(repo_prefix):].endswith("/")
            and key[len(repo_prefix):] != "app.yaml" and ".tmp." not in key[len(repo_prefix):]
        })
        return versions

    async def _materialize_source(self, source_dir: Path, repo_prefix: str) -> list[str]:
        files = await self.source_files(repo_prefix)
        for path, body in files.items():
            destination = source_dir / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)
        return list(files)

    # Test-facing adapters retain the existing focused synthesis coverage.
    async def _generate_app_tailwind(self, source_dir: Path, sources: list[str]) -> tuple[bool, set[str]]:
        return await generate_app_tailwind(source_dir, sources)

    def _write_entry(self, source_dir: Path, entry_file: str, sources: list[str]) -> None:
        write_entry(source_dir, entry_file, sources)

    def _write_bifrost_package(self, source_dir: Path, sources: list[str]) -> None:
        write_bifrost_package(source_dir, sources)

    async def _run_esbuild(self, config: dict) -> dict:
        return await run_esbuild(config)

    async def _write_live(self, app_id: str, path: str, body: bytes) -> None:
        key = self._app_storage._key(app_id, "live", path)
        async with self._app_storage._get_client() as client:
            await client.put_object(Bucket=self._app_storage._bucket, Key=key, Body=body)


async def build_snapshot_using_file_compute(
    app_id: str,
    source_files: dict[str, bytes],
    dependencies: dict[str, str],
    file_compute: FileCompute,
) -> tuple[BundleResult, dict[str, bytes]]:
    """Run a frozen publish snapshot through the registered App bundle worker."""
    inputs = AppBundleInputs(
        app_id=app_id,
        source_files=source_files,
        dependencies=dependencies,
        mode="preview",
        platform_exports=tuple(sorted(_PLATFORM_EXPORT_NAMES)),
    )
    transfer: FileTransfer | None = None
    try:
        transfer = await encode_file_transfer(encode_app_bundle_inputs, inputs)
        remote = await file_compute("app.bundle.v1", transfer)
        output = await decode_file_result(remote, decode_app_bundle_results)
    finally:
        if transfer is not None:
            transfer.close()
    warnings = [_bundle_message(value) for value in output.warnings]
    errors = [_bundle_message(value) for value in output.errors]
    if not output.success:
        return BundleResult(False, errors=errors, warnings=warnings, duration_ms=output.duration_ms), {}
    manifest = BundleManifest(
        entry=output.entry or "",
        css=output.css,
        outputs=list(output.outputs),
        duration_ms=output.duration_ms,
        warnings=warnings,
        dependencies=output.dependencies,
    )
    return BundleResult(True, manifest=manifest, warnings=warnings, duration_ms=output.duration_ms), output.artifacts


def _bundle_message(value: dict[str, object]) -> BundleMessage:
    return BundleMessage(
        text=str(value.get("text", "")),
        file=value.get("file") if isinstance(value.get("file"), str) else None,
        line=value.get("line") if isinstance(value.get("line"), int) else None,
        column=value.get("column") if isinstance(value.get("column"), int) else None,
        line_text=value.get("line_text") if isinstance(value.get("line_text"), str) else None,
    )


async def build_with_migrate(
    app_id: str, repo_prefix: str, mode: Mode, dependencies: dict[str, str] | None = None, *, db: AsyncSession
) -> tuple[BundleResult, bool]:
    """Run source auto-migration, then the intentionally local build path."""
    from src.services.app_bundler.auto_migrate import auto_migrate_repo_prefix

    migrated, _ = await auto_migrate_repo_prefix(app_id, repo_prefix, db)
    return await BundlerService().build(app_id, repo_prefix, mode, dependencies), migrated
