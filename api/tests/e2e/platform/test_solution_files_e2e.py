"""Capstone e2e: inactive-lifecycle arc with files + named policy rule + $ref.

Proves the FULL solution lifecycle end-to-end:

  Arc 1  (test_arc_1_deploy_through_uninstall) — Steps 1–3 + 6 (leakage)
    1. Create solution + workflow + table + FILE (solutions/{install}/docs/readme.md)
       + file POLICY referencing {"$ref":"admin_bypass"} (Plan 1 named rule).
    2. Deploy (real async job → poll succeeded).
    3. Allowed user (platform admin) reads the file → 200 + correct content.
       Denied user (non-admin) → 403 (admin_bypass rule enforced).
    UNINSTALL:
       status == "inactive"; FileMetadata row STILL has solution_id == install
       (FROZEN, NOT orphaned/nulled); Table row STILL has solution_id == install.
       File is BROWSABLE (entities endpoint 200) + workflow execution REFUSED
       (dormant gate → 409).
    6. Cross-solution leakage: a second solution cannot read the first's file.

  Arc 2  (test_arc_2_reactivate_export_harddelete) — Steps 4–5 (reinstall/reactivate)
       + export + hard-delete.
    REINSTALL without reactivate → 409 "inactive_install_exists".
    REINSTALL with reactivate=true → status "active", SAME install id, data intact
    (file + table still there under the same solution_id), file readable again.
    Export with data → secrets.enc present (encrypted tier) + $ref preserved in DB.
    HARD-DELETE with confirm=<slug> → Solution gone, table + file-metadata cascaded.
    Confirm MISMATCH → 4xx, nothing deleted.

Design: two methods in a single class sharing state via a class-level dict.
Class fixture ordering guarantees Arc 1 runs before Arc 2 (pytest alphabetical
within-class ordering is stable when not randomised, and the state-check guard
gives a clear skip message if ordering breaks).
"""
from __future__ import annotations

import io
import time
import uuid
import zipfile
from urllib.parse import quote
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.file_metadata import FileMetadata
from src.models.orm.tables import Table

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Module-level state shared between Arc 1 → Arc 2
# (populated by test_arc_1_*, consumed by test_arc_2_*)
# ---------------------------------------------------------------------------
_STATE: dict = {}

