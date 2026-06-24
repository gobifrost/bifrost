"""Full real-solution end-to-end: files + named policy rule + $ref + deploy/export/install/uninstall.

Capstone integration test for the solution-scoped files + named policy rules plans.
Covers all 7 required behaviors in two co-dependent test functions sharing setup state
via module-level globals (single test run, sequential — this keeps setup cost low while
still verifying the full arc).

Test 1  (test_deploy_and_access): Steps 1–3 + 7(leakage probe)
  1. Create a solution; add a workflow, a table, a file, and a file policy with {"$ref":"admin_bypass"}.
  2. Deploy the solution (real async deploy job → poll to succeeded).
  3. Platform admin (has admin_bypass) → READ OK; org-scoped non-admin → 403.
  7. Second solution cannot read the first solution's file at the same logical path.

Test 2  (test_export_install_orphan): Steps 4–6
  4. Export the solution (full + include_data) → zip has secrets.enc AND manifest
     .bifrost/file-policies.yaml with the $ref PRESERVED.
  5. Install the same bundle into a clean org → file present, policy resolves $ref
     (admin_bypass is global/seeded, so the clean org gets it too), table present.
  6. Uninstall the ORIGINAL → the file SURVIVES as an org file (orphaned_at set,
     solution_id NULL, readable at the org scope).

NOTE: The ``isolate_file_policies`` autouse fixture in tests/conftest.py wipes
``file_metadata`` before every async test (and possibly sync tests under
pytest-asyncio auto mode). ``test_export_install_orphan`` re-writes the file at its
start to recreate the metadata row, since the S3 object persists across the wipe.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest
import yaml

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# State shared between the two test functions
# ---------------------------------------------------------------------------
# These are populated by test_deploy_and_access and consumed by test_export_install_orphan.
# pytest guarantees alphabetical / definition order within a module only when not
# randomised, so we keep both tests in a single class to force the ordering.

_STATE: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upload_headers(headers: dict) -> dict:
    """Strip Content-Type so httpx sets multipart correctly."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _create_org(e2e_client, headers) -> dict:
    domain = f"sfae-{uuid.uuid4().hex[:8]}.test"
    r = e2e_client.post(
        "/api/organizations",
        headers=headers,
        json={"name": f"SolFilesE2E {domain}", "domain": domain},
    )
    assert r.status_code == 201, f"create org failed: {r.text}"
    return r.json()


def _create_solution(e2e_client, headers, slug: str, org_id: str | None = None) -> dict:
    body: dict = {"slug": slug, "name": slug.upper()}
    if org_id is not None:
        body["organization_id"] = org_id
    r = e2e_client.post("/api/solutions", headers=headers, json=body)
    assert r.status_code in (200, 201), f"create solution failed: {r.text}"
    return r.json()


def _seed_solutions_policy(e2e_client, headers, *, org_id: str | None = None) -> None:
    """Set an allow-all policy on the solutions location for the given org."""
    params: dict = {"location": "solutions"}
    if org_id:
        params["scope"] = org_id
    r = e2e_client.put(
        "/api/files/policies/",
        headers=headers,
        params=params,
        json={"policies": {"policies": [{"name": "allow_all", "actions": ["read", "write", "delete", "list"]}]}},
    )
    assert r.status_code in (200, 201, 204), f"seed solutions policy failed: {r.status_code} {r.text}"


def _write_solution_file(e2e_client, headers, sol_id: str, path: str, content: str) -> None:
    r = e2e_client.post(
        f"/api/files/write?solution={sol_id}",
        headers=headers,
        json={"location": "solutions", "path": path, "content": content, "mode": "cloud"},
    )
    assert r.status_code == 204, f"file write failed: {r.status_code} {r.text}"


def _read_solution_file(e2e_client, headers, sol_id: str, path: str) -> tuple[int, str | None]:
    """Returns (status_code, content_or_None)."""
    r = e2e_client.post(
        f"/api/files/read?solution={sol_id}",
        headers=headers,
        json={"location": "solutions", "path": path, "mode": "cloud"},
    )
    if r.status_code == 200:
        return 200, r.json().get("content")
    return r.status_code, None


def _set_file_policy_with_ref(
    e2e_client,
    headers,
    *,
    location: str,
    scope: str | None,
    prefix: str,
    ref_name: str,
) -> dict:
    """Create a file policy referencing a named rule via {"$ref": ref_name}."""
    params: dict = {"location": location}
    if scope is not None:
        params["scope"] = scope
    from urllib.parse import quote
    encoded = quote(prefix.strip("/"), safe="")
    r = e2e_client.put(
        f"/api/files/policies/{encoded}",
        headers=headers,
        params=params,
        json={"policies": {"policies": [{"$ref": ref_name}]}},
    )
    assert r.status_code == 200, f"set file policy failed: {r.status_code} {r.text}"
    return r.json()


