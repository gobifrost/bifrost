"""Chunk-encrypted payload members for full Solution backups."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from cryptography.fernet import Fernet

from src.services.solutions.secrets_blob import (
    _SCRYPT_N,
    _SCRYPT_P,
    _SCRYPT_R,
    _derive_fernet_key,
)

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_PAYLOAD_FORMAT = "bifrost.solution-file-payload.v1"


async def write_encrypted_payload_member(
    zf: ZipFile,
    member: str,
    chunks: AsyncIterator[bytes],
    *,
    password: str,
) -> None:
    """Write encrypted chunks as one ZIP member.

    Each line after the JSON header is a Fernet token for one plaintext chunk.
    That keeps memory bounded to one chunk plus encryption overhead.
    """
    import base64
    import os

    salt = os.urandom(16)
    key = _derive_fernet_key(password, salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    fernet = Fernet(key)
    header = {
        "format": _PAYLOAD_FORMAT,
        "kdf": "scrypt",
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "salt": base64.urlsafe_b64encode(salt).decode(),
    }

    info = ZipInfo(member, date_time=_ZIP_EPOCH)
    # An explicit ZipInfo defaults to stored entries, even when its ZipFile
    # uses compression. Recover Base64 overhead while encrypting in chunks.
    info.compress_type = ZIP_DEFLATED
    with zf.open(info, "w", force_zip64=True) as out:
        out.write(json.dumps(header, separators=(",", ":")).encode() + b"\n")
        async for chunk in chunks:
            if chunk:
                out.write(fernet.encrypt(chunk) + b"\n")


def write_encrypted_payload_member_from_bytes(
    zf: ZipFile,
    member: str,
    content: bytes,
    *,
    password: str,
) -> None:
    """Small compatibility helper for tests and in-memory bundle fixtures."""
    import base64
    import os

    salt = os.urandom(16)
    key = _derive_fernet_key(password, salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    fernet = Fernet(key)
    header = {
        "format": _PAYLOAD_FORMAT,
        "kdf": "scrypt",
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "salt": base64.urlsafe_b64encode(salt).decode(),
    }

    info = ZipInfo(member, date_time=_ZIP_EPOCH)
    info.compress_type = ZIP_DEFLATED
    with zf.open(info, "w", force_zip64=True) as out:
        out.write(json.dumps(header, separators=(",", ":")).encode() + b"\n")
        for offset in range(0, len(content), 8 * 1024 * 1024):
            out.write(
                fernet.encrypt(content[offset : offset + 8 * 1024 * 1024]) + b"\n"
            )


_MAX_HEADER_BYTES = 4096
_MAX_TOKEN_BYTES = 16 * 1024 * 1024
_MAX_KDF_MEMORY_BYTES = 64 * 1024 * 1024


async def iter_encrypted_payload_stream(
    stream: BinaryIO,
    *,
    password: str,
) -> AsyncIterator[bytes]:
    """Decrypt an archive member with bounded header, token and KDF allocations."""
    import base64

    first = stream.readline(_MAX_HEADER_BYTES + 1)
    if not first or len(first) > _MAX_HEADER_BYTES:
        raise ValueError("invalid solution file payload header")
    header = json.loads(first.decode())
    if not isinstance(header, dict) or header.get("format") != _PAYLOAD_FORMAT:
        raise ValueError("unsupported solution file payload format")
    if header.get("kdf") != "scrypt":
        raise ValueError("unsupported solution file payload KDF")
    n, r, p = int(header["n"]), int(header["r"]), int(header["p"])
    if (
        n < 2
        or n & (n - 1)
        or r < 1
        or not 1 <= p <= 4
        or 128 * n * r > _MAX_KDF_MEMORY_BYTES
    ):
        raise ValueError("unsafe solution file payload KDF parameters")
    salt = base64.urlsafe_b64decode(header["salt"])
    if len(salt) != 16:
        raise ValueError("invalid solution file payload KDF salt")
    key = _derive_fernet_key(password, salt, n=n, r=r, p=p)
    fernet = Fernet(key)
    while line := stream.readline(_MAX_TOKEN_BYTES + 1):
        if len(line) > _MAX_TOKEN_BYTES:
            raise ValueError("solution file payload token exceeds chunk limit")
        token = line.strip()
        if token:
            yield fernet.decrypt(token)


async def iter_encrypted_payload_file(
    path: Path,
    *,
    password: str,
) -> AsyncIterator[bytes]:
    """Yield bounded decrypted chunks from an extracted payload file."""
    with path.open("rb") as stream:
        async for chunk in iter_encrypted_payload_stream(stream, password=password):
            yield chunk