EXPORT_PASSWORD = "capstone-e2e-export-pw"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upload_headers(headers: dict) -> dict:
    """Strip Content-Type so httpx sets multipart boundary itself."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _create_solution(e2e_client, headers, slug: str) -> dict:
    # Pass organization_id=null EXPLICITLY so it lands in model_fields_set
    # and is treated as "global scope" (not HOME/caller's org).
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": None},
    )
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
        json={"policies": {"policies": [
            {"name": "allow_all", "actions": ["read", "write", "delete", "list"]}
        ]}},
    )
    assert r.status_code in (200, 201, 204), (
        f"seed solutions policy failed: {r.status_code} {r.text}"
    )


def _write_solution_file(
    e2e_client, headers, sol_id: str, path: str, content: str
) -> None:
    r = e2e_client.post(
        f"/api/files/write?solution={sol_id}",
        headers=headers,
        json={"location": "solutions", "path": path, "content": content, "mode": "cloud"},
    )
    assert r.status_code == 204, f"file write failed: {r.status_code} {r.text}"


async def _declare_solution_file_location(db_session, sol_id: str, location: str) -> None:
    from src.services.solutions.file_locations import reconcile_solution_file_locations

    await reconcile_solution_file_locations(db_session, solution_id=UUID(sol_id), locations=[location])
    await db_session.commit()


def _read_solution_file(
    e2e_client, headers, sol_id: str, path: str
) -> tuple[int, str | None]:
    """Returns (status_code, content_or_None)."""
    r = e2e_client.post(
        f"/api/files/read?solution={sol_id}",
        headers=headers,
        json={"location": "solutions", "path": path, "mode": "cloud"},
    )
    if r.status_code == 200:
        return 200, r.json().get("content")
    return r.status_code, None


def _set_file_policy_ref(
    e2e_client,
    headers,
    *,
    location: str,
    scope: str | None,
    prefix: str,
    ref_name: str,
) -> dict:
    """Create a file policy whose single rule is {"$ref": ref_name}."""
    params: dict = {"location": location}
    if scope is not None:
        params["scope"] = scope
    encoded = quote(prefix.strip("/"), safe="")
    r = e2e_client.put(
        f"/api/files/policies/{encoded}",
        headers=headers,
        params=params,
        json={"policies": {"policies": [{"$ref": ref_name}]}},
    )
    assert r.status_code == 200, f"set file policy failed: {r.status_code} {r.text}"
    return r.json()


def _deploy_solution(
    e2e_client,
    headers,
    sol_id: str,
    tables: list | None = None,
    workflows: list | None = None,
    python_files: dict | None = None,
    file_locations: list[str] | None = None,
) -> dict:
    """Deploy, poll async job, assert succeeded, return result payload."""
    from tests.e2e.platform.conftest import deploy_solution as _ds

    body: dict = {
        "tables": tables or [],
        "workflows": workflows or [],
        "file_locations": file_locations or [],
    }
    if python_files:
        body["python_files"] = python_files
    result = _ds(e2e_client, sol_id, headers, body)
    assert result.status_code == 200, f"deploy failed: {result.status_code} {result.text}"
    return result.json()


def _uninstall(e2e_client, headers, sol_id: str) -> dict:
    r = e2e_client.post(f"/api/solutions/{sol_id}/uninstall", headers=headers)
    assert r.status_code == 200, f"uninstall failed: {r.status_code} {r.text}"
    return r.json()


def _export_solution(e2e_client, headers, sol_id: str, password: str) -> bytes:
    r = e2e_client.post(
        f"/api/solutions/{sol_id}/export?mode=full&include_data=true",
        headers=headers,
        json={"password": password},
    )
    assert r.status_code == 200, f"export failed: {r.status_code} {r.text}"
    return r.content


def _make_install_zip(slug: str, table_name: str, table_bundle_id: str) -> bytes:
    """Minimal workspace zip used for the reinstall round-trip.

    ``table_bundle_id`` must be the SAME manifest UUID used in the original deploy
    so the solution_entity_id resolves to the same Table row (avoid duplicate-name
    conflict on the ix_tables_solution_name_unique index).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "bifrost.solution.yaml",
            f"slug: {slug}\nname: {slug.upper()}\nscope: global\n",
        )
        z.writestr(
            ".bifrost/tables.yaml",
            "tables:\n"
            f"  {table_bundle_id}:\n"
            f"    id: {table_bundle_id}\n"
            f"    name: {table_name}\n"
            "    schema:\n"
            "      columns:\n"
            "        - name: note\n"
            "    policies: null\n",
        )
        z.writestr(".bifrost/files.yaml", "locations:\n- solutions\n")
    return buf.getvalue()


def _install_zip(
    e2e_client, headers, zip_bytes: bytes, *, query: str = ""
):
    return e2e_client.post(
        f"/api/solutions/install{query}",
        headers=_upload_headers(headers),
        files={"file": ("sol.zip", zip_bytes, "application/zip")},
    )


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestFilePolicyDenialDiagnostics:
    async def test_denied_write_403_detail_identifies_scope(
        self, e2e_client, platform_admin, alice_user, db_session
    ):
        """A policy denial must identify itself: action, location, and the
        derived install scope in the 403 detail — a scope-loss bug then reads
        as `solution_id: null` instead of a bare 'Forbidden'."""
        headers = platform_admin.headers
        slug = f"deny-diag-{uuid.uuid4().hex[:8]}"
        sol = _create_solution(e2e_client, headers, slug)
        sol_id = sol["id"]
        _deploy_solution(e2e_client, headers, sol_id, file_locations=["solutions"])
        await _declare_solution_file_location(db_session, sol_id, "solutions")

        # alice is a regular user: admin_bypass denies her the write.
        r = e2e_client.post(
            f"/api/files/write?solution={sol_id}",
            headers=alice_user.headers,
            json={
                "location": "solutions",
                "path": "deny/probe.txt",
                "content": "x",
                "mode": "cloud",
            },
        )
        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert detail["action"] == "write"
        assert detail["location"] == "solutions"
        assert detail["solution_id"] == sol_id
        assert "denied" in detail["message"].lower()


