"""E2E: POST /api/solutions/{id}/export?mode=full — file sidecars in encrypted tier.

M7 verification: solution files travel inside .bifrost/secrets.enc (encrypted),
never as plaintext zip members.
"""
from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def make_solution_with_files(e2e_client, platform_admin, db_session):
    """Factory: create a Solution and write N files to it via the service.

    Returns a coroutine that accepts ``file_contents`` (dict path→bytes) and
    returns a SimpleNamespace with ``.id``.
    """
    from types import SimpleNamespace
    from src.services.solution_files import write_solution_file

    async def _make(file_contents: dict[str, bytes]) -> SimpleNamespace:
        headers = platform_admin.headers
        slug = f"export-files-{uuid.uuid4().hex[:8]}"
        r = e2e_client.post(
            "/api/solutions",
            headers=headers,
            json={"slug": slug, "name": slug.upper(), "scope": "org"},
        )
        assert r.status_code in (200, 201), r.text
        sol = r.json()
        sol_id = uuid.UUID(sol["id"])

        for path, content in file_contents.items():
            await write_solution_file(db_session, sol_id, "shared", path, content)
        await db_session.commit()

        return SimpleNamespace(id=str(sol_id))

    return _make


async def _read_payload(zf: zipfile.ZipFile, tmp_path: Path, payload: str, password: str) -> bytes:
    from src.services.solutions.file_payloads import iter_encrypted_payload_file

    payload_path = Path(zf.extract(payload, tmp_path))
    chunks: list[bytes] = []
    async for chunk in iter_encrypted_payload_file(payload_path, password=password):
        chunks.append(chunk)
    return b"".join(chunks)


async def test_full_export_files_in_encrypted_tier(
    e2e_client, platform_admin, make_solution_with_files, tmp_path
):
    """M7: include_files must put file bytes in encrypted payload members."""
    content_a = b"secret file alpha e2e"
    content_b = b"secret file beta e2e"
    sol = await make_solution_with_files({
        "docs/alpha.txt": content_a,
        "docs/beta.txt": content_b,
    })
    headers = platform_admin.headers

    ok = e2e_client.post(
        f"/api/solutions/{sol.id}/export?mode=full&include_files=true",
        json={"password": "pw-e2e"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text

    zf = zipfile.ZipFile(io.BytesIO(ok.content))
    names = zf.namelist()

    # M7: no plaintext files/ members.
    plaintext = [n for n in names if n.startswith("files/")]
    assert plaintext == [], f"Found plaintext file members: {plaintext}"

    # M7: secrets.enc present.
    assert ".bifrost/secrets.enc" in names

    # M7: decrypt and verify.
    from src.services.solutions.secrets_blob import decode_secrets_blob

    blob = zf.read(".bifrost/secrets.enc").decode()
    content = decode_secrets_blob(blob, password="pw-e2e")

    assert len(content.solution_files) == 2
    file_map = {f["path"]: f for f in content.solution_files}
    assert "content_b64" not in file_map["docs/alpha.txt"]
    assert await _read_payload(
        zf, tmp_path, file_map["docs/alpha.txt"]["payload"], "pw-e2e"
    ) == content_a
    assert await _read_payload(
        zf, tmp_path, file_map["docs/beta.txt"]["payload"], "pw-e2e"
    ) == content_b


async def test_full_export_no_files_omits_file_section(
    e2e_client, platform_admin, make_solution_with_files
):
    """A solution with no files must not put a solution_files key in secrets.enc."""
    sol = await make_solution_with_files({})  # no files
    headers = platform_admin.headers

    ok = e2e_client.post(
        f"/api/solutions/{sol.id}/export?mode=full",
        json={"password": "pw-empty"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text

    zf = zipfile.ZipFile(io.BytesIO(ok.content))
    # With no config values, table data, OR solution files, secrets.enc must be absent.
    assert ".bifrost/secrets.enc" not in zf.namelist()


async def test_shareable_export_never_includes_file_bytes(
    e2e_client, platform_admin, make_solution_with_files
):
    """Shareable export (no password) must NEVER expose file bytes."""
    sol = await make_solution_with_files({"docs/secret.txt": b"sensitive content"})
    headers = platform_admin.headers

    sh = e2e_client.post(
        f"/api/solutions/{sol.id}/export",
        json={},
        headers=headers,
    )
    assert sh.status_code == 200, sh.text

    zf = zipfile.ZipFile(io.BytesIO(sh.content))
    names = zf.namelist()

    # Neither plaintext nor encrypted tier.
    assert ".bifrost/secrets.enc" not in names
    assert not any(n.startswith("files/") for n in names)
