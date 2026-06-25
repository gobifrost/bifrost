"""Task 6 (Task-19): bundle_for populates solution_files for backups (include_files=True).

TDD — these tests are written BEFORE the implementation and use mocks so they
run in the unit test environment without a live S3/SeaweedFS.
"""
from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch


from src.models.orm.solutions import Solution
from src.services.solution_files import SolutionFileEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_solution() -> Solution:
    return Solution(
        id=uuid.uuid4(),
        slug=f"test-filedata-{uuid.uuid4().hex[:8]}",
        name="FileData Test",
        organization_id=None,
    )


def _make_entries(paths: list[tuple[str, bytes]]) -> list[SolutionFileEntry]:
    """Build SolutionFileEntry objects (without content_bytes — as enumerate returns)."""
    return [
        SolutionFileEntry(
            location="shared",
            path=path,
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
        )
        for path, content in paths
    ]


# ---------------------------------------------------------------------------
# Unit tests (mock enumerate_solution_files + read_solution_file)
# ---------------------------------------------------------------------------


async def _read_payload(zf: zipfile.ZipFile, tmp_path: Path, payload: str, password: str) -> bytes:
    from src.services.solutions.file_payloads import iter_encrypted_payload_file

    payload_path = Path(zf.extract(payload, tmp_path))
    chunks: list[bytes] = []
    async for chunk in iter_encrypted_payload_file(payload_path, password=password):
        chunks.append(chunk)
    return b"".join(chunks)


async def test_bundle_includes_solution_file_metadata_when_requested(db_session) -> None:
    """include_files=True must populate bundle.solution_files with entries
    carrying metadata only; payload bytes are streamed later by export."""
    from src.services.solutions.capture import SolutionCaptureService

    sol = _make_solution()
    db_session.add(sol)
    await db_session.flush()

    meta_entries = [
        SolutionFileEntry(
            location="shared",
            path="docs/alpha.txt",
            sha256=hashlib.sha256(b"file alpha content").hexdigest(),
            size=len(b"file alpha content"),
        ),
        SolutionFileEntry(
            location="shared",
            path="docs/beta.txt",
            sha256=hashlib.sha256(b"file beta content").hexdigest(),
            size=len(b"file beta content"),
        ),
    ]

    async def _mock_enumerate(db, install_id):
        return meta_entries

    with (
        patch(
            "src.services.solution_files.enumerate_solution_files",
            side_effect=_mock_enumerate,
        ),
        patch(
            "src.services.solution_files.read_solution_file",
            side_effect=AssertionError("capture must not read solution file bytes"),
        ),
    ):
        svc = SolutionCaptureService(db_session)
        bundle = await svc.bundle_for(sol, include_files=True)

    assert len(bundle.solution_files) == 2
    paths = {e.path for e in bundle.solution_files}
    assert paths == {"docs/alpha.txt", "docs/beta.txt"}

    for entry in bundle.solution_files:
        expected_bytes = b"file alpha content" if "alpha" in entry.path else b"file beta content"
        assert entry.content_bytes is None
        assert entry.sha256 == hashlib.sha256(expected_bytes).hexdigest()
        assert entry.size == len(expected_bytes)
        assert entry.location == "shared"


async def test_bundle_excludes_solution_files_by_default(db_session) -> None:
    """include_data=False (default) must leave solution_files empty."""
    from src.services.solutions.capture import SolutionCaptureService

    sol = _make_solution()
    db_session.add(sol)
    await db_session.flush()

    bundle = await SolutionCaptureService(db_session).bundle_for(sol)
    assert bundle.solution_files == []


async def test_bundle_table_data_does_not_imply_solution_files(db_session) -> None:
    """include_data controls table rows only; file payloads have their own option."""
    from src.services.solutions.capture import SolutionCaptureService

    sol = _make_solution()
    db_session.add(sol)
    await db_session.flush()

    async def _mock_enumerate(db, install_id):
        return _make_entries([("docs/alpha.txt", b"file alpha content")])

    with patch(
        "src.services.solution_files.enumerate_solution_files",
        side_effect=_mock_enumerate,
    ):
        bundle = await SolutionCaptureService(db_session).bundle_for(
            sol,
            include_data=True,
            include_files=False,
        )

    assert bundle.solution_files == []