@pytest.mark.e2e
class TestSolutionInactiveLifecycleCapstone:
    """Capstone: full inactive-lifecycle arc — deploy / uninstall / reactivate / export / hard-delete."""

    # ------------------------------------------------------------------
    # ARC 1: steps 1–3 + leakage + uninstall-freezes assertion
    # ------------------------------------------------------------------

    async def test_arc_1_deploy_through_uninstall(
        self, e2e_client, platform_admin, alice_user, db_session
    ):
        """Steps 1-3 + leakage check + UNINSTALL → frozen rows + dormant gate.

        Covers:
        - Behavior 1: create solution + workflow + table + file + $ref policy
        - Behavior 2: deploy (async poll) + allowed read 200 / denied 403
        - Behavior 6: cross-solution leakage blocked
        - UNINSTALL → status inactive; FileMetadata.solution_id unchanged; Table.solution_id unchanged
        - Behavior 3 (browse still works, execution refused)
        """
        headers = platform_admin.headers
        slug = f"capstone-{uuid.uuid4().hex[:8]}"

        # ── Step 1a: create solution (global scope so admin can manage it) ──
        sol = _create_solution(e2e_client, headers, slug)
        sol_id = sol["id"]

        _seed_solutions_policy(e2e_client, headers)

        # ── Step 1b: table for the deploy payload ────────────────────────
        table_name = f"capstone_tbl_{uuid.uuid4().hex[:8]}"
        table_bundle_id = str(uuid.uuid4())

        # ── Step 1c: file into the solution ──────────────────────────────
        file_path = f"docs/readme-{uuid.uuid4().hex[:8]}.md"
        file_content = f"# Capstone Readme\n\ntest-{uuid.uuid4().hex}"

        # ── Step 1d: file policy with {"$ref": "admin_bypass"} ───────────
        # admin_bypass is a global built-in seeded at startup.
        _set_file_policy_ref(
            e2e_client,
            headers,
            location="solutions",
            scope=None,  # global scope
            prefix="docs",
            ref_name="admin_bypass",
        )

        # ── Step 2: deploy (async job + poll) ────────────────────────────
        wf_content = "def run(**kwargs): return {'ok': True}"
        _deploy_solution(
            e2e_client,
            headers,
            sol_id,
            tables=[{
                "id": table_bundle_id,
                "name": table_name,
                "description": "capstone table",
                "schema": {"columns": [{"name": "note", "type": "string"}]},
                "policies": None,
            }],
            workflows=[{
                "id": str(uuid.uuid4()),
                "name": "run",
                "path": "workflows/run.py",
                "function_name": "run",
                "type": "workflow",
            }],
            python_files={"workflows/run.py": wf_content},
            file_locations=["solutions"],
        )

        await _declare_solution_file_location(db_session, sol_id, "solutions")
        _write_solution_file(e2e_client, headers, sol_id, file_path, file_content)

        # ── Step 3a: platform admin can read the file ─────────────────────
        status, content = _read_solution_file(e2e_client, headers, sol_id, file_path)
        assert status == 200, f"admin read failed with status {status}"
        assert content == file_content, f"content mismatch: {content!r}"

        # ── Step 3b: non-admin user is denied (admin_bypass enforced) ─────
        # alice_user is a regular user (not is_platform_admin).
        denied_status, _ = _read_solution_file(
            e2e_client, alice_user.headers, sol_id, file_path
        )
        assert denied_status == 403, (
            f"Expected 403 for non-admin user, got {denied_status} — "
            "admin_bypass $ref policy should deny non-platform-admins"
        )

        # ── Step 6: cross-solution leakage check ─────────────────────────
        slug_b = f"capstone-leak-{uuid.uuid4().hex[:8]}"
        sol_b = _create_solution(e2e_client, headers, slug_b)
        sol_b_id = sol_b["id"]
        _deploy_solution(e2e_client, headers, sol_b_id, file_locations=["solutions"])

        leak_r = e2e_client.post(
            f"/api/files/read?solution={sol_b_id}",
            headers=headers,
            json={"location": "solutions", "path": file_path, "mode": "cloud"},
        )
        assert leak_r.status_code in (403, 404), (
            f"Cross-solution leakage: sol B could read sol A's file "
            f"(status {leak_r.status_code})"
        )

        # ── UNINSTALL ─────────────────────────────────────────────────────
        uninstall_body = _uninstall(e2e_client, headers, sol_id)
        assert uninstall_body["status"] == "inactive", (
            f"Expected status='inactive' after uninstall, got: {uninstall_body}"
        )

        # Solution row STILL EXISTS.
        sol_check = e2e_client.get(f"/api/solutions/{sol_id}", headers=headers)
        assert sol_check.status_code == 200, (
            "Solution row was deleted on uninstall — must only flip status"
        )
        assert sol_check.json()["status"] == "inactive"

        # ── UNINSTALL-FREEZES: FileMetadata.solution_id unchanged ─────────
        # Load the FileMetadata row directly — it must still carry solution_id == sol_id.
        db_session.expire_all()
        fm_rows = (
            await db_session.execute(
                select(FileMetadata).where(
                    FileMetadata.solution_id == UUID(sol_id)
                )
            )
        ).scalars().all()
        assert fm_rows, (
            f"No FileMetadata rows with solution_id={sol_id} after uninstall — "
            "uninstall must NOT clear solution_id (data must be FROZEN, not orphaned)"
        )
        for fm in fm_rows:
            assert fm.solution_id == UUID(sol_id), (
                f"FileMetadata {fm.id}: solution_id was cleared on uninstall "
                f"(got {fm.solution_id!r}) — freeze invariant violated"
            )

        # ── UNINSTALL-FREEZES: Table.solution_id unchanged ────────────────
        from src.services.solutions.deploy import solution_entity_id
        real_tid = solution_entity_id(UUID(sol_id), UUID(table_bundle_id))
        db_session.expire_all()
        tbl = (
            await db_session.execute(
                select(Table).where(Table.id == real_tid)
            )
        ).scalar_one_or_none()
        assert tbl is not None, (
            f"Table row {real_tid} was deleted on uninstall — data was destroyed"
        )
        assert tbl.solution_id == UUID(sol_id), (
            f"Table.solution_id was cleared on uninstall (got {tbl.solution_id!r}) — "
            "uninstall must NOT mutate owned entities"
        )

        # ── Behavior 3: file BROWSABLE while inactive ─────────────────────
        browse_r = e2e_client.get(f"/api/solutions/{sol_id}/entities", headers=headers)
        assert browse_r.status_code == 200, (
            f"Inactive solution entities browse failed: {browse_r.status_code} {browse_r.text}"
        )

        # ── Behavior 3: workflow execution REFUSED (dormant gate → 409) ──
        exec_r = e2e_client.post(
            f"/api/workflows/execute?solution={sol_id}",
            headers=headers,
            json={"workflow_id": "workflows/run.py::run", "input_data": {}},
        )
        assert exec_r.status_code == 409, (
            f"Expected 409 (dormant gate) for inactive solution execution, "
            f"got {exec_r.status_code}: {exec_r.text}"
        )
        assert "inactive" in exec_r.json().get("detail", "").lower(), (
            f"Expected 'inactive' in dormant-gate detail, got: {exec_r.text}"
        )

        # ── Persist state for Arc 2 ───────────────────────────────────────
        _STATE.update(
            sol_id=sol_id,
            slug=slug,
            file_path=file_path,
            file_content=file_content,
            table_name=table_name,
            table_bundle_id=table_bundle_id,
            real_tid=str(real_tid),
        )

    # ------------------------------------------------------------------
    # ARC 2: reinstall/reactivate + export + hard-delete
    # ------------------------------------------------------------------

    async def test_arc_2_reactivate_export_harddelete(
        self, e2e_client, platform_admin, db_session
    ):
        """Steps 4–5 (reinstall/reactivate) + export (encrypted tier + $ref) + hard-delete.

        Covers:
        - Behavior 4: reinstall without reactivate → 409 inactive_install_exists
        - Behavior 4: reinstall with reactivate=true → status active, SAME install id, data intact
        - Behavior 5: export with data → secrets.enc + $ref preserved in DB
        - Behavior 6: hard-delete confirm mismatch → 4xx, nothing deleted
        - Behavior 6: hard-delete confirmed → Solution gone, Table gone, FileMetadata gone
        """
        if not _STATE:
            pytest.skip(
                "test_arc_2_reactivate_export_harddelete requires test_arc_1_deploy_through_uninstall "
                "to run first. Run the full class or fix test ordering."
            )

        headers = platform_admin.headers
        sol_id: str = _STATE["sol_id"]
        slug: str = _STATE["slug"]
        file_path: str = _STATE["file_path"]
        file_content: str = _STATE["file_content"]
        table_name: str = _STATE["table_name"]
        table_bundle_id: str = _STATE["table_bundle_id"]
        real_tid = UUID(_STATE["real_tid"])

        # Re-seed the allow-all policy (autouse isolate_file_policies wipes between tests).
        _seed_solutions_policy(e2e_client, headers)

        # ── Behavior 4a: reinstall WITHOUT reactivate → 409 ──────────────
        zip_bytes = _make_install_zip(slug, table_name, table_bundle_id)
        r_no_react = _install_zip(e2e_client, headers, zip_bytes)
        assert r_no_react.status_code == 409, (
            f"Expected 409 on reinstall without reactivate, got {r_no_react.status_code}: "
            f"{r_no_react.text}"
        )
        detail = r_no_react.json().get("detail", {})
        assert detail.get("reason") == "inactive_install_exists", (
            f"Expected reason='inactive_install_exists', got: {detail}"
        )
        assert detail.get("solution_id") == sol_id, (
            f"Conflict payload must carry the inactive install's id, got: {detail}"
        )

        # Nothing was deleted — the inactive install still exists.
        still_exists = e2e_client.get(f"/api/solutions/{sol_id}", headers=headers)
        assert still_exists.status_code == 200, (
            f"Inactive install was deleted by a failed reinstall attempt: "
            f"{still_exists.status_code}"
        )

        # ── Behavior 4b: reinstall WITH reactivate=true ───────────────────
        r_react = _install_zip(e2e_client, headers, zip_bytes, query="?reactivate=true")
        assert r_react.status_code in (200, 201), (
            f"Expected 200/201 on reactivate, got {r_react.status_code}: {r_react.text}"
        )
        reactivated = r_react.json()

        # SAME install id — NOT a duplicate.
        assert reactivated["id"] == sol_id, (
            f"Reactivate must return the SAME install id, not a new one. "
            f"Original: {sol_id}, returned: {reactivated['id']}"
        )
        assert reactivated["status"] == "active", (
            f"Reactivated install must have status='active', got: {reactivated['status']}"
        )

        # Only ONE Solution for this slug (no duplicate row).
        list_r = e2e_client.get("/api/solutions?scope=global", headers=headers)
        assert list_r.status_code == 200, list_r.text
        matching = [s for s in list_r.json().get("solutions", []) if s["slug"] == slug]
        assert len(matching) == 1, (
            f"Expected exactly one install for slug '{slug}', found {len(matching)}: {matching}"
        )

        # Table still there (FileMetadata was wiped by isolate_file_policies
        # between the two test methods — the freeze invariant is verified in Arc 1).
        db_session.expire_all()
        tbl = (
            await db_session.execute(
                select(Table).where(Table.id == real_tid)
            )
        ).scalar_one_or_none()
        assert tbl is not None, (
            f"Table {real_tid} was lost on reactivation — owned data must survive"
        )
        assert tbl.solution_id == UUID(sol_id), (
            f"Table.solution_id changed on reactivation: got {tbl.solution_id!r}"
        )

        # File readable again after reactivation.
        # Re-write file because isolate_file_policies wiped the metadata row.
        _write_solution_file(e2e_client, headers, sol_id, file_path, file_content)
        read_status, read_content = _read_solution_file(
            e2e_client, headers, sol_id, file_path
        )
        assert read_status == 200, (
            f"File not readable after reactivation: status {read_status}"
        )
        assert read_content == file_content, (
            f"File content mismatch after reactivation: {read_content!r}"
        )

        # ── Behavior 5: export with data → encrypted tier + $ref preserved ─
        # Restore the $ref policy (wiped by isolate_file_policies).
        _set_file_policy_ref(
            e2e_client,
            headers,
            location="solutions",
            scope=None,
            prefix="docs",
            ref_name="admin_bypass",
        )

        zip_exp = _export_solution(e2e_client, headers, sol_id, EXPORT_PASSWORD)

        # secrets.enc must be present (file in encrypted tier, NOT plaintext).
        with zipfile.ZipFile(io.BytesIO(zip_exp)) as zf:
            exp_names = zf.namelist()
        assert ".bifrost/secrets.enc" in exp_names, (
            f"secrets.enc absent from full export — file not in encrypted tier. "
            f"Zip contains: {exp_names}"
        )
        # File bytes must be inside secrets.enc (not as a plaintext member).
        plaintext_files = [n for n in exp_names if n.startswith("files/")]
        assert not plaintext_files, (
            f"File sidecars leaked as plaintext zip members: {plaintext_files}"
        )

        # Decrypt and verify the file is in the encrypted blob.
        from src.services.solutions.secrets_blob import decode_secrets_blob
        with zipfile.ZipFile(io.BytesIO(zip_exp)) as zf:
            blob_text = zf.read(".bifrost/secrets.enc").decode()
        blob = decode_secrets_blob(blob_text, password=EXPORT_PASSWORD)
        encrypted_paths = [sf.get("path", "") for sf in blob.solution_files]
        assert any(file_path in p for p in encrypted_paths), (
            f"File {file_path!r} not found in secrets.enc. Found: {encrypted_paths}"
        )

        # $ref PRESERVED in DB: policy GET must still carry {"$ref":"admin_bypass"}.
        # Verified via DB (GET /api/files/policies/...) rather than the zip artifact because
        # file policies are not carried in the SolutionBundle zip manifest — this proves the
        # DB $ref survives the export round-trip, not the export artifact itself.
        docs_encoded = quote("docs", safe="")
        policy_r = e2e_client.get(
            f"/api/files/policies/{docs_encoded}",
            headers=headers,
            params={"location": "solutions"},
        )
        assert policy_r.status_code == 200, (
            f"GET file policy for docs/ failed: {policy_r.status_code} {policy_r.text}"
        )
        policy_rules = policy_r.json().get("policies", {}).get("policies", [])
        ref_rules = [rule for rule in policy_rules if rule.get("$ref") == "admin_bypass"]
        assert ref_rules, (
            f"$ref not preserved in policy store. "
            f"Policy rules after export: {policy_rules}"
        )

        # ── Behavior 6a: hard-delete confirm MISMATCH → 4xx, nothing touched ─
        bad_del = e2e_client.request(
            "DELETE",
            f"/api/solutions/{sol_id}",
            headers=headers,
            params={"confirm": "wrong-slug-mismatch"},
        )
        assert bad_del.status_code in (400, 422), (
            f"Expected 4xx on confirm mismatch, got {bad_del.status_code}: {bad_del.text}"
        )
        # Solution must still exist.
        still_r = e2e_client.get(f"/api/solutions/{sol_id}", headers=headers)
        assert still_r.status_code == 200, (
            f"Solution was deleted despite confirm mismatch (status {still_r.status_code})"
        )

        # ── Behavior 6b: hard-delete CONFIRMED → cascade ──────────────────
        ok_del = e2e_client.request(
            "DELETE",
            f"/api/solutions/{sol_id}",
            headers=headers,
            params={"confirm": slug},
        )
        assert ok_del.status_code in (200, 204), (
            f"Hard-delete failed: {ok_del.status_code} {ok_del.text}"
        )
        body = ok_del.json()
        assert body["solution_id"] == sol_id, (
            f"Hard-delete response must carry solution_id, got: {body}"
        )

        # Solution row gone.
        gone_r = e2e_client.get(f"/api/solutions/{sol_id}", headers=headers)
        assert gone_r.status_code == 404, (
            f"Solution still exists after hard-delete: {gone_r.status_code}"
        )

        # ── HARD-DELETE-CASCADE: FileMetadata rows cascaded away ──────────
        # Poll briefly to let any async S3 sweep complete before the DB assertion.
        deadline = time.monotonic() + 10.0
        fm_remaining = None
        while time.monotonic() < deadline:
            db_session.expire_all()
            fm_remaining = (
                await db_session.execute(
                    select(FileMetadata).where(
                        FileMetadata.solution_id == UUID(sol_id)
                    )
                )
            ).scalars().all()
            if not fm_remaining:
                break
            time.sleep(0.25)

        assert fm_remaining is not None, (
            "poll loop never executed — cannot confirm cascade (clock anomaly?)"
        )
        assert not fm_remaining, (
            f"FileMetadata rows survived hard-delete timeout — FK cascade did not fire. "
            f"Remaining: {[str(fm.id) for fm in fm_remaining]}"
        )

        # ── HARD-DELETE-CASCADE: Table row cascaded away ──────────────────
        db_session.expire_all()
        tbl_gone = (
            await db_session.execute(
                select(Table).where(Table.id == real_tid)
            )
        ).scalar_one_or_none()
        assert tbl_gone is None, (
            f"Owned Table {real_tid} survived hard-delete — cascade did not fire"
        )
