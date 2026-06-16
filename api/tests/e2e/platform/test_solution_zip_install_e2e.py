"""E2E: drag-and-drop ZIP install of a Solution (Tasks 11+12).

* PREVIEW (``POST /api/solutions/install/preview``) parses the workspace and
  reports its entities + config declarations without persisting anything.
* INSTALL (``POST /api/solutions/install``) deploys the bundle AND applies the
  provided config VALUES atomically under the per-install write lock — so the
  install never exists without its just-entered secret. We prove atomicity by
  reading ``/entities`` afterward: the required secret's ``value_set`` is True
  and it is NOT in ``required_configs_unset``.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytestmark = pytest.mark.e2e


def _make_zip(
    slug: str,
    version: str | None = None,
    *,
    extra_workflow: bool = False,
    api_key_type: str = "secret",
) -> bytes:
    """A minimal Solution workspace zip: descriptor + a workflow (manifest +
    source) + a required secret config declaration.

    Entity ids are STABLE per slug (uuid5), mirroring a real workspace: the
    ``.bifrost/*.yaml`` manifests keep their ids across versions, so a v2 zip
    of the same Solution carries the same manifest UUIDs as v1.
    ``extra_workflow``/``api_key_type`` shape a "v2" zip for upgrade-diff tests.
    """
    wf_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/workflows/main"))
    cfg_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/configs/API_KEY"))
    descriptor = f"slug: {slug}\nname: {slug.upper()}\nscope: global\n"
    if version is not None:
        descriptor += f"version: '{version}'\n"
    workflows_yaml = (
        "workflows:\n"
        f"  {wf_id}:\n"
        f"    id: {wf_id}\n"
        "    name: main\n"
        "    function_name: run\n"
        "    path: workflows/main.py\n"
    )
    files = {
        "bifrost.solution.yaml": descriptor,
        ".bifrost/configs.yaml": (
            "configs:\n"
            "  API_KEY:\n"
            f"    id: {cfg_id}\n"
            "    key: API_KEY\n"
            f"    type: {api_key_type}\n"
            "    required: true\n"
            "    description: needed\n"
            "    position: 0\n"
        ),
        "workflows/main.py": "def run(sdk):\n    return 'ok'\n",
    }
    if extra_workflow:
        wf2_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/workflows/extra"))
        workflows_yaml += (
            f"  {wf2_id}:\n"
            f"    id: {wf2_id}\n"
            "    name: extra\n"
            "    function_name: run\n"
            "    path: workflows/extra.py\n"
        )
        files["workflows/extra.py"] = "def run(sdk):\n    return 'extra'\n"
    files[".bifrost/workflows.yaml"] = workflows_yaml
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


async def test_zip_install_atomic_deploy_and_values(e2e_client, platform_admin):
    headers = platform_admin.headers
    # httpx sets the multipart Content-Type itself; the auth headers carry an
    # application/json Content-Type that would otherwise override it and make the
    # server fail to parse the upload — strip it for the multipart requests.
    upload_headers = {
        k: v for k, v in headers.items() if k.lower() != "content-type"
    }
    slug = f"zip-e2e-{uuid.uuid4().hex[:8]}"
    data = _make_zip(slug)

    # PREVIEW: parse-only, nothing persisted.
    pv = e2e_client.post(
        "/api/solutions/install/preview",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", data, "application/zip")},
    )
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert body["slug"] == slug
    assert len(body["workflows"]) == 1
    assert any(c["key"] == "API_KEY" for c in body["config_schemas"])

    # INSTALL: deploy + apply the secret value atomically.
    inst = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", data, "application/zip")},
        data={"config_values": '{"API_KEY": "sk_x"}'},
    )
    assert inst.status_code in (200, 201), inst.text
    sid = inst.json()["id"]

    # The deployed workflow landed and the secret VALUE is set (atomic with deploy).
    ent = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert ent.status_code == 200, ent.text
    entities = ent.json()
    assert len(entities["workflows"]) >= 1, "workflow should have deployed"

    api_key = next((c for c in entities["configs"] if c["key"] == "API_KEY"), None)
    assert api_key is not None, "API_KEY declaration should be present"
    assert api_key["value_set"] is True, "the provided secret value should be set"
    assert "API_KEY" not in entities["required_configs_unset"], (
        "a provided required value must not be reported as unset"
    )


async def test_zip_install_refused_into_git_connected_install(e2e_client, platform_admin):
    """A zip POSTed for a slug+scope that already has a git-connected install
    must be refused with 409 — auto-pull is that install's only writer."""
    headers = platform_admin.headers
    upload_headers = {
        k: v for k, v in headers.items() if k.lower() != "content-type"
    }
    slug = f"zip-gc-{uuid.uuid4().hex[:8]}"

    # Create a git-connected global install for this slug first.
    create = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug,
        "name": slug.upper(),
        "organization_id": None,  # explicit global (kind derives from org_id)
        "git_connected": True,
        "git_repo_url": "https://example.com/repo.git",
    })
    assert create.status_code in (200, 201), create.text

    # A zip for the same slug+scope resolves to that connected install → refused.
    inst = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", _make_zip(slug), "application/zip")},
        data={"config_values": "{}"},
    )
    assert inst.status_code == 409, inst.text
    assert "git-connected" in inst.json()["detail"]


async def test_zip_install_upgrade_and_downgrade_gate(e2e_client, platform_admin):
    """Versioned zip install (Task 20):

    * install v1 → install v2 (same slug+scope) is an UPGRADE of the SAME
      install (no second install row); version + upgraded_from_version recorded.
    * an older zip (v0.9) → 409 with the downgrade detail.
    * the same older zip with ?force=true → succeeds and records the downgrade.
    """
    headers = platform_admin.headers
    upload_headers = {
        k: v for k, v in headers.items() if k.lower() != "content-type"
    }
    slug = f"zip-ver-{uuid.uuid4().hex[:8]}"

    def _install(version: str, force: bool = False):
        url = "/api/solutions/install" + ("?force=true" if force else "")
        return e2e_client.post(
            url,
            headers=upload_headers,
            files={"file": (f"{slug}.zip", _make_zip(slug, version), "application/zip")},
            data={"config_values": "{}"},
        )

    # Install v1.0.0.
    v1 = _install("1.0.0")
    assert v1.status_code in (200, 201), v1.text
    sid = v1.json()["id"]
    assert v1.json()["version"] == "1.0.0"

    # Install v1.1.0 for the same slug+scope → SAME install id, version updated.
    v2 = _install("1.1.0")
    assert v2.status_code in (200, 201), v2.text
    assert v2.json()["id"] == sid, "upgrade must not create a second install"
    assert v2.json()["version"] == "1.1.0"
    assert v2.json()["upgraded_from_version"] == "1.0.0"

    # An older zip is refused with the downgrade detail.
    down = _install("0.9.0")
    assert down.status_code == 409, down.text
    detail = down.json()["detail"]
    assert "0.9.0" in detail and "1.1.0" in detail and "force" in detail

    # Same zip, ?force=true → succeeds and records the downgrade.
    forced = _install("0.9.0", force=True)
    assert forced.status_code in (200, 201), forced.text
    assert forced.json()["id"] == sid
    assert forced.json()["version"] == "0.9.0"
    assert forced.json()["upgraded_from_version"] == "1.1.0"


async def test_zip_preview_returns_upgrade_diff_for_existing_install(
    e2e_client, platform_admin
):
    """Preview of a v2 zip whose slug+scope matches an existing install (Task 22):

    * ``existing_install`` identifies the install (id, name, version=v1)
    * ``diff`` reports the added workflow + the changed config declaration
    * preview stays read-only — no second install row appears
    * a fresh slug previews with ``existing_install``/``diff`` absent
    """
    headers = platform_admin.headers
    upload_headers = {
        k: v for k, v in headers.items() if k.lower() != "content-type"
    }
    slug = f"zip-diff-{uuid.uuid4().hex[:8]}"

    # Install v1 (one workflow, API_KEY declared as secret).
    v1 = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", _make_zip(slug, "1.0.0"), "application/zip")},
        data={"config_values": "{}"},
    )
    assert v1.status_code in (200, 201), v1.text
    sid = v1.json()["id"]

    # Preview a v2 zip: same slug+scope, one extra workflow, API_KEY type changed.
    v2_zip = _make_zip(slug, "2.0.0", extra_workflow=True, api_key_type="string")
    pv = e2e_client.post(
        "/api/solutions/install/preview",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", v2_zip, "application/zip")},
    )
    assert pv.status_code == 200, pv.text
    body = pv.json()

    existing = body["existing_install"]
    assert existing is not None, "preview must detect the existing install"
    assert existing["id"] == sid
    assert existing["version"] == "1.0.0"

    diff = body["diff"]
    assert diff is not None
    assert diff["workflows"]["added"] == ["extra"]
    assert diff["workflows"]["removed"] == []
    changed = {c["key"]: c for c in diff["config_schemas"]["changed"]}
    assert "API_KEY" in changed
    assert changed["API_KEY"]["from"]["type"] == "secret"
    assert changed["API_KEY"]["to"]["type"] == "string"

    # Read-only: preview must NOT have created a second install for this slug.
    listing = e2e_client.get("/api/solutions", headers=headers)
    assert listing.status_code == 200, listing.text
    rows = [s for s in listing.json()["solutions"] if s["slug"] == slug]
    assert len(rows) == 1 and rows[0]["id"] == sid

    # A fresh slug previews with no existing install and no diff.
    fresh_slug = f"zip-fresh-{uuid.uuid4().hex[:8]}"
    fresh = e2e_client.post(
        "/api/solutions/install/preview",
        headers=upload_headers,
        files={"file": (f"{fresh_slug}.zip", _make_zip(fresh_slug, "1.0.0"), "application/zip")},
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["existing_install"] is None
    assert fresh.json()["diff"] is None
    # ...and the fresh-slug preview created nothing either.
    listing2 = e2e_client.get("/api/solutions", headers=headers)
    assert [s for s in listing2.json()["solutions"] if s["slug"] == fresh_slug] == []


async def test_export_round_trips_the_installed_bundle(e2e_client, platform_admin):
    """GET /{id}/export returns the workspace zip the install's last write
    produced — re-parseable AND re-installable (the full round trip):

    install zip v1 → export → the export previews identically → installing the
    EXPORT as a new slug-scoped... (same slug+scope resolves to the same
    install) → re-INSTALLING the export onto the same install succeeds as a
    no-op full replace."""
    headers = platform_admin.headers
    upload_headers = {
        k: v for k, v in headers.items() if k.lower() != "content-type"
    }
    slug = f"zip-exp-{uuid.uuid4().hex[:8]}"
    data = _make_zip(slug, "1.0.0")

    inst = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", data, "application/zip")},
        data={"config_values": '{"API_KEY": "sk_x"}'},
    )
    assert inst.status_code in (200, 201), inst.text
    sid = inst.json()["id"]

    # EXPORT: the stored bundle, as a workspace zip.
    exp = e2e_client.post(f"/api/solutions/{sid}/export", json={}, headers=headers)
    assert exp.status_code == 200, exp.text
    assert exp.headers["content-type"] == "application/zip"
    assert f'filename="{slug}-1.0.0.zip"' in exp.headers.get("content-disposition", "")
    export_bytes = exp.content

    # The export parses to the same workspace (preview round trip).
    pv = e2e_client.post(
        "/api/solutions/install/preview",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", export_bytes, "application/zip")},
    )
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert body["slug"] == slug
    assert body["version"] == "1.0.0"
    assert len(body["workflows"]) == 1
    assert any(c["key"] == "API_KEY" for c in body["config_schemas"])
    # The preview matched it back to the SAME install (original manifest ids).
    assert body["existing_install"] is not None
    assert body["existing_install"]["id"] == sid

    # Re-INSTALL the export onto the same install: a no-op full replace.
    reinst = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": (f"{slug}.zip", export_bytes, "application/zip")},
    )
    assert reinst.status_code in (200, 201), reinst.text
    assert reinst.json()["id"] == sid

    ent = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert ent.status_code == 200, ent.text
    entities = ent.json()
    assert len(entities["workflows"]) == 1
    # Config VALUE survived the export re-install (values are instance-owned,
    # never carried in a bundle).
    api_key = next((c for c in entities["configs"] if c["key"] == "API_KEY"), None)
    assert api_key is not None and api_key["value_set"] is True


async def test_export_before_first_deploy_rebuilds_live(e2e_client, platform_admin):
    """Export rebuilds the bundle LIVE from the entities the install currently
    owns (it is not a stored-bundle fetch). An install created but never deployed
    owns nothing, so the export is a valid, re-installable zip carrying just the
    ``bifrost.solution.yaml`` descriptor — not a 404."""
    import io
    import zipfile

    headers = platform_admin.headers
    slug = f"zip-noexp-{uuid.uuid4().hex[:8]}"
    create = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug,
        "name": slug.upper(),
        "organization_id": None,  # explicit global (kind derives from org_id)
    })
    assert create.status_code in (200, 201), create.text
    sid = create.json()["id"]

    exp = e2e_client.post(f"/api/solutions/{sid}/export", json={}, headers=headers)
    assert exp.status_code == 200, exp.text
    assert exp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exp.content)) as zf:
        assert "bifrost.solution.yaml" in zf.namelist()
