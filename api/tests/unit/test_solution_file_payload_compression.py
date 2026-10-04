"""Encrypted backup payloads should not retain Base64 storage overhead."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from src.services.solutions.file_payloads import (
    iter_encrypted_payload_file,
    write_encrypted_payload_member,
    write_encrypted_payload_member_from_bytes,
)


@pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "bytes"])
async def test_encrypted_payload_compresses_encoding_overhead_and_round_trips(
    tmp_path: Path, streaming: bool,
) -> None:
    # High-entropy content models existing compressed files. The ZIP can still
    # recover Base64's overhead after encryption, without relying on plaintext
    # repetition or changing the encrypted payload format.
    content = random.Random(71).randbytes(512 * 1024)
    archive = tmp_path / "backup.zip"
    member = ".bifrost/file-payloads/test.bin.enc"
    password = "synthetic-backup-password"

    async def chunks():
        for offset in range(0, len(content), 128 * 1024):
            yield content[offset:offset + 128 * 1024]

    with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
        if streaming:
            await write_encrypted_payload_member(zf, member, chunks(), password=password)
        else:
            write_encrypted_payload_member_from_bytes(zf, member, content, password=password)

    with ZipFile(archive) as zf:
        info = zf.getinfo(member)
        assert info.compress_size < len(content) * 1.1
        payload = Path(zf.extract(member, tmp_path / "restore"))

    digest = hashlib.sha256()
    size = 0
    async for chunk in iter_encrypted_payload_file(payload, password=password):
        digest.update(chunk)
        size += len(chunk)
    assert size == len(content)
    assert digest.hexdigest() == hashlib.sha256(content).hexdigest()
