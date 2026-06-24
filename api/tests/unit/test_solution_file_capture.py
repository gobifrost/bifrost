"""Task 6 (Task-19): bundle_for populates solution_files for full exports (include_data=True).

TDD — these tests are written BEFORE the implementation and use mocks so they
run in the unit test environment without a live S3/SeaweedFS.
"""
from __future__ import annotations

import base64
import hashlib
import io
import uuid
import zipfile
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


async def test_bundle_includes_solution_files_when_requested(db_session) -> None:
    """include_data=True must populate bundle.solution_files with entries
    carrying correct sha256 and non-empty content_bytes."""
    from src.services.solutions.capture import SolutionCaptureService

    sol = _make_solution()
    db_session.add(sol)
    await db_session.flush()

    file_a = (b"file alpha content", "docs/alpha.txt")
    file_b = (b"file beta content", "docs/beta.txt")
    files_data = {p: c for c, p in [file_a, file_b]}

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

    async def _mock_read(db, install_id, location, path):
        return files_data[path]

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
        svc = SolutionCaptureService(db_session)
        bundle = await svc.bundle_for(sol, include_data=True)

    assert len(bundle.solution_files) == 2
    paths = {e.path for e in bundle.solution_files}
    assert paths == {"docs/alpha.txt", "docs/beta.txt"}

    for entry in bundle.solution_files:
        expected_bytes = b"file alpha content" if "alpha" in entry.path else b"file beta content"
        assert entry.content_bytes == expected_bytes
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
        bundle = await SolutionCaptureService(db_session).bundle_for(sol, include_data=True)

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

    async def _mock_read(db, install_id, location, path):
        i = int(path.replace("docs/f", "").replace(".txt", ""))
        return f"content {i}".encode()

    with (
        patch(
            "src.services.solution_files.enumerate_solution_files",
            side_effect=_mock_enumerate,
        ),
        patch(
            "src.services.solution_files.read_solution_file",
            side_effect=_mock_read,
        ),
        caplog.at_level(logging.WARNING, logger="src.services.solutions.capture"),
    ):
        bundle = await SolutionCaptureService(db_session).bundle_for(sol, include_data=True)

    assert len(bundle.solution_files) == FILE_CAP
    assert any("file" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# M7 assertion: bytes go into ENCRYPTED tier, not plaintext zip members
# ---------------------------------------------------------------------------


async def test_export_zip_files_in_encrypted_tier_not_plaintext(db_session) -> None:
    """M7 property: the bytes land in the encrypted .bifrost/secrets.enc,
    NOT as plaintext files/ members in the zip.

    Calls build_workspace_zip directly with a bundle that carries
    solution_files, then:
    1. Asserts the zip does NOT contain any 'files/' member.
    2. Asserts .bifrost/secrets.enc is present.
    3. Decrypts it and confirms the file bytes are present.
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
        ),
        SolutionFileEntry(
            location="shared",
            path="docs/beta.txt",
            sha256=hashlib.sha256(content_b).hexdigest(),
            size=len(content_b),
        ),
    ]
    files_data = {"docs/alpha.txt": content_a, "docs/beta.txt": content_b}

    async def _mock_enumerate(db, install_id):
        return meta_entries

    async def _mock_read(db, install_id, location, path):
        return files_data[path]

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
        bundle = await SolutionCaptureService(db_session).bundle_for(sol, include_data=True)

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
    assert base64.b64decode(file_map["docs/alpha.txt"]["content_b64"]) == content_a
    assert base64.b64decode(file_map["docs/beta.txt"]["content_b64"]) == content_b


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
        bundle = await SolutionCaptureService(db_session).bundle_for(sol, include_data=True)

    zip_bytes = build_workspace_zip(bundle)  # no password
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    assert ".bifrost/secrets.enc" not in zf.namelist()
