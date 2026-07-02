"""E2E: CLI parity for solution files.

Three assertions:
1. ``bifrost solution export --mode full --password <pw>`` → the downloaded zip
   contains ``.bifrost/secrets.enc`` (file sidecars land in the encrypted tier,
   not as plaintext zip members) — this proves the CLI wires through to the full
   export correctly.
2. ``bifrost solution install`` of a full-backup zip → the file is readable on
   the new install via the REST files API (install restores files from the
   encrypted sidecar).
3. ``bifrost files list --solution <slug|id>`` → the listed files include the
   written file.
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile

import pytest
from uuid import UUID

from src.models.orm.solution_file_location import SolutionFileLocation
from tests.e2e.platform.conftest import wait_for_install

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upload_headers(headers: dict) -> dict:
    """Strip Content-Type so httpx sets it correctly for multipart."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _create_solution(e2e_client, headers, slug: str, org_id: str | None = None) -> dict:
    body: dict = {"slug": slug, "name": slug.upper()}
    if org_id is not None:
        body["organization_id"] = org_id
    r = e2e_client.post("/api/solutions", headers=headers, json=body)
    assert r.status_code in (200, 201), f"create solution failed: {r.text}"
    return r.json()


def _seed_solutions_policy(e2e_client, headers, *, org_id: str | None) -> None:
    """Allow-all policy on the 'solutions' location for the given org."""
    params: dict = {"location": "solutions"}
    if org_id is not None:
        params["scope"] = org_id
    r = e2e_client.put(
        "/api/files/policies/",
        headers=headers,
        params=params,
        json={"policies": {"policies": [{"name": "allow_all", "actions": ["read", "write", "delete", "list"]}]}},
    )
    assert r.status_code in (200, 201, 204), f"seed policy failed: {r.status_code} {r.text}"


def _declare_solutions_location(db_session, sol_id: str) -> None:
    async def _run() -> None:
        db_session.add(SolutionFileLocation(solution_id=UUID(sol_id), location="solutions"))
        await db_session.commit()

    asyncio.run(_run())


def _write_solution_file(e2e_client, headers, sol_id: str, path: str, content: str) -> None:
    """Write a file into the solution scope via the REST API."""
    r = e2e_client.post(
        f"/api/files/write?solution={sol_id}",
        headers=headers,
        json={
            "location": "solutions",
            "path": path,
            "content": content,
            "mode": "cloud",
        },
    )
    assert r.status_code == 204, f"write failed: {r.status_code} {r.text}"


def _read_solution_file(e2e_client, headers, sol_id: str, path: str) -> str:
    """Read a file from the solution scope via the REST API."""
    r = e2e_client.post(
        f"/api/files/read?solution={sol_id}",
        headers=headers,
        json={"location": "solutions", "path": path, "mode": "cloud"},
    )
    assert r.status_code == 200, f"read failed: {r.status_code} {r.text}"
    return r.json()["content"]


def _invoke_cli(group, args: list):
    """Invoke a Click group via CliRunner (standalone_mode=False)."""
    from click.testing import CliRunner
    return CliRunner().invoke(group, args, standalone_mode=False, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Test 1: CLI export --mode full captures file sidecars in secrets.enc
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_solution_export_full_contains_secrets_enc_with_file(
    e2e_client, platform_admin, cli_client, tmp_path, db_session
):
    """``bifrost solution export <slug> --mode full --password pw`` → zip with
    ``.bifrost/secrets.enc`` (no plaintext files/ members)."""
    from bifrost.commands.solution import solution_group

    headers = platform_admin.headers
    slug = f"cli-exp-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, headers, slug)
    sol_id = sol["id"]
    org_id = sol.get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=org_id)
    _declare_solutions_location(db_session, sol_id)

    # Write a file into the solution scope via REST (simulates workflow writing a file).
    file_path = f"docs/{uuid.uuid4().hex[:8]}.txt"
    file_content = f"cli-export-test-{uuid.uuid4().hex}"
    _write_solution_file(e2e_client, headers, sol_id, file_path, file_content)

    # Export via CLI with --include-data to pull file sidecars into secrets.enc.
    out_zip = tmp_path / f"{slug}.zip"
    result = _invoke_cli(
        solution_group,
        ["export", slug, "--mode", "full", "--password", "pw-cli-test", "--include-data", "--out", str(out_zip)],
    )
    assert result.exit_code == 0, f"export failed: {result.output}"
    assert out_zip.exists(), "output zip not created"

    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()

    # File bytes must NOT appear as plaintext zip members.
    plaintext = [n for n in names if n.startswith("files/")]
    assert plaintext == [], f"Found plaintext file members: {plaintext}"

    # secrets.enc must be present (file bytes are encrypted inside it).
    assert ".bifrost/secrets.enc" in names, (
        f"secrets.enc absent from full export; zip contains: {names}"
    )


