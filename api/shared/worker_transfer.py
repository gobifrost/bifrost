"""Bounded, file-backed byte transfer framing for worker jobs.

Transfers spool payloads to temporary files and exchange a small manifest
followed by ordered base64 chunks.  The producer and receiver each own their
temporary files and must close or cancel them when the hand-off ends.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path


FILE_TRANSFER_VERSION = 1
MAX_CHUNK_BYTES = 1024 * 1024


class FileTransferError(ValueError):
    """A file transfer is malformed, incomplete, or over its budget."""


def validate_file_transfer_chunk_size(chunk_size: int) -> None:
    if type(chunk_size) is not int or not 0 < chunk_size <= MAX_CHUNK_BYTES:
        raise FileTransferError("Invalid file transfer chunk size.")


@dataclass(frozen=True)
class FileTransferManifest:
    """Small JSON-safe declaration for one bounded file transfer."""

    version: int
    kind: str
    total_size: int
    sha256: str
    chunk_size: int
    chunk_count: int
    max_total_size: int

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
    def from_dict(
        cls, value: Mapping[str, object], *, max_total_size: int
    ) -> "FileTransferManifest":
        try:
            manifest = cls(
                version=value["version"],  # type: ignore[arg-type]
                kind=value["kind"],  # type: ignore[arg-type]
                total_size=value["total_size"],  # type: ignore[arg-type]
                sha256=value["sha256"],  # type: ignore[arg-type]
                chunk_size=value["chunk_size"],  # type: ignore[arg-type]
                chunk_count=value["chunk_count"],  # type: ignore[arg-type]
                max_total_size=max_total_size,
            )
        except (KeyError, TypeError) as exc:
            raise FileTransferError("Invalid file transfer manifest.") from exc
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.version != FILE_TRANSFER_VERSION:
            raise FileTransferError("Unsupported file transfer version.")
        if not isinstance(self.kind, str) or not self.kind:
            raise FileTransferError("Invalid file transfer kind.")
        if type(self.max_total_size) is not int or self.max_total_size <= 0:
            raise FileTransferError("Invalid file transfer size limit.")
        if type(self.total_size) is not int or not 0 < self.total_size <= self.max_total_size:
            raise FileTransferError("Invalid file transfer size.")
        validate_file_transfer_chunk_size(self.chunk_size)
        expected_chunks = (self.total_size + self.chunk_size - 1) // self.chunk_size
        if type(self.chunk_count) is not int or self.chunk_count != expected_chunks:
            raise FileTransferError("Invalid file transfer chunk count.")
        if not isinstance(self.sha256, str) or len(self.sha256) != 64:
            raise FileTransferError("Invalid file transfer digest.")
        try:
            int(self.sha256, 16)
        except ValueError as exc:
            raise FileTransferError("Invalid file transfer digest.") from exc


@dataclass(frozen=True)
class FileTransferChunk:
    """One ordered JSON-safe base64 file-transfer message."""

    index: int
    data_b64: str

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "data_b64": self.data_b64}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FileTransferChunk":
        try:
            chunk = cls(index=value["index"], data_b64=value["data_b64"])  # type: ignore[arg-type]
        except (KeyError, TypeError) as exc:
            raise FileTransferError("Invalid file transfer chunk.") from exc
        if type(chunk.index) is not int or chunk.index < 0 or not isinstance(chunk.data_b64, str):
            raise FileTransferError("Invalid file transfer chunk.")
        return chunk

    def decode(self) -> bytes:
        if len(self.data_b64) > 4 * ((MAX_CHUNK_BYTES + 2) // 3):
            raise FileTransferError("File transfer chunk exceeds the size limit.")
        try:
            data = base64.b64decode(self.data_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FileTransferError("Invalid file transfer chunk encoding.") from exc
        if len(data) > MAX_CHUNK_BYTES:
            raise FileTransferError("File transfer chunk exceeds the size limit.")
        return data


@dataclass
class FileTransfer:
    """A locally produced transfer whose payload is held in a temporary file."""

    manifest: FileTransferManifest
    path: Path

    def iter_chunks(self) -> Iterator[FileTransferChunk]:
        """Yield bounded chunks without loading the payload into memory."""
        with self.path.open("rb") as payload:
            for index in range(self.manifest.chunk_count):
                data = payload.read(self.manifest.chunk_size)
                if not data:
                    raise FileTransferError("File transfer payload is incomplete.")
                yield FileTransferChunk(index, base64.b64encode(data).decode("ascii"))
            if payload.read(1):
                raise FileTransferError("File transfer payload exceeds its declared size.")

    def close(self) -> None:
        self.path.unlink(missing_ok=True)

    def cancel(self) -> None:
        """Discard a transfer after cancellation or a failed hand-off."""
        self.close()

    def __enter__(self) -> "FileTransfer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass
class ReceivedFileTransfer:
    """A verified received payload; the caller owns cleanup after decoding."""

    manifest: FileTransferManifest
    path: Path

    def close(self) -> None:
        self.path.unlink(missing_ok=True)

    def cancel(self) -> None:
        self.close()

    def __enter__(self) -> "ReceivedFileTransfer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FileTransferReceiver:
    """Spool strictly ordered incoming chunks into one temporary payload."""

    def __init__(self, manifest: FileTransferManifest, *, temp_dir: Path | None = None):
        manifest.validate()
        self.manifest = manifest
        fd, name = tempfile.mkstemp(prefix="bifrost-worker-transfer-", dir=temp_dir)
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

    def add_chunk(self, chunk: FileTransferChunk | Mapping[str, object]) -> None:
        """Write exactly the next chunk; duplicates and reordering fail closed."""
        if self._finished:
            raise FileTransferError("File transfer has already finished.")
        if not isinstance(chunk, FileTransferChunk):
            chunk = FileTransferChunk.from_dict(chunk)
        if chunk.index != self._next_index:
            raise FileTransferError("File transfer chunks must be ordered without duplicates.")
        if chunk.index >= self.manifest.chunk_count:
            raise FileTransferError("File transfer has too many chunks.")
        data = chunk.decode()
        expected_size = self.manifest.chunk_size
        if chunk.index == self.manifest.chunk_count - 1:
            expected_size = self.manifest.total_size - self._size
        if len(data) != expected_size:
            raise FileTransferError("File transfer chunk has an unexpected size.")
        self._file.write(data)
        self._digest.update(data)
        self._size += len(data)
        self._next_index += 1

    def finish(self) -> ReceivedFileTransfer:
        """Verify the declared byte count and digest, then transfer file ownership."""
        if self._finished:
            raise FileTransferError("File transfer has already finished.")
        try:
            if self._next_index != self.manifest.chunk_count or self._size != self.manifest.total_size:
                raise FileTransferError("File transfer is incomplete.")
            if self._digest.hexdigest() != self.manifest.sha256:
                raise FileTransferError("File transfer digest does not match.")
            self._file.close()
            self._finished = True
            self._owns_path = False
            return ReceivedFileTransfer(self.manifest, self._path)
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
        """Discard a partial transfer when its worker job is cancelled."""
        self.close()

    def __enter__(self) -> "FileTransferReceiver":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
