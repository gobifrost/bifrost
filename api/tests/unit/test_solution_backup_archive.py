"""Large encrypted backups preserve source limits and avoid payload extraction."""

from __future__ import annotations

import hashlib
import io
import random
import struct
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.solutions import file_payloads, zip_install
from src.services.solutions.secrets_blob import SolutionContent, encode_secrets_blob

MEMBER = ".bifrost/file-payloads/test.bin.enc"
PASSWORD = "synthetic-backup-password"
MIB = 1024 * 1024


@pytest.mark.parametrize("from_path", [True, False])
async def test_corrupt_payload_is_rejected_before_install_writes(
    tmp_path, monkeypatch, from_path
):
    archive = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        write_metadata(zf)
        zf.writestr(MEMBER, b"payload with an intact ZIP checksum")
    with zipfile.ZipFile(archive) as zf:
        offset = zf.getinfo(MEMBER).header_offset
    encoded = bytearray(archive.read_bytes())
    name_size, extra_size = struct.unpack_from("<HH", encoded, offset + 26)
    encoded[offset + 30 + name_size + extra_size] ^= 1
    archive.write_bytes(encoded)
    install = AsyncMock()
    monkeypatch.setattr(zip_install, "_install_workspace", install)

    # Preview reads source/metadata only; payload integrity belongs to the job.
    assert zip_install.preview_zip_path(archive).requires_password is True
    with pytest.raises(zipfile.BadZipFile, match="CRC"):
        if from_path:
            await zip_install.install_zip_path(
                None,
                archive,
                organization_id=None,
                config_values={},
                deployer_email="test",
                password=PASSWORD,
            )
        else:
            await zip_install.install_zip(
                None,
                bytes(encoded),
                organization_id=None,
                config_values={},
                deployer_email="test",
                password=PASSWORD,
            )
    install.assert_not_awaited()


def write_metadata(zf, *, payload=MEMBER, size=1, digest=None):
    zf.writestr("bifrost.solution.yaml", "slug: backup-test\nname: Backup Test\n")
    zf.writestr(
        ".bifrost/secrets.enc",
        encode_secrets_blob(
            SolutionContent(
                solution_files=[
                    {
                        "location": "shared",
                        "path": "test.bin",
                        "payload": payload,
                        "size": size,
                        "sha256": digest,
                    }
                ]
            ),
            password=PASSWORD,
        ),
    )


def test_larger_compressed_budget_applies_only_to_encrypted_backups(tmp_path, monkeypatch):
    source = tmp_path / "source.zip"
    backup = tmp_path / "backup.zip"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("bifrost.solution.yaml", "slug: source-test\nname: Source Test\n")
        zf.writestr("README.md", "documentation\n" * 100)
    with zipfile.ZipFile(backup, "w") as zf:
        write_metadata(zf)
    monkeypatch.setattr(zip_install, "MAX_SOLUTION_ARCHIVE_BYTES", 128)
    monkeypatch.setattr(zip_install, "MAX_SOLUTION_BACKUP_ARCHIVE_BYTES", 8 * MIB)

    with pytest.raises(ValueError, match="compressed upload limit"):
        zip_install.preview_zip_path(source)
    assert zip_install.preview_zip_path(backup).requires_password is True


@pytest.mark.slow
async def test_preview_accepts_largest_docs_payload_without_extracting_encrypted_copy(
    tmp_path, monkeypatch
):
    # Models the actual largest migrated file, exceeding the original entry cap.
    size = 268_313_994
    block = random.Random(77).randbytes(8 * MIB)
    digest = hashlib.sha256()
    archive = tmp_path / "backup.zip"

    async def chunks():
        remaining = size
        while remaining:
            chunk = block[: min(remaining, len(block))]
            digest.update(chunk)
            remaining -= len(chunk)
            yield chunk

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        await file_payloads.write_encrypted_payload_member(
            zf, MEMBER, chunks(), password=PASSWORD
        )
        write_metadata(zf, size=size, digest=digest.hexdigest())

    original_parse = zip_install._parse_workspace

    def parse_source_only(workspace):
        assert not (workspace / ".bifrost/file-payloads").exists(), (
            "encrypted payload copy must stay in the ZIP"
        )
        return original_parse(workspace)

    monkeypatch.setattr(zip_install, "_parse_workspace", parse_source_only)
    preview = zip_install.preview_zip_path(archive)
    assert preview.slug == "backup-test"
    assert preview.requires_password is True
    assert (
        zip_install.validate_install_zip(archive, password=PASSWORD).slug
        == "backup-test"
    )


