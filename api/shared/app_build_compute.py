"""Bounded, file-backed transfer codec for remote App compilation.

The API process prepares an :class:`~shared.solution_app_compiler.AppBuildInputs`
instance, but a remote compiler must use its *own* process environment.  This
module serializes only the provenance inputs needed by the compiler (source,
SDK, dependencies, and recipe) into a versioned ZIP transfer.  The ZIP itself
is always spooled to a temporary file, and transport messages contain one
bounded base64 chunk at a time.

It deliberately has no job, storage, or platform dependencies.  A host can
send ``transfer.manifest.to_dict()`` followed by ``chunk.to_dict()`` messages;
the receiving launcher feeds those messages to ``FileTransferReceiver``.
Both transfer objects own temporary files and must be closed (or cancelled).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from shared.solution_app_compiler import AppBuildInputs, AppBuildResult
from shared.worker_transfer import (
    FILE_TRANSFER_VERSION as _FILE_TRANSFER_VERSION,
    MAX_CHUNK_BYTES as _MAX_FILE_TRANSFER_CHUNK_BYTES,
    FileTransfer,
    FileTransferManifest,
    ReceivedFileTransfer,
    validate_file_transfer_chunk_size,
)


MIB = 1024 * 1024

# This is the same allowance enforced for retained standalone App source in
# ``application_source_archive``.  Keep the compiler handoff at that boundary,
# rather than allowing a remote launcher to expand a larger source tree.
MAX_SOURCE_EXPANDED_BYTES = 256 * MIB
MAX_SOURCE_MEMBERS = 50_000
MAX_SDK_BYTES = 64 * MIB
MAX_METADATA_BYTES = 64 * 1024

# Result artifacts are independently bounded.  A Vite build must not turn an
# admitted remote compile into an unbounded API-to-launcher transfer.
MAX_RESULT_EXPANDED_BYTES = 512 * MIB
MAX_RESULT_MEMBERS = 50_000

# The compressed archive is separately bounded.  This protects the transfer
# receiver before it attempts ZIP parsing, including for incompressible SDKs.
MAX_INPUT_TRANSFER_BYTES = MAX_SOURCE_EXPANDED_BYTES + MAX_SDK_BYTES + 16 * MIB
MAX_RESULT_TRANSFER_BYTES = MAX_RESULT_EXPANDED_BYTES + 16 * MIB
_INPUT_KIND = "app_build_inputs"
_RESULT_KIND = "app_build_result"
_ARCHIVE_MANIFEST = "manifest.json"
_SDK_MEMBER = "sdk.tar.gz"
_SOURCE_PREFIX = "source/"
_ARTIFACT_PREFIX = "artifacts/"


class AppBuildCodecError(ValueError):
    """An App build ZIP or its provenance is malformed or unsafe."""


TransferKind = Literal["app_build_inputs", "app_build_result"]


def encode_app_build_inputs(
    inputs: AppBuildInputs, *, chunk_size: int = _MAX_FILE_TRANSFER_CHUNK_BYTES
) -> FileTransfer:
    """Create a bounded transfer for an API-prepared compilation request.

    ``inputs.environment`` is intentionally omitted.  The remote launcher must
    pass its actual execution environment to :func:`decode_app_build_inputs`.
    """
    validate_file_transfer_chunk_size(chunk_size)
    source_files = _validated_files(
        inputs.source_files,
        prefix=_SOURCE_PREFIX,
        max_members=MAX_SOURCE_MEMBERS,
        max_expanded_bytes=MAX_SOURCE_EXPANDED_BYTES,
        label="App source",
    )
    if len(inputs.sdk_tarball) > MAX_SDK_BYTES:
        raise AppBuildCodecError("App build SDK exceeds the size limit.")
    metadata = {
        "version": _FILE_TRANSFER_VERSION,
        "kind": _INPUT_KIND,
        "app_id": str(inputs.app_id),
        "dependencies": _string_mapping(inputs.dependencies, "dependencies"),
        "recipe": _json_object(inputs.recipe, "recipe"),
        "require_lockfile": inputs.require_lockfile,
        "package_name": inputs.package_name,
    }
    _validate_input_metadata(metadata)
    return _write_transfer(
        _INPUT_KIND,
        metadata,
        [(_SDK_MEMBER, inputs.sdk_tarball), *((_SOURCE_PREFIX + path, data) for path, data in source_files.items())],
        chunk_size=chunk_size,
    )


def decode_app_build_inputs(
    transfer: ReceivedFileTransfer,
    *,
    environment: Mapping[str, str],
) -> AppBuildInputs:
    """Decode input provenance and bind it to the remote worker environment."""
    metadata, files = _read_transfer(transfer, expected_kind=_INPUT_KIND)
    _validate_input_metadata(metadata)
    source = _files_with_prefix(
        files,
        prefix=_SOURCE_PREFIX,
        max_members=MAX_SOURCE_MEMBERS,
        max_expanded_bytes=MAX_SOURCE_EXPANDED_BYTES,
        label="App source",
    )
    sdk = files.get(_SDK_MEMBER)
    if sdk is None or len(sdk) > MAX_SDK_BYTES:
        raise AppBuildCodecError("App build transfer has an invalid SDK.")
    expected = {_SDK_MEMBER, *(_SOURCE_PREFIX + path for path in source)}
    if set(files) != expected:
        raise AppBuildCodecError("App build input transfer has unsupported members.")
    return AppBuildInputs(
        app_id=metadata["app_id"],
        source_files=source,
        dependencies=_string_mapping(metadata["dependencies"], "dependencies"),
        sdk_tarball=sdk,
        recipe=_json_object(metadata["recipe"], "recipe"),
        environment=_string_mapping(environment, "environment"),
        require_lockfile=cast(bool, metadata["require_lockfile"]),
        package_name=cast(str, metadata["package_name"]),
    )


def encode_app_build_result(
    result: AppBuildResult, *, chunk_size: int = _MAX_FILE_TRANSFER_CHUNK_BYTES
) -> FileTransfer:
    """Create a bounded artifact transfer from a remote compiler result."""
    validate_file_transfer_chunk_size(chunk_size)
    artifacts = _validated_files(
        result.artifacts,
        prefix=_ARTIFACT_PREFIX,
        max_members=MAX_RESULT_MEMBERS,
        max_expanded_bytes=MAX_RESULT_EXPANDED_BYTES,
        label="App build artifacts",
    )
    metadata = {
        "version": _FILE_TRANSFER_VERSION,
        "kind": _RESULT_KIND,
        "build_identity": result.build_identity,
    }
    _validate_result_metadata(metadata)
    return _write_transfer(
        _RESULT_KIND,
        metadata,
        [(_ARTIFACT_PREFIX + path, data) for path, data in artifacts.items()],
        chunk_size=chunk_size,
    )


def decode_app_build_result(transfer: ReceivedFileTransfer) -> AppBuildResult:
    """Decode verified remote artifacts after applying their output bounds."""
    metadata, files = _read_transfer(transfer, expected_kind=_RESULT_KIND)
    _validate_result_metadata(metadata)
    artifacts = _files_with_prefix(
        files,
        prefix=_ARTIFACT_PREFIX,
        max_members=MAX_RESULT_MEMBERS,
        max_expanded_bytes=MAX_RESULT_EXPANDED_BYTES,
        label="App build artifacts",
    )
    if set(files) != {_ARTIFACT_PREFIX + path for path in artifacts}:
        raise AppBuildCodecError("App build result transfer has unsupported members.")
    return AppBuildResult(artifacts=artifacts, build_identity=cast(str, metadata["build_identity"]))


# Short aliases keep host and launcher call sites obvious.
serialize_app_build_inputs = encode_app_build_inputs
deserialize_app_build_inputs = decode_app_build_inputs
serialize_app_build_result = encode_app_build_result
deserialize_app_build_result = decode_app_build_result


def _write_transfer(
    kind: TransferKind,
    metadata: dict[str, object],
    members: list[tuple[str, bytes]],
    *,
    chunk_size: int,
) -> FileTransfer:
    fd, name = tempfile.mkstemp(prefix="bifrost-appbuild-transfer-")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(
            raw, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False
        ) as archive:
            archive.writestr(_ARCHIVE_MANIFEST, _json_bytes(metadata))
            for member, content in members:
                archive.writestr(member, content)
        total_size, digest = _file_size_and_digest(path, limit=_transfer_limit(kind))
        manifest = FileTransferManifest(
            version=_FILE_TRANSFER_VERSION,
            kind=kind,
            total_size=total_size,
            sha256=digest,
            chunk_size=chunk_size,
            chunk_count=(total_size + chunk_size - 1) // chunk_size,
            max_total_size=_transfer_limit(kind),
        )
        manifest.validate()
        return FileTransfer(manifest, path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _read_transfer(
    transfer: ReceivedFileTransfer, *, expected_kind: TransferKind
) -> tuple[dict[str, object], dict[str, bytes]]:
    if transfer.manifest.kind != expected_kind:
        raise AppBuildCodecError("App build transfer kind does not match its decoder.")
    max_members = 2 + (MAX_SOURCE_MEMBERS if expected_kind == _INPUT_KIND else MAX_RESULT_MEMBERS)
    try:
        with zipfile.ZipFile(transfer.path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > max_members:
                raise AppBuildCodecError("App build transfer has too many archive members.")
            seen: set[str] = set()
            files: dict[str, bytes] = {}
            content_members = 0
            expanded = 0
            content_limit = (
                MAX_SOURCE_EXPANDED_BYTES if expected_kind == _INPUT_KIND else MAX_RESULT_EXPANDED_BYTES
            )
            for info in infos:
                _validate_archive_info(info, seen)
                if info.filename == _ARCHIVE_MANIFEST:
                    if info.file_size > MAX_METADATA_BYTES:
                        raise AppBuildCodecError("App build transfer metadata exceeds the size limit.")
                elif info.filename == _SDK_MEMBER:
                    if expected_kind != _INPUT_KIND or info.file_size > MAX_SDK_BYTES:
                        raise AppBuildCodecError("App build transfer has an invalid SDK.")
                elif info.filename.startswith(_SOURCE_PREFIX):
                    if expected_kind != _INPUT_KIND:
                        raise AppBuildCodecError("App build transfer has unsupported members.")
                    _safe_relative_path(info.filename[len(_SOURCE_PREFIX):])
                    content_members += 1
                    expanded += info.file_size
                elif info.filename.startswith(_ARTIFACT_PREFIX):
                    if expected_kind != _RESULT_KIND:
                        raise AppBuildCodecError("App build transfer has unsupported members.")
                    _safe_relative_path(info.filename[len(_ARTIFACT_PREFIX):])
                    content_members += 1
                    expanded += info.file_size
                else:
                    raise AppBuildCodecError("App build transfer has unsupported members.")
                if content_members > (
                    MAX_SOURCE_MEMBERS if expected_kind == _INPUT_KIND else MAX_RESULT_MEMBERS
                ):
                    raise AppBuildCodecError("App build transfer has too many archive members.")
                if expanded > content_limit:
                    raise AppBuildCodecError("App build transfer exceeds the expanded byte limit.")
                files[info.filename] = archive.read(info)
    except AppBuildCodecError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise AppBuildCodecError("App build transfer is not a valid ZIP archive.") from exc
    raw_metadata = files.pop(_ARCHIVE_MANIFEST, None)
    if raw_metadata is None:
        raise AppBuildCodecError("App build transfer is missing metadata.")
    try:
        metadata = json.loads(raw_metadata)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppBuildCodecError("App build transfer metadata is invalid.") from exc
    if not isinstance(metadata, dict):
        raise AppBuildCodecError("App build transfer metadata is invalid.")
    if metadata.get("version") != _FILE_TRANSFER_VERSION or metadata.get("kind") != expected_kind:
        raise AppBuildCodecError("App build transfer metadata version does not match.")
    return metadata, files


def _validate_archive_info(info: zipfile.ZipInfo, seen: set[str]) -> None:
    if info.is_dir() or info.flag_bits & 0x1 or stat.S_ISLNK(info.external_attr >> 16):
        raise AppBuildCodecError("App build transfer contains an unsafe archive member.")
    if info.filename in seen:
        raise AppBuildCodecError("App build transfer contains duplicate archive members.")
    seen.add(info.filename)
    if "\\" in info.filename:
        raise AppBuildCodecError("App build transfer contains an unsafe archive path.")
    _safe_relative_path(info.filename)


def _files_with_prefix(
    files: Mapping[str, bytes],
    *,
    prefix: str,
    max_members: int,
    max_expanded_bytes: int,
    label: str,
) -> dict[str, bytes]:
    selected = {name[len(prefix):]: content for name, content in files.items() if name.startswith(prefix)}
    return _validated_files(
        selected,
        prefix=prefix,
        max_members=max_members,
        max_expanded_bytes=max_expanded_bytes,
        label=label,
    )


def _validated_files(
    files: Mapping[str, bytes],
    *,
    prefix: str,
    max_members: int,
    max_expanded_bytes: int,
    label: str,
) -> dict[str, bytes]:
    if len(files) > max_members:
        raise AppBuildCodecError(f"{label} exceeds the {max_members} member limit.")
    validated: dict[str, bytes] = {}
    expanded = 0
    for raw_path, content in files.items():
        path = _safe_relative_path(raw_path)
        if path in validated:
            raise AppBuildCodecError(f"{label} contains duplicate path: {path}")
        if not isinstance(content, bytes):
            raise AppBuildCodecError(f"{label} contains non-bytes content.")
        expanded += len(content)
        if expanded > max_expanded_bytes:
            raise AppBuildCodecError(f"{label} exceeds the expanded byte limit.")
        validated[path] = content
    return dict(sorted(validated.items()))


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AppBuildCodecError("App build transfer contains an unsafe archive path.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or value.startswith("./"):
        raise AppBuildCodecError("App build transfer contains an unsafe archive path.")
    if value in {".", "/"} or not path.parts:
        raise AppBuildCodecError("App build transfer contains an unsafe archive path.")
    return value


def _validate_input_metadata(metadata: Mapping[str, object]) -> None:
    expected = {"version", "kind", "app_id", "dependencies", "recipe", "require_lockfile", "package_name"}
    if set(metadata) != expected or not isinstance(metadata.get("app_id"), str) or not metadata["app_id"]:
        raise AppBuildCodecError("App build input metadata is invalid.")
    _string_mapping(metadata.get("dependencies"), "dependencies")
    _json_object(metadata.get("recipe"), "recipe")
    if type(metadata.get("require_lockfile")) is not bool:
        raise AppBuildCodecError("App build input metadata is invalid.")
    if not isinstance(metadata.get("package_name"), str) or not metadata["package_name"]:
        raise AppBuildCodecError("App build input metadata is invalid.")


def _validate_result_metadata(metadata: Mapping[str, object]) -> None:
    expected = {"version", "kind", "build_identity"}
    if set(metadata) != expected or not isinstance(metadata.get("build_identity"), str) or not metadata["build_identity"]:
        raise AppBuildCodecError("App build result metadata is invalid.")


def _string_mapping(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise AppBuildCodecError(f"App build {label} must be a string mapping.")
    return dict(value)


def _json_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppBuildCodecError(f"App build {label} must be an object.")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AppBuildCodecError(f"App build {label} must be JSON serializable.") from exc
    return json.loads(encoded)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AppBuildCodecError("App build transfer metadata is not JSON serializable.") from exc


def _transfer_limit(kind: TransferKind) -> int:
    return MAX_INPUT_TRANSFER_BYTES if kind == _INPUT_KIND else MAX_RESULT_TRANSFER_BYTES


def _file_size_and_digest(path: Path, *, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as archive:
        while data := archive.read(_MAX_FILE_TRANSFER_CHUNK_BYTES):
            total += len(data)
            if total > limit:
                raise AppBuildCodecError("App build transfer exceeds the size limit.")
            digest.update(data)
    return total, digest.hexdigest()