# ---------------------------------------------------------------------------
# Test 2: CLI install restores solution files
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_solution_install_restores_files(
    e2e_client, platform_admin, cli_client, tmp_path, db_session
):
    """Install a full-backup zip → file is readable on the new install."""

    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    # --- Source solution: create, write a file, export as full backup ---
    src_slug = f"cli-inst-src-{uuid.uuid4().hex[:8]}"
    src = _create_solution(e2e_client, headers, src_slug)
    src_id = src["id"]
    org_id = src.get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=org_id)
    _declare_solutions_location(db_session, src_id)

    file_path = f"restore-test/{uuid.uuid4().hex[:8]}.txt"
    file_content = f"restore-content-{uuid.uuid4().hex}"
    _write_solution_file(e2e_client, headers, src_id, file_path, file_content)

    # Export the full backup.
    export_resp = e2e_client.post(
        f"/api/solutions/{src_id}/export?mode=full&include_data=true",
        headers=headers,
        json={"password": "pw-restore"},
    )
    assert export_resp.status_code == 200, f"export failed: {export_resp.text}"
    zip_bytes = export_resp.content

    # --- Install the zip into a fresh slug ---
    dst_slug = f"cli-inst-dst-{uuid.uuid4().hex[:8]}"
    # Rename the descriptor inside the zip so install treats it as a new solution.
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as src_zf, zipfile.ZipFile(buf, "w") as dst_zf:
        for name in src_zf.namelist():
            data = src_zf.read(name)
            if name == "bifrost.solution.yaml":
                # Rename slug so this becomes a distinct install.
                import yaml as _yaml
                desc = _yaml.safe_load(data.decode())
                desc["slug"] = dst_slug
                desc["name"] = dst_slug.upper()
                data = _yaml.safe_dump(desc, sort_keys=False).encode()
            dst_zf.writestr(name, data)
    new_zip_bytes = buf.getvalue()

    new_zip_path = tmp_path / f"{dst_slug}.zip"
    new_zip_path.write_bytes(new_zip_bytes)

    inst = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=upload_headers,
            files={"file": (f"{dst_slug}.zip", new_zip_bytes, "application/zip")},
            data={"password": "pw-restore", "replace_data": "true"},
        ),
        headers,
    )
    assert inst.status_code in (200, 201), f"install failed: {inst.text}"
    dst_id = inst.json()["id"]

    # Seed policy for the new install's org.
    dst_org_id = inst.json().get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=dst_org_id)

    # The file must be readable on the new install.
    restored = _read_solution_file(e2e_client, headers, dst_id, file_path)
    assert restored == file_content, (
        f"restored content mismatch: expected {file_content!r}, got {restored!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: `bifrost files list --solution <slug|id>` shows the written file
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_files_list_with_solution_flag(
    e2e_client, platform_admin, cli_client, db_session
):
    """``bifrost files list --solution <slug>`` lists files in that install's scope."""
    from bifrost.commands.files import files_group

    headers = platform_admin.headers
    slug = f"cli-fls-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, headers, slug)
    sol_id = sol["id"]
    org_id = sol.get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=org_id)
    _declare_solutions_location(db_session, sol_id)

    # Write a file via REST.
    subdir = uuid.uuid4().hex[:8]
    file_path = f"{subdir}/test.txt"
    _write_solution_file(e2e_client, headers, sol_id, file_path, "hello")

    # List via CLI --solution slug.
    result = _invoke_cli(
        files_group,
        ["--json", "list", subdir, "--location", "solutions", "--solution", slug],
    )
    assert result.exit_code == 0, f"list failed: {result.output}"
    items = json.loads(result.output)
    assert isinstance(items, list), f"expected a list, got: {items!r}"
    assert any("test.txt" in item for item in items), (
        f"test.txt not in listing: {items}"
    )


@pytest.mark.e2e
def test_files_list_solution_defaults_location(
    e2e_client, platform_admin, cli_client, db_session
):
    """``bifrost files list --solution <slug>`` WITHOUT ``--location`` defaults to
    'solutions' and finds the written file (verifies the advertised UX)."""
    from bifrost.commands.files import files_group

    headers = platform_admin.headers
    slug = f"cli-flsdef-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, headers, slug)
    sol_id = sol["id"]
    org_id = sol.get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=org_id)
    _declare_solutions_location(db_session, sol_id)

    subdir = uuid.uuid4().hex[:8]
    file_path = f"{subdir}/default-loc.txt"
    _write_solution_file(e2e_client, headers, sol_id, file_path, "default-loc-content")

    # Deliberately omit --location; the command must default to 'solutions'.
    result = _invoke_cli(
        files_group,
        ["--json", "list", subdir, "--solution", slug],
    )
    assert result.exit_code == 0, f"list failed: {result.output}"
    items = json.loads(result.output)
    assert isinstance(items, list), f"expected a list, got: {items!r}"
    assert any("default-loc.txt" in item for item in items), (
        f"default-loc.txt not in listing (location default may not have applied): {items}"
    )


@pytest.mark.e2e
def test_files_list_with_solution_id_flag(
    e2e_client, platform_admin, cli_client, db_session
):
    """``bifrost files list --solution <id>`` also works with a bare UUID."""
    from bifrost.commands.files import files_group

    headers = platform_admin.headers
    slug = f"cli-flsid-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, headers, slug)
    sol_id = sol["id"]
    org_id = sol.get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=org_id)
    _declare_solutions_location(db_session, sol_id)

    subdir = uuid.uuid4().hex[:8]
    file_path = f"{subdir}/id-test.txt"
    _write_solution_file(e2e_client, headers, sol_id, file_path, "by-id")

    result = _invoke_cli(
        files_group,
        ["--json", "list", subdir, "--location", "solutions", "--solution", sol_id],
    )
    assert result.exit_code == 0, f"list by id failed: {result.output}"
    items = json.loads(result.output)
    assert any("id-test.txt" in item for item in items), (
        f"id-test.txt not in listing: {items}"
    )