async def test_file_restore_reads_and_validates_member_without_disk_sidecar(
    tmp_path, monkeypatch
):
    from src.services import solution_files

    content = random.Random(21).randbytes(256 * 1024)
    digest = hashlib.sha256(content).hexdigest()
    archive = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        file_payloads.write_encrypted_payload_member_from_bytes(
            zf, MEMBER, content, password=PASSWORD
        )
        write_metadata(zf, size=len(content), digest=digest)
    received = []

    async def write_chunks(db, solution_id, location, path, chunks, *, mode):
        assert (location, path, mode) == ("shared", "test.bin", "replace")
        async for chunk in chunks:
            received.append(chunk)

    monkeypatch.setattr(solution_files, "write_solution_file_from_chunks", write_chunks)
    workspace = tmp_path / "source"
    workspace.mkdir()
    with zipfile.ZipFile(archive) as zf:
        await zip_install._apply_solution_files(
            None,
            solution=SimpleNamespace(id=uuid4()),
            workspace=workspace,
            password=PASSWORD,
            payload_archive=zf,
            solution_files=[
                {
                    "location": "shared",
                    "path": "test.bin",
                    "payload": MEMBER,
                    "size": len(content),
                    "sha256": digest,
                }
            ],
        )
    assert b"".join(received) == content
    assert list(workspace.iterdir()) == []


async def test_archive_payload_reader_uses_bounded_reads_and_authenticates_chunks(
    tmp_path,
):
    content = b"authenticated chunk" * 100
    with zipfile.ZipFile(tmp_path / "payload.zip", "w") as zf:
        file_payloads.write_encrypted_payload_member_from_bytes(
            zf, MEMBER, content, password=PASSWORD
        )
    with zipfile.ZipFile(tmp_path / "payload.zip") as zf:
        encoded = zf.read(MEMBER)

    class BoundedReader(io.BytesIO):
        def readline(self, size=-1):
            assert 0 < size <= 16 * MIB + 1, (
                "payload input must not request an unbounded line"
            )
            return super().readline(size)

    chunks = [
        chunk
        async for chunk in file_payloads.iter_encrypted_payload_stream(
            BoundedReader(encoded), password=PASSWORD
        )
    ]
    assert b"".join(chunks) == content
    from cryptography.fernet import InvalidToken

    corrupted = bytearray(encoded)
    corrupted[-15] ^= 1
    with pytest.raises(InvalidToken):
        _ = [
            chunk
            async for chunk in file_payloads.iter_encrypted_payload_stream(
                BoundedReader(corrupted), password=PASSWORD
            )
        ]


async def test_payload_reader_refuses_oversized_header_before_parsing():
    with pytest.raises(ValueError, match="header"):
        _ = [
            chunk
            async for chunk in file_payloads.iter_encrypted_payload_stream(
                io.BytesIO(b"x" * 5000), password=PASSWORD
            )
        ]


@pytest.mark.parametrize(
    "member", ["../outside", ".bifrost/file-payloads/../../../outside"]
)
def test_ignored_payload_paths_still_receive_traversal_validation(tmp_path, member):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        write_metadata(zf)
        zf.writestr(member, b"unsafe")
    with pytest.raises(ValueError, match="unsafe path"):
        zip_install.preview_zip_path(archive)
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize(
    "member,size",
    [
        ("src/large.py", 129 * MIB),
        (".bifrost/secrets.enc", 129 * MIB),
    ],
)
def test_source_and_metadata_entry_limits_remain_enforced(member, size):
    infos = [SimpleNamespace(filename=member, file_size=size, compress_size=size)]
    archive = SimpleNamespace(
        infolist=lambda: infos, namelist=lambda: [i.filename for i in infos]
    )
    with pytest.raises(ValueError, match="entry is too large"):
        zip_install._validate_zip_members(archive)


def test_backup_payload_totals_can_exceed_source_workspace_budget():
    infos = [
        SimpleNamespace(
            filename=".bifrost/secrets.enc", file_size=100, compress_size=100
        )
    ]
    infos += [
        SimpleNamespace(
            filename=f".bifrost/file-payloads/{i}.bin.enc",
            file_size=512 * MIB,
            compress_size=400 * MIB,
        )
        for i in range(24)
    ]
    archive = SimpleNamespace(
        infolist=lambda: infos, namelist=lambda: [i.filename for i in infos]
    )
    zip_install._validate_zip_members(archive)


async def test_payload_header_cannot_request_unbounded_scrypt_memory(monkeypatch):
    import base64
    import json

    def no_derivation(*args, **kwargs):
        raise AssertionError("unsafe KDF header must fail before allocation")

    monkeypatch.setattr(file_payloads, "_derive_fernet_key", no_derivation)
    header = {
        "format": "bifrost.solution-file-payload.v1",
        "kdf": "scrypt",
        "salt": base64.urlsafe_b64encode(b"x" * 16).decode(),
        "n": 2**30,
        "r": 8,
        "p": 1,
    }
    with pytest.raises(ValueError, match="KDF"):
        _ = [
            chunk
            async for chunk in file_payloads.iter_encrypted_payload_stream(
                io.BytesIO(json.dumps(header).encode() + b"\n"), password=PASSWORD
            )
        ]
