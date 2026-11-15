"""Bounded JSON payloads on the shared file-backed compute transport."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from shared.worker_transfer import FILE_TRANSFER_VERSION, MAX_CHUNK_BYTES, FileTransfer, FileTransferManifest, ReceivedFileTransfer

def write_json_transfer(value: dict, kind: str, limit: int) -> FileTransfer:
    body = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    if len(body) > limit:
        raise ValueError("JSON compute transfer exceeds its byte limit")
    fd, name = tempfile.mkstemp(prefix="bifrost-json-compute-")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
        return FileTransfer(FileTransferManifest(FILE_TRANSFER_VERSION, kind, len(body),
            hashlib.sha256(body).hexdigest(), MAX_CHUNK_BYTES,
            (len(body) + MAX_CHUNK_BYTES - 1) // MAX_CHUNK_BYTES, limit), path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def read_json_transfer(transfer: ReceivedFileTransfer, kind: str, limit: int) -> dict:
    manifest = transfer.manifest
    if manifest.kind != kind or manifest.total_size > limit or transfer.path.stat().st_size != manifest.total_size:
        raise ValueError("JSON compute transfer kind or size is invalid")
    body = transfer.path.read_bytes()
    if hashlib.sha256(body).hexdigest() != manifest.sha256:
        raise ValueError("JSON compute transfer digest is invalid")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("JSON compute transfer must be an object")
    return value

