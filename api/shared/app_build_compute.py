"""Bounded, file-backed transfer codec for remote App compilation.

The API process prepares an :class:`~shared.solution_app_compiler.AppBuildInputs`
instance, but a remote compiler must use its *own* process environment.  This
module serializes only the provenance inputs needed by the compiler (source,
SDK, dependencies, and recipe) into a versioned ZIP transfer.  The ZIP itself
is always spooled to a temporary file, and transport messages contain one
bounded base64 chunk at a time.

It deliberately has no job, storage, or platform dependencies.  A host can
send ``transfer.manifest.to_dict()`` followed by ``chunk.to_dict()`` messages;
the receiving launcher feeds those messages to ``AppBuildTransferReceiver``.
Both transfer objects own temporary files and must be closed (or cancelled).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from shared.solution_app_compiler import AppBuildInputs, AppBuildResult


APP_BUILD_TRANSFER_VERSION = 1
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
MAX_CHUNK_BYTES = MIB

_INPUT_KIND = "app_build_inputs"
_RESULT_KIND = "app_build_result"
_ARCHIVE_MANIFEST = "manifest.json"
_SDK_MEMBER = "sdk.tar.gz"
_SOURCE_PREFIX = "source/"
_ARTIFACT_PREFIX = "artifacts/"


class AppBuildTransferError(ValueError):
    """A transfer is malformed, unsafe, incomplete, or over its budget."""


TransferKind = Literal["app_build_inputs", "app_build_result"]


@dataclass(frozen=True)
class AppBuildTransferManifest:
    """Small JSON-safe declaration for one file-backed transfer."""

    version: int
    kind: TransferKind
    total_size: int
    sha256: str
    chunk_size: int
    chunk_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "kind": self.kind,
            "total_size": self.total_size,
            "sha256": self.sha256,
            "chunk_size": self.chunk_size,
            "chunk_count": self.chunk_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AppBuildTransferManifest":
        try:
            manifest = cls(
                version=value["version"],  # type: ignore[arg-type]
                kind=value["kind"],  # type: ignore[arg-type]
                total_size=value["total_size"],  # type: ignore[arg-type]
                sha256=value["sha256"],  # type: ignore[arg-type]
                chunk_size=value["chunk_size"],  # type: ignore[arg-type]
                chunk_count=value["chunk_count"],  # type: ignore[arg-type]
            )
        except (KeyError, TypeError) as exc:
            raise AppBuildTransferError("Invalid App build transfer manifest.") from exc
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.version != APP_BUILD_TRANSFER_VERSION:
            raise AppBuildTransferError("Unsupported App build transfer version.")
        if not isinstance(self.kind, str) or self.kind not in {_INPUT_KIND, _RESULT_KIND}:
            raise AppBuildTransferError("Unknown App build transfer kind.")
        if type(self.total_size) is not int or not 0 < self.total_size <= _transfer_limit(self.kind):
            raise AppBuildTransferError("Invalid App build transfer size.")
        if type(self.chunk_size) is not int or not 0 < self.chunk_size <= MAX_CHUNK_BYTES:
            raise AppBuildTransferError("Invalid App build transfer chunk size.")
        expected_chunks = (self.total_size + self.chunk_size - 1) // self.chunk_size
        if type(self.chunk_count) is not int or self.chunk_count != expected_chunks:
            raise AppBuildTransferError("Invalid App build transfer chunk count.")
        if not isinstance(self.sha256, str) or len(self.sha256) != 64:
            raise AppBuildTransferError("Invalid App build transfer digest.")
        try:
            int(self.sha256, 16)
        except ValueError as exc:
            raise AppBuildTransferError("Invalid App build transfer digest.") from exc


@dataclass(frozen=True)
class AppBuildTransferChunk:
    """One ordered JSON-safe base64 transfer message."""

    index: int
    data_b64: str

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "data_b64": self.data_b64}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AppBuildTransferChunk":
        try:
            chunk = cls(index=value["index"], data_b64=value["data_b64"])  # type: ignore[arg-type]
        except (KeyError, TypeError) as exc:
            raise AppBuildTransferError("Invalid App build transfer chunk.") from exc
        if type(chunk.index) is not int or chunk.index < 0 or not isinstance(chunk.data_b64, str):
            raise AppBuildTransferError("Invalid App build transfer chunk.")
        return chunk

    def decode(self) -> bytes:
        if len(self.data_b64) > 4 * ((MAX_CHUNK_BYTES + 2) // 3):
            raise AppBuildTransferError("App build transfer chunk exceeds the size limit.")
        try:
            data = base64.b64decode(self.data_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AppBuildTransferError("Invalid App build transfer chunk encoding.") from exc
        if len(data) > MAX_CHUNK_BYTES:
            raise AppBuildTransferError("App build transfer chunk exceeds the size limit.")
        return data


@dataclass
class AppBuildTransfer:
    """A locally produced transfer whose archive is held in a temporary file."""

    manifest: AppBuildTransferManifest
    path: Path

    def iter_chunks(self) -> Iterator[AppBuildTransferChunk]:
        """Yield bounded chunks without loading the archive into memory."""
        with self.path.open("rb") as archive:
            for index in range(self.manifest.chunk_count):
                data = archive.read(self.manifest.chunk_size)
                if not data:
                    raise AppBuildTransferError("App build transfer archive is incomplete.")
                yield AppBuildTransferChunk(index, base64.b64encode(data).decode("ascii"))
            if archive.read(1):
                raise AppBuildTransferError("App build transfer archive exceeds its declared size.")

    def close(self) -> None:
        self.path.unlink(missing_ok=True)

    def cancel(self) -> None:
        """Discard a transfer after cancellation or a failed handoff."""
        self.close()

    def __enter__(self) -> "AppBuildTransfer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass
class ReceivedAppBuildTransfer:
    """A verified received archive; the caller owns cleanup after decoding."""

    manifest: AppBuildTransferManifest
    path: Path

    def close(self) -> None:
        self.path.unlink(missing_ok=True)

    def cancel(self) -> None:
        self.close()

    def __enter__(self) -> "ReceivedAppBuildTransfer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AppBuildTransferReceiver:
    """Spool strictly ordered incoming chunks into one temporary archive."""

    def __init__(self, manifest: AppBuildTransferManifest, *, temp_dir: Path | None = None):
        manifest.validate()
        self.manifest = manifest
        fd, name = tempfile.mkstemp(prefix="bifrost-appbuild-transfer-", dir=temp_dir)
        self._file = os.fdopen(fd, "wb")
        self._path = Path(name)
        self._next_index = 0
        self._size = 0
        self._digest = hashlib.sha256()
        self._finished = False
        self._owns_path = True

    @property
    def path(self) -> Path:
        return self._path

    def add_chunk(self, chunk: AppBuildTransferChunk | Mapping[str, object]) -> None:
        """Write exactly the next chunk; duplicates and reordering fail closed."""
        if self._finished:
            raise AppBuildTransferError("App build transfer has already finished.")
        if not isinstance(chunk, AppBuildTransferChunk):
            chunk = AppBuildTransferChunk.from_dict(chunk)
        if chunk.index != self._next_index:
            raise AppBuildTransferError("App build transfer chunks must be ordered without duplicates.")
        if chunk.index >= self.manifest.chunk_count:
            raise AppBuildTransferError("App build transfer has too many chunks.")
        data = chunk.decode()
        expected_size = self.manifest.chunk_size
        if chunk.index == self.manifest.chunk_count - 1:
            expected_size = self.manifest.total_size - self._size
        if len(data) != expected_size:
            raise AppBuildTransferError("App build transfer chunk has an unexpected size.")
        self._file.write(data)
        self._digest.update(data)
        self._size += len(data)
        self._next_index += 1

    def finish(self) -> ReceivedAppBuildTransfer:
        """Verify the declared byte count and digest, then transfer file ownership."""
        if self._finished:
            raise AppBuildTransferError("App build transfer has already finished.")
        try:
            if self._next_index != self.manifest.chunk_count or self._size != self.manifest.total_size:
                raise AppBuildTransferError("App build transfer is incomplete.")
            if self._digest.hexdigest() != self.manifest.sha256:
                raise AppBuildTransferError("App build transfer digest does not match.")
            self._file.close()
            self._finished = True
            self._owns_path = False
            return ReceivedAppBuildTransfer(self.manifest, self._path)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()
        if self._owns_path:
            self._path.unlink(missing_ok=True)
        self._finished = True

    def cancel(self) -> None:
        """Discard a partial transfer when its platform job is cancelled."""
        self.close()

    def __enter__(self) -> "AppBuildTransferReceiver":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def encode_app_build_inputs(
    inputs: AppBuildInputs, *, chunk_size: int = MAX_CHUNK_BYTES
) -> AppBuildTransfer:
    """Create a bounded transfer for an API-prepared compilation request.

    ``inputs.environment`` is intentionally omitted.  The remote launcher must
    pass its actual execution environment to :func:`decode_app_build_inputs`.
    """
    _validate_chunk_size(chunk_size)
    source_files = _validated_files(
        inputs.source_files,
        prefix=_SOURCE_PREFIX,
        max_members=MAX_SOURCE_MEMBERS,
        max_expanded_bytes=MAX_SOURCE_EXPANDED_BYTES,
        label="App source",
    )
    if len(inputs.sdk_tarball) > MAX_SDK_BYTES:
        raise AppBuildTransferError("App build SDK exceeds the size limit.")
    metadata = {
        "version": APP_BUILD_TRANSFER_VERSION,
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
    transfer: ReceivedAppBuildTransfer,
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
        raise AppBuildTransferError("App build transfer has an invalid SDK.")
    expected = {_SDK_MEMBER, *(_SOURCE_PREFIX + path for path in source)}
    if set(files) != expected:
        raise AppBuildTransferError("App build input transfer has unsupported members.")
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
    result: AppBuildResult, *, chunk_size: int = MAX_CHUNK_BYTES
) -> AppBuildTransfer:
    """Create a bounded artifact transfer from a remote compiler result."""
    _validate_chunk_size(chunk_size)
    artifacts = _validated_files(
        result.artifacts,
        prefix=_ARTIFACT_PREFIX,
        max_members=MAX_RESULT_MEMBERS,
        max_expanded_bytes=MAX_RESULT_EXPANDED_BYTES,
        label="App build artifacts",
    )
    metadata = {
        "version": APP_BUILD_TRANSFER_VERSION,
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


def decode_app_build_result(transfer: ReceivedAppBuildTransfer) -> AppBuildResult:
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
        raise AppBuildTransferError("App build result transfer has unsupported members.")
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
) -> AppBuildTransfer:
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
        manifest = AppBuildTransferManifest(
            version=APP_BUILD_TRANSFER_VERSION,
            kind=kind,
            total_size=total_size,
            sha256=digest,
            chunk_size=chunk_size,
            chunk_count=(total_size + chunk_size - 1) // chunk_size,
        )
        manifest.validate()
        return AppBuildTransfer(manifest, path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _read_transfer(
    transfer: ReceivedAppBuildTransfer, *, expected_kind: TransferKind
) -> tuple[dict[str, object], dict[str, bytes]]:
    if transfer.manifest.kind != expected_kind:
        raise AppBuildTransferError("App build transfer kind does not match its decoder.")
    max_members = 2 + (MAX_SOURCE_MEMBERS if expected_kind == _INPUT_KIND else MAX_RESULT_MEMBERS)
    try:
        with zipfile.ZipFile(transfer.path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > max_members:
                raise AppBuildTransferError("App build transfer has too many archive members.")
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
                        raise AppBuildTransferError("App build transfer metadata exceeds the size limit.")
                elif info.filename == _SDK_MEMBER:
                    if expected_kind != _INPUT_KIND or info.file_size > MAX_SDK_BYTES:
                        raise AppBuildTransferError("App build transfer has an invalid SDK.")
                elif info.filename.startswith(_SOURCE_PREFIX):
                    if expected_kind != _INPUT_KIND:
                        raise AppBuildTransferError("App build transfer has unsupported members.")
                    _safe_relative_path(info.filename[len(_SOURCE_PREFIX):])
                    content_members += 1
                    expanded += info.file_size
                elif info.filename.startswith(_ARTIFACT_PREFIX):
                    if expected_kind != _RESULT_KIND:
                        raise AppBuildTransferError("App build transfer has unsupported members.")
                    _safe_relative_path(info.filename[len(_ARTIFACT_PREFIX):])
                    content_members += 1
                    expanded += info.file_size
                else:
                    raise AppBuildTransferError("App build transfer has unsupported members.")
                if content_members > (
                    MAX_SOURCE_MEMBERS if expected_kind == _INPUT_KIND else MAX_RESULT_MEMBERS
                ):
                    raise AppBuildTransferError("App build transfer has too many archive members.")
                if expanded > content_limit:
                    raise AppBuildTransferError("App build transfer exceeds the expanded byte limit.")
                files[info.filename] = archive.read(info)
    except AppBuildTransferError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise AppBuildTransferError("App build transfer is not a valid ZIP archive.") from exc
    raw_metadata = files.pop(_ARCHIVE_MANIFEST, None)
    if raw_metadata is None:
        raise AppBuildTransferError("App build transfer is missing metadata.")
    try:
        metadata = json.loads(raw_metadata)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppBuildTransferError("App build transfer metadata is invalid.") from exc
    if not isinstance(metadata, dict):
        raise AppBuildTransferError("App build transfer metadata is invalid.")
    if metadata.get("version") != APP_BUILD_TRANSFER_VERSION or metadata.get("kind") != expected_kind:
        raise AppBuildTransferError("App build transfer metadata version does not match.")
    return metadata, files


def _validate_archive_info(info: zipfile.ZipInfo, seen: set[str]) -> None:
    if info.is_dir() or info.flag_bits & 0x1 or stat.S_ISLNK(info.external_attr >> 16):
        raise AppBuildTransferError("App build transfer contains an unsafe archive member.")
    if info.filename in seen:
        raise AppBuildTransferError("App build transfer contains duplicate archive members.")
    seen.add(info.filename)
    if "\\" in info.filename:
        raise AppBuildTransferError("App build transfer contains an unsafe archive path.")
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
        raise AppBuildTransferError(f"{label} exceeds the {max_members} member limit.")
    validated: dict[str, bytes] = {}
    expanded = 0
    for raw_path, content in files.items():
        path = _safe_relative_path(raw_path)
        if path in validated:
            raise AppBuildTransferError(f"{label} contains duplicate path: {path}")
        if not isinstance(content, bytes):
            raise AppBuildTransferError(f"{label} contains non-bytes content.")
        expanded += len(content)
        if expanded > max_expanded_bytes:
            raise AppBuildTransferError(f"{label} exceeds the expanded byte limit.")
        validated[path] = content
    return dict(sorted(validated.items()))


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AppBuildTransferError("App build transfer contains an unsafe archive path.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or value.startswith("./"):
        raise AppBuildTransferError("App build transfer contains an unsafe archive path.")
    if value in {".", "/"} or not path.parts:
        raise AppBuildTransferError("App build transfer contains an unsafe archive path.")
    return value


def _validate_input_metadata(metadata: Mapping[str, object]) -> None:
    expected = {"version", "kind", "app_id", "dependencies", "recipe", "require_lockfile", "package_name"}
    if set(metadata) != expected or not isinstance(metadata.get("app_id"), str) or not metadata["app_id"]:
        raise AppBuildTransferError("App build input metadata is invalid.")
    _string_mapping(metadata.get("dependencies"), "dependencies")
    _json_object(metadata.get("recipe"), "recipe")
    if type(metadata.get("require_lockfile")) is not bool:
        raise AppBuildTransferError("App build input metadata is invalid.")
    if not isinstance(metadata.get("package_name"), str) or not metadata["package_name"]:
        raise AppBuildTransferError("App build input metadata is invalid.")


def _validate_result_metadata(metadata: Mapping[str, object]) -> None:
    expected = {"version", "kind", "build_identity"}
    if set(metadata) != expected or not isinstance(metadata.get("build_identity"), str) or not metadata["build_identity"]:
        raise AppBuildTransferError("App build result metadata is invalid.")


def _string_mapping(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise AppBuildTransferError(f"App build {label} must be a string mapping.")
    return dict(value)


def _json_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppBuildTransferError(f"App build {label} must be an object.")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AppBuildTransferError(f"App build {label} must be JSON serializable.") from exc
    return json.loads(encoded)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AppBuildTransferError("App build transfer metadata is not JSON serializable.") from exc


def _validate_chunk_size(chunk_size: int) -> None:
    if type(chunk_size) is not int or not 0 < chunk_size <= MAX_CHUNK_BYTES:
        raise AppBuildTransferError("Invalid App build transfer chunk size.")


def _transfer_limit(kind: TransferKind) -> int:
    return MAX_INPUT_TRANSFER_BYTES if kind == _INPUT_KIND else MAX_RESULT_TRANSFER_BYTES


def _file_size_and_digest(path: Path, *, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as archive:
        while data := archive.read(MAX_CHUNK_BYTES):
            total += len(data)
            if total > limit:
                raise AppBuildTransferError("App build transfer exceeds the size limit.")
            digest.update(data)
    return total, digest.hexdigest()