async def test_bundle_solution_files_empty_when_no_files(db_session) -> None:
    """Solutions with no files must have an empty solution_files list."""
    from src.services.solutions.capture import SolutionCaptureService

    sol = _make_solution()
    db_session.add(sol)
    await db_session.flush()

    async def _empty_enumerate(db, install_id):
        return []

    with patch(
        "src.services.solution_files.enumerate_solution_files",
        side_effect=_empty_enumerate,
    ):
        bundle = await SolutionCaptureService(db_session).bundle_for(sol, include_files=True)

    assert bundle.solution_files == []


async def test_bundle_file_cap_logs_warning_and_truncates(db_session, caplog) -> None:
    """When a solution has more than FILE_CAP files, a WARNING is logged and
    only FILE_CAP entries are returned."""
    import logging
    from src.services.solutions.capture import FILE_CAP, SolutionCaptureService

    sol = _make_solution()
    db_session.add(sol)
    await db_session.flush()

    # Build FILE_CAP + 1 meta entries.
    over_cap = [
        SolutionFileEntry(
            location="shared",
            path=f"docs/f{i:04d}.txt",
            sha256=hashlib.sha256(f"content {i}".encode()).hexdigest(),
            size=len(f"content {i}"),
        )
        for i in range(FILE_CAP + 1)
    ]

    async def _mock_enumerate(db, install_id):
        return over_cap

    with (
        patch(
            "src.services.solution_files.enumerate_solution_files",
            side_effect=_mock_enumerate,
        ),
        patch(
            "src.services.solution_files.read_solution_file",
            side_effect=AssertionError("capture must not read solution file bytes"),
        ),
        caplog.at_level(logging.WARNING, logger="src.services.solutions.capture"),
    ):
        bundle = await SolutionCaptureService(db_session).bundle_for(sol, include_files=True)

    assert len(bundle.solution_files) == FILE_CAP
    assert any("file" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# M7 assertion: bytes go into ENCRYPTED tier, not plaintext zip members
# ---------------------------------------------------------------------------


async def test_export_zip_files_in_encrypted_payload_members_not_plaintext(
    db_session, tmp_path
) -> None:
    """M7 property: bytes land in encrypted payload members, not plaintext.

    Calls build_workspace_zip directly with a bundle that carries
    solution_files, then:
    1. Asserts the zip does NOT contain any 'files/' member.
    2. Asserts .bifrost/secrets.enc is present.
    3. Decrypts payload members and confirms the file bytes are present.
    """
    from src.services.solutions.export import build_workspace_zip
    from src.services.solutions.secrets_blob import decode_secrets_blob

    content_a = b"secret file alpha"
    content_b = b"secret file beta"

    sol = _make_solution()
    db_session.add(sol)
    await db_session.flush()

    meta_entries = [
        SolutionFileEntry(
            location="shared",
            path="docs/alpha.txt",
            sha256=hashlib.sha256(content_a).hexdigest(),
            size=len(content_a),
            content_bytes=content_a,
        ),
        SolutionFileEntry(
            location="shared",
            path="docs/beta.txt",
            sha256=hashlib.sha256(content_b).hexdigest(),
            size=len(content_b),
            content_bytes=content_b,
        ),
    ]

    async def _mock_enumerate(db, install_id):
        return meta_entries

    from src.services.solutions.capture import SolutionCaptureService

    with patch(
        "src.services.solution_files.enumerate_solution_files",
        side_effect=_mock_enumerate,
    ):
        bundle = await SolutionCaptureService(db_session).bundle_for(sol, include_files=True)
    for entry, content_bytes in zip(bundle.solution_files, (content_a, content_b), strict=True):
        entry.content_bytes = content_bytes

    assert len(bundle.solution_files) == 2

    password = "test-password-m7"
    zip_bytes = build_workspace_zip(bundle, password=password)

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = zf.namelist()

    # M7: no plaintext files/ members.
    plaintext_file_members = [n for n in names if n.startswith("files/")]
    assert plaintext_file_members == [], (
        f"Found plaintext files/ members in zip: {plaintext_file_members}. "
        "File bytes must travel in the encrypted secrets.enc tier only."
    )

    # M7: secrets.enc present.
    assert ".bifrost/secrets.enc" in names, "Expected .bifrost/secrets.enc in zip"

    # M7: decrypt and confirm file bytes.
    blob = zf.read(".bifrost/secrets.enc").decode()
    content = decode_secrets_blob(blob, password=password)

    assert len(content.solution_files) == 2
    file_map = {f["path"]: f for f in content.solution_files}
    assert "docs/alpha.txt" in file_map
    assert "docs/beta.txt" in file_map
    assert "content_b64" not in file_map["docs/alpha.txt"]
    assert await _read_payload(
        zf, tmp_path, file_map["docs/alpha.txt"]["payload"], password
    ) == content_a
    assert await _read_payload(
        zf, tmp_path, file_map["docs/beta.txt"]["payload"], password
    ) == content_b


async def test_export_no_secrets_enc_without_password(db_session) -> None:
    """Shareable export (no password) must NOT include secrets.enc
    even when solution_files are present."""
    from src.services.solutions.export import build_workspace_zip

    sol = _make_solution()
    db_session.add(sol)
    await db_session.flush()

    meta_entries = [
        SolutionFileEntry(
            location="shared",
            path="docs/foo.txt",
            sha256=hashlib.sha256(b"foo content").hexdigest(),
            size=len(b"foo content"),
        ),
    ]

    async def _mock_enumerate(db, install_id):
        return meta_entries

    async def _mock_read(db, install_id, location, path):
        return b"foo content"

    from src.services.solutions.capture import SolutionCaptureService

    with (
        patch(
            "src.services.solution_files.enumerate_solution_files",
            side_effect=_mock_enumerate,
        ),
        patch(
            "src.services.solution_files.read_solution_file",
            side_effect=_mock_read,
        ),
    ):
        bundle = await SolutionCaptureService(db_session).bundle_for(sol, include_files=True)

    zip_bytes = build_workspace_zip(bundle)  # no password
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    assert ".bifrost/secrets.enc" not in zf.namelist()


async def test_import_payload_streams_bounded_chunks(
    db_session, tmp_path, monkeypatch
) -> None:
    from src.services.solutions.deploy import SolutionBundle
    from src.services.solutions.export import build_workspace_zip
    from src.services.solutions.secrets_blob import decode_secrets_blob
    from src.services.solutions.zip_install import _apply_solution_files

    content = (b"a" * (8 * 1024 * 1024)) + b"tail"
    sol = _make_solution()
    sol.id = uuid.uuid4()
    password = "stream-import-pw"
    bundle = SolutionBundle(
        solution=sol,
        solution_files=[
            SolutionFileEntry(
                location="shared",
                path="docs/large.bin",
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                content_bytes=content,
            )
        ],
    )
    zip_bytes = build_workspace_zip(bundle, password=password)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(tmp_path)
        encrypted = zf.read(".bifrost/secrets.enc").decode()

    sidecar = decode_secrets_blob(encrypted, password=password)
    assert "content_b64" not in sidecar.solution_files[0]
    assert sidecar.solution_files[0]["payload"].startswith(".bifrost/file-payloads/")

    seen_chunks: list[int] = []
    restored = bytearray()

    async def _fake_write(db, install_id, location, path, chunks, *, mode):  # noqa: ANN001
        assert install_id == sol.id
        assert location == "shared"
        assert path == "docs/large.bin"
        assert mode == "replace"
        async for chunk in chunks:
            seen_chunks.append(len(chunk))
            restored.extend(chunk)
        return True

    monkeypatch.setattr(
        "src.services.solution_files.write_solution_file_from_chunks",
        _fake_write,
    )

    await _apply_solution_files(
        db_session,
        solution=sol,
        solution_files=sidecar.solution_files,
        workspace=tmp_path,
        password=password,
    )

    assert bytes(restored) == content
    assert seen_chunks
    assert max(seen_chunks) <= 8 * 1024 * 1024
