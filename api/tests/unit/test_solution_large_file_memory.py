from __future__ import annotations

import hashlib
import io
import uuid
import zipfile

from src.models.orm.solutions import Solution
from src.services.solution_files import SolutionFileEntry
from src.services.solutions.deploy import SolutionBundle
from src.services.solutions.export import build_workspace_zip_for_export
from src.services.solutions.secrets_blob import decode_secrets_blob


class _FakeZip:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def open(self, info, mode, *, force_zip64=False):  # noqa: ANN001
        self.calls.append(
            {
                "filename": info.filename,
                "mode": mode,
                "force_zip64": force_zip64,
            }
        )
        return io.BytesIO()


async def test_encrypted_solution_file_payload_members_force_zip64() -> None:
    from src.services.solutions.file_payloads import (
        write_encrypted_payload_member,
        write_encrypted_payload_member_from_bytes,
    )

    async def _empty_chunks():
        if False:
            yield b""

    streaming_zip = _FakeZip()
    await write_encrypted_payload_member(
        streaming_zip,
        ".bifrost/file-payloads/large.bin.enc",
        _empty_chunks(),
        password="zip64",
    )

    bytes_zip = _FakeZip()
    write_encrypted_payload_member_from_bytes(
        bytes_zip,
        ".bifrost/file-payloads/small.bin.enc",
        b"content",
        password="zip64",
    )

    assert streaming_zip.calls == [
        {
            "filename": ".bifrost/file-payloads/large.bin.enc",
            "mode": "w",
            "force_zip64": True,
        }
    ]
    assert bytes_zip.calls == [
        {
            "filename": ".bifrost/file-payloads/small.bin.enc",
            "mode": "w",
            "force_zip64": True,
        }
    ]


async def test_solution_file_export_uses_bounded_payload_chunks(
    db_session, tmp_path, monkeypatch
) -> None:
    chunk = b"x" * (8 * 1024 * 1024)
    chunks = 16
    expected_size = len(chunk) * chunks
    digest = hashlib.sha256()
    for _ in range(chunks):
        digest.update(chunk)
    expected_sha256 = digest.hexdigest()
    seen_chunk_sizes: list[int] = []

    def _fake_iter_raw_s3_chunks(self, path, *, chunk_size=8 * 1024 * 1024):  # noqa: ANN001
        assert path == "reports/source/large.bin"

        async def _gen():
            for _ in range(chunks):
                seen_chunk_sizes.append(len(chunk))
                yield chunk

        return _gen()

    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService.iter_raw_s3_chunks",
        _fake_iter_raw_s3_chunks,
    )

    solution = Solution(
        id=uuid.uuid4(),
        slug=f"large-export-{uuid.uuid4().hex[:8]}",
        name="Large Export",
        organization_id=None,
    )
    bundle = SolutionBundle(
        solution=solution,
        solution_files=[
            SolutionFileEntry(
                location="reports",
                path="large.bin",
                sha256=expected_sha256,
                size=expected_size,
                s3_key="reports/source/large.bin",
            )
        ],
    )
    out = tmp_path / "large-export.zip"

    await build_workspace_zip_for_export(
        bundle,
        db_session,
        out,
        password="large-payload",
    )

    assert seen_chunk_sizes == [len(chunk)] * chunks
    assert max(seen_chunk_sizes) == 8 * 1024 * 1024

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert ".bifrost/secrets.enc" in names
        blob = zf.read(".bifrost/secrets.enc").decode()

    content = decode_secrets_blob(blob, password="large-payload")
    assert len(content.solution_files) == 1
    entry = content.solution_files[0]
    assert entry["sha256"] == expected_sha256
    assert entry["size"] == expected_size
    assert "content_b64" not in entry
    assert entry["payload"] in names
