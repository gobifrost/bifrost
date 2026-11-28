"""Bounded source and artifact transfers for the inline App bundle worker.

The API owns source migration, object storage, publication, and activation.  A
physical worker receives only this immutable source snapshot and returns an
archive of compiler output.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from shared.worker_transfer import (
    FILE_TRANSFER_VERSION,
    MAX_CHUNK_BYTES,
    FileTransfer,
    FileTransferManifest,
    ReceivedFileTransfer,
    validate_file_transfer_chunk_size,
)
from src.services.app_resource_limits import (
    DEFAULT_APP_ARTIFACT_LIMITS,
    DEFAULT_APP_SOURCE_LIMITS,
    validate_app_artifacts,
    validate_app_source_metadata,
    validate_app_source_write,
)


MIB = 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_INPUT_TRANSFER_BYTES = DEFAULT_APP_SOURCE_LIMITS.max_total_bytes + 16 * MIB
MAX_RESULT_TRANSFER_BYTES = DEFAULT_APP_ARTIFACT_LIMITS.max_total_bytes + 16 * MIB
APP_BUNDLE_INPUT_KIND = "app_bundle_inputs"
APP_BUNDLE_RESULT_KIND = "app_bundle_results"
_MANIFEST_MEMBER = "manifest.json"
_SOURCE_PREFIX = "source/"
_ARTIFACT_PREFIX = "artifacts/"


class AppBundleComputeError(ValueError):
    """The App-bundle worker payload is malformed, unsafe, or over budget."""


@dataclass(frozen=True)
class AppBundleInputs:
    app_id: str
    source_files: dict[str, bytes]
    dependencies: dict[str, str]
    mode: Literal["preview", "live"]
    platform_exports: tuple[str, ...]


@dataclass(frozen=True)
class AppBundleOutput:
    success: bool
    entry: str | None
    css: str | None
    outputs: tuple[str, ...]
    duration_ms: int
    warnings: tuple[dict[str, object], ...]
    errors: tuple[dict[str, object], ...]
    dependencies: dict[str, str]
    artifacts: dict[str, bytes]


def encode_app_bundle_inputs(
    inputs: AppBundleInputs, *, chunk_size: int = MAX_CHUNK_BYTES
) -> FileTransfer:
    validate_file_transfer_chunk_size(chunk_size)
    source_files = _validated_source_files(inputs.source_files)
    metadata: dict[str, object] = {
        "version": FILE_TRANSFER_VERSION,
        "kind": APP_BUNDLE_INPUT_KIND,
        "app_id": inputs.app_id,
        "dependencies": _string_mapping(inputs.dependencies, "dependencies"),
        "mode": inputs.mode,
        "platform_exports": _platform_exports(inputs.platform_exports),
    }
    _validate_input_metadata(metadata)
    return _write_transfer(
        APP_BUNDLE_INPUT_KIND,
        metadata,
        [(_SOURCE_PREFIX + path, data) for path, data in source_files.items()],
        chunk_size=chunk_size,
    )


def decode_app_bundle_inputs(transfer: ReceivedFileTransfer) -> AppBundleInputs:
    metadata, files = _read_transfer(transfer, expected_kind=APP_BUNDLE_INPUT_KIND)
    _validate_input_metadata(metadata)
    source_files = _validated_source_files(_files_with_prefix(files, _SOURCE_PREFIX))
    if set(files) != {_SOURCE_PREFIX + path for path in source_files}:
        raise AppBundleComputeError("App bundle input transfer has unsupported members.")
    return AppBundleInputs(
        app_id=str(metadata["app_id"]),
        source_files=source_files,
        dependencies=_string_mapping(metadata["dependencies"], "dependencies"),
        mode=cast(Literal["preview", "live"], metadata["mode"]),
        platform_exports=tuple(_platform_exports(metadata["platform_exports"])),
    )


def encode_app_bundle_results(
    output: AppBundleOutput, *, chunk_size: int = MAX_CHUNK_BYTES
) -> FileTransfer:
    validate_file_transfer_chunk_size(chunk_size)
    artifacts = _validated_artifacts(output.artifacts) if output.success else {}
    metadata: dict[str, object] = {
        "version": FILE_TRANSFER_VERSION,
        "kind": APP_BUNDLE_RESULT_KIND,
        "success": output.success,
        "entry": output.entry,
        "css": output.css,
        "outputs": list(output.outputs),
        "duration_ms": output.duration_ms,
        "warnings": list(output.warnings),
        "errors": list(output.errors),
        "dependencies": _string_mapping(output.dependencies, "dependencies"),
    }
    _validate_result_metadata(metadata)
    if output.success and set(artifacts) != set(output.outputs):
        raise AppBundleComputeError("App bundle result artifacts do not match its manifest.")
    return _write_transfer(
        APP_BUNDLE_RESULT_KIND,
        metadata,
        [(_ARTIFACT_PREFIX + path, data) for path, data in artifacts.items()],
        chunk_size=chunk_size,
    )


def decode_app_bundle_results(transfer: ReceivedFileTransfer) -> AppBundleOutput:
    metadata, files = _read_transfer(transfer, expected_kind=APP_BUNDLE_RESULT_KIND)
    _validate_result_metadata(metadata)
    artifacts = _validated_artifacts(_files_with_prefix(files, _ARTIFACT_PREFIX))
    outputs = tuple(cast(list[str], metadata["outputs"]))
    if metadata["success"]:
        if set(files) != {_ARTIFACT_PREFIX + path for path in artifacts} or set(artifacts) != set(outputs):
            raise AppBundleComputeError("App bundle result artifacts do not match its manifest.")
    elif files or artifacts or outputs:
        raise AppBundleComputeError("Failed App bundle result must not contain artifacts.")
    return AppBundleOutput(
        success=cast(bool, metadata["success"]),
        entry=cast(str | None, metadata["entry"]),
        css=cast(str | None, metadata["css"]),
        outputs=outputs,
        duration_ms=cast(int, metadata["duration_ms"]),
        warnings=tuple(cast(list[dict[str, object]], metadata["warnings"])),
        errors=tuple(cast(list[dict[str, object]], metadata["errors"])),
        dependencies=_string_mapping(metadata["dependencies"], "dependencies"),
        artifacts=artifacts,
    )


def _write_transfer(kind: str, metadata: dict[str, object], members: list[tuple[str, bytes]], *, chunk_size: int) -> FileTransfer:
    fd, name = tempfile.mkstemp(prefix="bifrost-app-bundle-")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
            archive.writestr(_MANIFEST_MEMBER, _json_bytes(metadata))
            for member, body in members:
                archive.writestr(member, body)
        size, digest = _file_size_and_digest(path, _transfer_limit(kind))
        manifest = FileTransferManifest(FILE_TRANSFER_VERSION, kind, size, digest, chunk_size, (size + chunk_size - 1) // chunk_size, _transfer_limit(kind))
        manifest.validate()
        return FileTransfer(manifest, path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _read_transfer(transfer: ReceivedFileTransfer, *, expected_kind: str) -> tuple[dict[str, object], dict[str, bytes]]:
    if transfer.manifest.kind != expected_kind or transfer.manifest.total_size > _transfer_limit(expected_kind):
        raise AppBundleComputeError("App bundle transfer kind or size is invalid.")
    try:
        with zipfile.ZipFile(transfer.path) as archive:
            infos = archive.infolist()
            max_files = (
                DEFAULT_APP_SOURCE_LIMITS.max_files
                if expected_kind == APP_BUNDLE_INPUT_KIND
                else DEFAULT_APP_ARTIFACT_LIMITS.max_files
            )
            max_total = (
                DEFAULT_APP_SOURCE_LIMITS.max_total_bytes
                if expected_kind == APP_BUNDLE_INPUT_KIND
                else DEFAULT_APP_ARTIFACT_LIMITS.max_total_bytes
            )
            max_file = (
                DEFAULT_APP_SOURCE_LIMITS.max_file_bytes
                if expected_kind == APP_BUNDLE_INPUT_KIND
                else DEFAULT_APP_ARTIFACT_LIMITS.max_file_bytes
            )
            prefix = _SOURCE_PREFIX if expected_kind == APP_BUNDLE_INPUT_KIND else _ARTIFACT_PREFIX
            if not infos or len(infos) > max_files + 1:
                raise AppBundleComputeError("App bundle transfer has too many archive members.")
            files: dict[str, bytes] = {}
            content_total = 0
            for info in infos:
                _validate_info(info, files)
                if info.filename == _MANIFEST_MEMBER and info.file_size > MAX_METADATA_BYTES:
                    raise AppBundleComputeError("App bundle transfer metadata exceeds the size limit.")
                elif info.filename != _MANIFEST_MEMBER:
                    if not info.filename.startswith(prefix) or info.file_size > max_file:
                        raise AppBundleComputeError("App bundle transfer has unsupported or oversized members.")
                    content_total += info.file_size
                    if content_total > max_total:
                        raise AppBundleComputeError("App bundle transfer exceeds the expanded byte limit.")
                files[info.filename] = archive.read(info)
    except AppBundleComputeError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise AppBundleComputeError("App bundle transfer is not a valid ZIP archive.") from exc
    raw = files.pop(_MANIFEST_MEMBER, None)
    if raw is None:
        raise AppBundleComputeError("App bundle transfer is missing metadata.")
    try:
        metadata = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppBundleComputeError("App bundle transfer metadata is invalid.") from exc
    if not isinstance(metadata, dict) or metadata.get("version") != FILE_TRANSFER_VERSION or metadata.get("kind") != expected_kind:
        raise AppBundleComputeError("App bundle transfer metadata version does not match.")
    return metadata, files


def _validate_info(info: zipfile.ZipInfo, seen: Mapping[str, bytes]) -> None:
    if info.is_dir() or info.flag_bits & 0x1 or stat.S_ISLNK(info.external_attr >> 16) or info.filename in seen or "\\" in info.filename:
        raise AppBundleComputeError("App bundle transfer contains an unsafe or duplicate member.")
    _safe_path(info.filename)


def _files_with_prefix(files: Mapping[str, bytes], prefix: str) -> dict[str, bytes]:
    return {name[len(prefix):]: body for name, body in files.items() if name.startswith(prefix)}


def _validated_source_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    checked = _checked_files(files, label="App bundle source")
    validate_app_source_metadata({path: len(body) for path, body in checked.items()})
    for path, body in checked.items():
        validate_app_source_write(path, body)
    return checked


def _validated_artifacts(files: Mapping[str, bytes]) -> dict[str, bytes]:
    checked = _checked_files(files, label="App bundle artifacts")
    validate_app_artifacts({path: len(body) for path, body in checked.items()})
    return checked


def _checked_files(files: Mapping[str, bytes], *, label: str) -> dict[str, bytes]:
    checked: dict[str, bytes] = {}
    for raw_path, body in files.items():
        path = _safe_path(raw_path)
        if path in checked or not isinstance(body, bytes):
            raise AppBundleComputeError(f"{label} has an invalid member.")
        checked[path] = body
    return dict(sorted(checked.items()))


def _safe_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AppBundleComputeError("App bundle transfer contains an unsafe archive path.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or value.startswith("./") or value in {".", "/"}:
        raise AppBundleComputeError("App bundle transfer contains an unsafe archive path.")
    return value


def _string_mapping(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise AppBundleComputeError(f"App bundle {label} is invalid.")
    return dict(sorted(value.items()))


def _platform_exports(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(name, str) and name.isidentifier() for name in value):
        raise AppBundleComputeError("App bundle platform exports are invalid.")
    exports = sorted(set(value))
    if not exports:
        raise AppBundleComputeError("App bundle platform exports are invalid.")
    return exports


def _messages(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) and isinstance(item.get("text"), str) for item in value):
        raise AppBundleComputeError(f"App bundle {label} are invalid.")
    return value


def _validate_input_metadata(metadata: Mapping[str, object]) -> None:
    if set(metadata) != {"version", "kind", "app_id", "dependencies", "mode", "platform_exports"} or not isinstance(metadata.get("app_id"), str) or not metadata["app_id"] or metadata.get("mode") not in {"preview", "live"}:
        raise AppBundleComputeError("App bundle input metadata is invalid.")
    _string_mapping(metadata["dependencies"], "dependencies")
    _platform_exports(metadata["platform_exports"])


def _validate_result_metadata(metadata: Mapping[str, object]) -> None:
    required = {"version", "kind", "success", "entry", "css", "outputs", "duration_ms", "warnings", "errors", "dependencies"}
    if set(metadata) != required or type(metadata.get("success")) is not bool or type(metadata.get("duration_ms")) is not int or metadata["duration_ms"] < 0:
        raise AppBundleComputeError("App bundle result metadata is invalid.")
    if metadata["entry"] is not None and not isinstance(metadata["entry"], str) or metadata["css"] is not None and not isinstance(metadata["css"], str):
        raise AppBundleComputeError("App bundle result metadata is invalid.")
    if not isinstance(metadata["outputs"], list) or not all(isinstance(value, str) for value in metadata["outputs"]):
        raise AppBundleComputeError("App bundle result metadata is invalid.")
    if len(set(metadata["outputs"])) != len(metadata["outputs"]):
        raise AppBundleComputeError("App bundle result metadata is invalid.")
    _messages(metadata["warnings"], "warnings")
    _messages(metadata["errors"], "errors")
    _string_mapping(metadata["dependencies"], "dependencies")
    if metadata["success"] and (not metadata["entry"] or metadata["errors"]):
        raise AppBundleComputeError("App bundle result metadata is invalid.")
    if not metadata["success"] and (metadata["entry"] is not None or metadata["css"] is not None or metadata["outputs"] or not metadata["errors"]):
        raise AppBundleComputeError("App bundle result metadata is invalid.")


def _transfer_limit(kind: str) -> int:
    if kind == APP_BUNDLE_INPUT_KIND:
        return MAX_INPUT_TRANSFER_BYTES
    if kind == APP_BUNDLE_RESULT_KIND:
        return MAX_RESULT_TRANSFER_BYTES
    raise AppBundleComputeError("Unknown App bundle transfer kind.")


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _file_size_and_digest(path: Path, limit: int) -> tuple[int, str]:
    digest, total = hashlib.sha256(), 0
    with path.open("rb") as source:
        while chunk := source.read(MAX_CHUNK_BYTES):
            total += len(chunk)
            if total > limit:
                raise AppBundleComputeError("App bundle transfer exceeds the size limit.")
            digest.update(chunk)
    return total, digest.hexdigest()