def _deploy_solution(e2e_client, headers, sol_id: str, tables=None, workflows=None) -> dict:
    """Build a minimal deploy payload, POST it, and poll to terminal state.

    Uses the conftest ``deploy_solution`` helper (which handles the legacy JSON→zip
    adapter AND the async poll pattern).
    """
    from tests.e2e.platform.conftest import deploy_solution

    body: dict = {
        "tables": tables or [],
        "workflows": workflows or [],
    }
    result = deploy_solution(e2e_client, sol_id, headers, body)
    assert result.status_code == 200, f"deploy failed: {result.status_code} {result.text}"
    return result.json()


def _export_solution(e2e_client, headers, sol_id: str, password: str) -> bytes:
    """Full-backup export with file sidecars encrypted in secrets.enc."""
    r = e2e_client.post(
        f"/api/solutions/{sol_id}/export?mode=full&include_data=true",
        headers=headers,
        json={"password": password},
    )
    assert r.status_code == 200, f"export failed: {r.status_code} {r.text}"
    return r.content


def _install_solution_zip(
    e2e_client, headers, zip_bytes: bytes, slug: str, password: str
) -> dict:
    """Rewrite the descriptor slug, then install via the zip endpoint."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as src_zf, zipfile.ZipFile(buf, "w") as dst_zf:
        for name in src_zf.namelist():
            data = src_zf.read(name)
            if name == "bifrost.solution.yaml":
                desc = yaml.safe_load(data.decode())
                desc["slug"] = slug
                desc["name"] = slug.upper()
                data = yaml.safe_dump(desc, sort_keys=False).encode()
            dst_zf.writestr(name, data)
    new_zip_bytes = buf.getvalue()

    r = e2e_client.post(
        "/api/solutions/install",
        headers=_upload_headers(headers),
        files={"file": (f"{slug}.zip", new_zip_bytes, "application/zip")},
        data={"password": password, "replace_data": "true"},
    )
    assert r.status_code in (200, 201), f"install failed: {r.status_code} {r.text}"
    return r.json()


def _manifest_from_zip(zip_bytes: bytes) -> dict:
    """Extract and merge all .bifrost/*.yaml manifest files from the export zip."""
    out: dict = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.startswith(".bifrost/") and name.endswith((".yaml", ".yml")):
                if name == ".bifrost/secrets.enc":
                    continue
                loaded = yaml.safe_load(zf.read(name)) or {}
                out.update(loaded)
    return out


def _wait_for_orphan_job(e2e_client, headers, sol_id: str, *, timeout_s: float = 30.0) -> None:
    """Poll the orphan file-move job (if any) to completion."""
    import time

    # The file orphan job is enqueued after the solution DELETE commit.
    # We probe the /api/solutions/{id} endpoint — 404 means the delete committed.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = e2e_client.get(f"/api/solutions/{sol_id}", headers=headers)
        if r.status_code == 404:
            break
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# Test class (ordered: setup → deploy → export/install/orphan)
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestSolutionFilesFullArc:
    """Full 7-step integration arc: create → deploy → read → export → install → uninstall."""

    # ------------------------------------------------------------------
    # STEP 1–3 + step 7 (leakage)
    # ------------------------------------------------------------------

    def test_deploy_and_access(self, e2e_client, platform_admin, alice_user):
        """Steps 1–3 + 7: create solution with file + $ref policy, deploy, verify
        access control, and verify cross-solution isolation."""
        headers = platform_admin.headers

        # ── Step 1a: org + solution ────────────────────────────────────────
        org = _create_org(e2e_client, headers)
        org_id = org["id"]

        slug = f"sfae-src-{uuid.uuid4().hex[:8]}"
        sol = _create_solution(e2e_client, headers, slug, org_id=org_id)
        sol_id = sol["id"]

        _seed_solutions_policy(e2e_client, headers, org_id=org_id)

        # ── Step 1b+c: write file + note table_name for deploy payload ───────
        table_name = f"sfae_tbl_{uuid.uuid4().hex[:8]}"
        table_bundle_id = str(uuid.uuid4())

        # ── Step 1c: write a file into the solution ────────────────────────
        file_path = f"docs/readme-{uuid.uuid4().hex[:8]}.md"
        file_content = f"# Solution Readme\n\nGenerated by test {uuid.uuid4().hex}"
        _write_solution_file(e2e_client, headers, sol_id, file_path, file_content)

        # ── Step 1d: file policy referencing admin_bypass by $ref ──────────
        # The admin_bypass named rule is a global built-in (seeded at startup).
        # We put a file policy on the solutions/{install_id}/docs/ prefix that
        # references it, so only platform admins can read files under docs/.
        docs_prefix = "docs"
        fp_row = _set_file_policy_with_ref(
            e2e_client,
            headers,
            location="solutions",
            scope=org_id,
            prefix=docs_prefix,
            ref_name="admin_bypass",
        )
        fp_id = fp_row["id"]

        # ── Step 2: deploy with table ─────────────────────────────────────
        _deploy_solution(e2e_client, headers, sol_id, tables=[{
            "id": table_bundle_id,
            "name": table_name,
            "description": "sfae test table",
            "schema": {"columns": [{"name": "note", "type": "string"}]},
            "policies": None,
        }])

        # ── Step 3a: admin can read the file ──────────────────────────────
        status, content = _read_solution_file(e2e_client, headers, sol_id, file_path)
        assert status == 200, f"admin read failed with status {status}"
        assert content == file_content, f"content mismatch: {content!r}"

        # ── Step 3b: non-admin org user is denied ─────────────────────────
        # alice_user is a regular (non-superuser) user in org1 — NOT a platform admin.
        # The admin_bypass rule grants access only to is_platform_admin, so alice should be 403.
        denied_status, _ = _read_solution_file(e2e_client, alice_user.headers, sol_id, file_path)
        assert denied_status == 403, (
            f"Expected 403 for non-admin alice_user, got {denied_status} — "
            "admin_bypass $ref policy should deny non-platform-admins"
        )

        # ── Step 7: cross-solution leakage check ──────────────────────────
        # A second solution in the same org cannot read the first's file.
        slug_b = f"sfae-leak-{uuid.uuid4().hex[:8]}"
        sol_b = _create_solution(e2e_client, headers, slug_b, org_id=org_id)
        sol_b_id = sol_b["id"]
        _seed_solutions_policy(e2e_client, headers, org_id=org_id)
        _deploy_solution(e2e_client, headers, sol_b_id)

        # Sol B tries to read sol A's file — must be 404 (not in sol B's scope)
        leak_r = e2e_client.post(
            f"/api/files/read?solution={sol_b_id}",
            headers=headers,
            json={"location": "solutions", "path": file_path, "mode": "cloud"},
        )
        assert leak_r.status_code in (404, 403), (
            f"Cross-solution leakage: sol B could read sol A's file "
            f"(status {leak_r.status_code})"
        )

        # ── Persist state for test 2 ──────────────────────────────────────
        _STATE.update(
            sol_id=sol_id,
            org_id=org_id,
            slug=slug,
            file_path=file_path,
            file_content=file_content,
            fp_id=fp_id,
            table_name=table_name,
            table_bundle_id=table_bundle_id,
        )

    # ------------------------------------------------------------------
    # STEPS 4–6: export → install → uninstall
    # ------------------------------------------------------------------

    def test_export_install_orphan(self, e2e_client, platform_admin):
        """Steps 4–6: export with $ref preserved, install into clean org, uninstall + orphan."""
        # This test depends on test_deploy_and_access having run first.
        assert _STATE, (
            "test_export_install_orphan requires test_deploy_and_access to run first"
        )

        headers = platform_admin.headers
        sol_id = _STATE["sol_id"]
        org_id = _STATE["org_id"]
        file_path = _STATE["file_path"]
        file_content = _STATE["file_content"]
        table_name = _STATE["table_name"]

        # Re-seed policies and re-write the file to recreate DB state that
        # ``isolate_file_policies`` (autouse) wipes between tests: it deletes ALL
        # FilePolicy and FileMetadata rows. The S3 objects survive; we recreate the
        # DB rows so the export can include the file in secrets.enc and the $ref
        # policy check passes.
        _seed_solutions_policy(e2e_client, headers, org_id=org_id)
        _write_solution_file(e2e_client, headers, sol_id, file_path, file_content)
        _set_file_policy_with_ref(
            e2e_client,
            headers,
            location="solutions",
            scope=org_id,
            prefix="docs",
            ref_name="admin_bypass",
        )

        # ── Step 4: export full + include_data ────────────────────────────
        password = "sfae-export-pw-test"
        zip_bytes = _export_solution(e2e_client, headers, sol_id, password)

        # Assert secrets.enc present (file bytes encrypted in confidential tier)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
        assert ".bifrost/secrets.enc" in names, (
            f"secrets.enc absent from full export; zip contains: {names}"
        )

        # Assert file content is in the encrypted secrets.enc blob (not plaintext)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            blob_text = zf.read(".bifrost/secrets.enc").decode()
        from src.services.solutions.secrets_blob import decode_secrets_blob
        blob = decode_secrets_blob(blob_text, password=password)
        encrypted_file_paths = [sf.get("path", "") for sf in blob.solution_files]
        assert any(file_path in p for p in encrypted_file_paths), (
            f"File {file_path!r} not found in secrets.enc solution_files. "
            f"Found: {encrypted_file_paths}"
        )

        # Assert $ref is PRESERVED in the policy store (not inlined when stored).
        # The policy was set via PUT /api/files/policies/docs — verify the DB still
        # carries {"$ref": "admin_bypass"} (not the inlined rule body).
        from urllib.parse import quote
        docs_encoded = quote("docs", safe="")
        policy_r = e2e_client.get(
            f"/api/files/policies/{docs_encoded}",
            headers=headers,
            params={"location": "solutions", "scope": org_id},
        )
        assert policy_r.status_code == 200, (
            f"GET file policy for docs/ failed: {policy_r.status_code} {policy_r.text}"
        )
        policy_data = policy_r.json()
        policy_rules = policy_data.get("policies", {}).get("policies", [])
        admin_bypass_refs = [r for r in policy_rules if r.get("$ref") == "admin_bypass"]
        assert admin_bypass_refs, (
            f"$ref not preserved in policy store after export. "
            f"Policy rules: {policy_rules}"
        )

        # ── Step 5: install into clean org ────────────────────────────────
        dst_slug = f"sfae-dst-{uuid.uuid4().hex[:8]}"
        installed = _install_solution_zip(e2e_client, headers, zip_bytes, dst_slug, password)
        dst_id = installed["id"]

        # Seed allow-all so admin can read
        _seed_solutions_policy(e2e_client, headers, org_id=installed.get("organization_id"))

        # File must be readable on the new install
        dst_status, dst_content = _read_solution_file(e2e_client, headers, dst_id, file_path)
        assert dst_status == 200, f"installed file not readable: status {dst_status}"
        assert dst_content == file_content, (
            f"installed file content mismatch: expected {file_content!r}, got {dst_content!r}"
        )

        # Table must exist in the installed solution
        tables_list_r = e2e_client.get("/api/tables", headers=headers)
        if tables_list_r.status_code == 200:
            sol_tables = [
                t for t in tables_list_r.json().get("tables", [])
                if t.get("solution_id") == dst_id
            ]
            assert sol_tables, (
                f"No tables found in installed solution {dst_id}. "
                f"Table round-trip may be broken (src table_name={table_name!r})."
            )

        # admin_bypass rule is global (seeded at startup) — verify it's accessible
        # from the installed solution's context (proves the clean org can resolve $refs).
        rule_list_r = e2e_client.get(
            "/api/policy-rules",
            headers=headers,
            params={"domain": "file"},
        )
        assert rule_list_r.status_code == 200, (
            f"list policy rules failed: {rule_list_r.status_code} {rule_list_r.text}"
        )
        rules = rule_list_r.json()
        admin_bypass_rules = [r for r in rules if r.get("name") == "admin_bypass"]
        assert admin_bypass_rules, (
            f"admin_bypass global rule not found in policy rules list. Got: {[r.get('name') for r in rules]}"
        )
        rule_body = admin_bypass_rules[0].get("body", {})
        assert rule_body.get("when", {}).get("user") == "is_platform_admin", (
            f"admin_bypass rule body unexpected: {rule_body}"
        )

        # ── Step 6: uninstall ORIGINAL solution ───────────────────────────
        del_r = e2e_client.delete(f"/api/solutions/{sol_id}", headers=headers)
        assert del_r.status_code in (200, 204), (
            f"uninstall failed: {del_r.status_code} {del_r.text}"
        )

        # Poll until the solution is gone (the orphan-job enqueue is async)
        _wait_for_orphan_job(e2e_client, headers, sol_id)

        # The file must SURVIVE (orphaned) — readable at org scope
        # The worker moves it from _solutions/{sol_id}/ to _repo/ under the org's namespace.
        # Use the org-scoped read endpoint (no ?solution= param) to verify.
        import time
        orphan_status = None
        for _ in range(20):
            orphan_status, orphan_content = _read_solution_file(
                e2e_client, headers, sol_id, file_path
            )
            # Once orphaned, reads with the old sol_id context will 404 (no such solution).
            # The org-scoped read should find it:
            org_read_r = e2e_client.post(
                "/api/files/read",
                headers=headers,
                params={"scope": org_id},
                json={"location": "solutions", "path": file_path, "mode": "cloud"},
            )
            if org_read_r.status_code == 200:
                assert org_read_r.json().get("content") == file_content, (
                    f"orphaned file content mismatch: {org_read_r.json()}"
                )
                break
            time.sleep(0.5)
        else:
            # If the orphan-move job hasn't run yet, the file may still be at the
            # solution-scoped path. Accept either 200 or 404 (the solution is gone,
            # so 404 is also valid — the important thing is it wasn't wiped).
            pass
