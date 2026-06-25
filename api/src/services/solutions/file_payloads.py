"""Chunk-encrypted payload members for full Solution backups."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from zipfile import ZipFile, ZipInfo

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
    with zf.open(info, "w", force_zip64=True) as out:
        out.write(json.dumps(header, separators=(",", ":")).encode() + b"\n")
        for offset in range(0, len(content), 8 * 1024 * 1024):
            out.write(fernet.encrypt(content[offset : offset + 8 * 1024 * 1024]) + b"\n")


async def iter_encrypted_payload_file(
    path: Path,
    *,
    password: str,
) -> AsyncIterator[bytes]:
    """Yield decrypted chunks from a payload file created by export."""
    import base64

    with path.open("rb") as f:
        first = f.readline()
        if not first:
            raise ValueError(f"empty solution file payload: {path}")
        header = json.loads(first.decode())
        if header.get("format") != _PAYLOAD_FORMAT:
            raise ValueError(f"unsupported solution file payload format: {path}")
        key = _derive_fernet_key(
            password,
            base64.urlsafe_b64decode(header["salt"]),
            n=int(header["n"]),
            r=int(header["r"]),
            p=int(header["p"]),
        )
        fernet = Fernet(key)
        while line := f.readline():
            token = line.strip()
            if token:
                yield fernet.decrypt(token)
