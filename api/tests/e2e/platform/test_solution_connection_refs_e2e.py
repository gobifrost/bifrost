"""E2E: connection-references feature end-to-end, through the real endpoints.

The connection-refs feature has three round-trip behaviors that the unit tests
(test_solution_capture_connections / test_solution_deploy_shells /
test_solution_setup_status / test_solution_connection_runtime) each cover in
isolation. This file proves they hold together THROUGH THE REAL HTTP ENDPOINTS:

  - ``POST /api/solutions/{id}/export`` — capture rebuilds the bundle, which
    scans the solution's workflow source for ``integrations.get("X")``, resolves
    X to its global Integration, and writes a SECRET-SCRUBBED connection template
    (a SolutionConnectionSchema row). Assert: the template carries no client_id
    and the exported zip bytes contain NO secret.
  - ``POST /api/solutions/{id}/deploy`` — a bundle that declares a connection for
    an integration that does NOT exist globally pre-creates an EMPTY integration
    shell (config schema + empty-client_id OAuth provider). Assert the shell is
    created through the real deploy endpoint and never clobbers an existing one.
  - ``GET /api/solutions/{id}/setup`` — surfaces a connection item for the
    declared integration with is_set/has_oauth reflecting the shell.
  - README round-trips: a deploy that carries ``readme`` lands it on
    Solution.readme, readable via ``GET /api/solutions/{id}/readme``.

The "capture-org has it / install-org doesn't" round-trip in the design note is
IMPOSSIBLE for a real round-trip: Integrations are GLOBAL, so an integration that
exists for the capture solution also exists for any install. The feature is
therefore exercised in the two realistic halves the design intends — template
fidelity+scrub on export (integration present), and shell-creation on deploy
(integration absent) — both against real endpoints.

NOTE: the install-blocking (test_solution_install_blocking.py) and runtime-424
(test_solution_connection_runtime.py) behaviors live in their own files and are
NOT duplicated here.
"""
from __future__ import annotations

import io
import uuid
import zipfile
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.integrations import Integration, IntegrationConfigSchema
from src.models.orm.oauth import OAuthProvider
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.workflows import Workflow
from tests.e2e.platform.conftest import wait_for_deploy, wait_for_install

pytestmark = pytest.mark.e2e

# A unique, recognizable secret string. The whole point of the scrub assertion is
# that this NEVER appears in the exported bundle bytes.
_SECRET_CLIENT_ID = "super-secret-client-id-DO-NOT-LEAK-{tok}"


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": None},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _assert_secret_absent(zip_bytes: bytes, *secrets: bytes | str) -> None:
    """Decompress every member of the zip and assert no secret appears in any.

    Stronger than scanning the raw (DEFLATE-compressed) zip bytes: a secret that
    survives compression in a non-literal form would slip past a raw scan but is
    caught here because each member is fully decompressed before scanning."""
    needles = [s.encode() if isinstance(s, str) else s for s in secrets]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            content = zf.read(name)
            for needle in needles:
                assert needle not in content, f"secret leaked into zip member {name!r}"


def _upload_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip Content-Type so httpx sets the multipart boundary itself."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


async def test_export_scrubs_connection_template_and_carries_no_secret(
    e2e_client, platform_admin, db_session
):
    """Template fidelity + scrub through the real ``/export`` endpoint.

    A solution-owned workflow calls ``integrations.get("HaloPSA-<id>")`` where a
    GLOBAL Integration of that name exists with an OAuth provider carrying a real
    secret client_id and a config-schema item. Exporting must:
      (a) write a SolutionConnectionSchema row whose template carries the safe
          shape (config_schema item present) but NO client_id (scrubbed), and
      (b) produce a zip whose bytes do NOT contain the secret string anywhere.
    """
    headers = platform_admin.headers
    tok = uuid.uuid4().hex[:8]
    integ_name = f"HaloPSA-{tok}"
    secret = _SECRET_CLIENT_ID.format(tok=tok)
    slug = f"conn-export-{tok}"

    sid = _create_solution(e2e_client, headers, slug)

    # Global Integration + OAuth provider (real secret) + a config-schema item.
    integ = Integration(id=uuid.uuid4(), name=integ_name, entity_id_name="tenant_id")
    db_session.add(integ)
    await db_session.flush()
    db_session.add(
        IntegrationConfigSchema(
            integration_id=integ.id, key="base_url", type="string",
            required=True, position=0,
        )
    )
    db_session.add(
        OAuthProvider(
            id=uuid.uuid4(),
            provider_name=f"halopsa-{tok}",
            display_name="HaloPSA",
            oauth_flow_type="authorization_code",
            client_id=secret,
            encrypted_client_secret=b"also-secret",
            integration_id=integ.id,
        )
    )

    # A solution-owned workflow whose source references the integration. The
    # source must live in repo storage at wf.path so the export scanner reads it.
    wf_path = f"workflows/sync_{tok}.py"
    src = (
        f'def run(sdk):\n'
        f'    tickets = sdk.integrations.get("{integ_name}").list()\n'
        f'    return tickets\n'
    )
    wr = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": wf_path, "content": src, "encoding": "utf-8"},
    )
    assert wr.status_code in (200, 201), wr.text

    wf = Workflow(
        id=uuid.uuid4(),
        name=f"sync-{tok}",
        function_name="run",
        path=wf_path,
        type="workflow",
        is_active=True,
        solution_id=UUID(sid),
    )
    db_session.add(wf)
    await db_session.commit()

    # Real shareable export — capture rebuilds the bundle (writes the connection
    # declaration) and serializes the zip.
    exp = e2e_client.post(
        f"/api/solutions/{sid}/export?mode=shareable",
        headers=headers,
        json={},
    )
    assert exp.status_code == 200, exp.text
    zip_bytes = exp.content

    # (b) No secret anywhere in the exported bundle — scanned HERMETICALLY:
    # decompress each zip member and scan its DECOMPRESSED bytes. A raw-bytes
    # scan of the DEFLATE-compressed zip is a weak backstop (the secret could
    # survive compression unrecognizably); decompressing proves it is truly
    # absent from every file's content.
    _assert_secret_absent(zip_bytes, secret, b"also-secret")

    # (a) The connection declaration was written and is secret-scrubbed.
    rows = (
        await db_session.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == UUID(sid)
            )
        )
    ).scalars().all()
    decl = next((r for r in rows if r.integration_name == integ_name), None)
    assert decl is not None, f"no connection declaration for {integ_name}: {rows}"
    template = decl.template
    assert template["name"] == integ_name
    # Safe shape carried: the config-schema item round-trips.
    keys = {c["key"] for c in template.get("config_schema") or []}
    assert "base_url" in keys, f"config schema not carried: {template}"
    # OAuth shape carried but scrubbed: provider_name present, client_id absent.
    oauth = template.get("oauth") or {}
    assert oauth.get("provider_name") == f"halopsa-{tok}"
    assert "client_id" not in oauth, f"client_id NOT scrubbed: {oauth}"
    assert "encrypted_client_secret" not in oauth

    # Setup status surfaces the connection item, satisfied because the GLOBAL
    # Integration exists (is_set) and the template carries oauth (has_oauth). The
    # SolutionConnectionSchema row that powers this was persisted by the export's
    # capture pass above — proving the export→setup path end to end.
    setup = e2e_client.get(f"/api/solutions/{sid}/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    conn_items = [i for i in setup.json()["items"] if i.get("kind") == "connection"]
    conn = next((i for i in conn_items if i.get("key") == integ_name), None)
    assert conn is not None, f"no connection setup item for {integ_name}: {conn_items}"
    assert conn["is_set"] is True, f"global integration exists → is_set True: {conn}"
    assert conn["has_oauth"] is True, f"template has oauth → has_oauth True: {conn}"


async def test_deploy_creates_integration_shell_and_readme_round_trips(
    e2e_client, platform_admin, db_session
):
    """Shell creation + README through the real ``/deploy`` & ``/readme`` endpoints.

    A deploy bundle declares a connection for an integration that does NOT exist
    globally. The real deploy endpoint must:
      (a) create an EMPTY Integration shell (config schema + an OAuth provider
          whose client_id is empty) — integrations_shell_created == 1,
      (b) re-deploying with the SAME declaration must NOT clobber it (shell
          count 0 the second time),
      (c) the README carried on the deploy bundle round-trips to /readme.
    """
    headers = platform_admin.headers
    tok = uuid.uuid4().hex[:8]
    integ_name = f"GhostInteg-{tok}"  # unique → absent globally at deploy time
    slug = f"conn-deploy-{tok}"
    readme_md = f"# {slug}\n\nConnect **{integ_name}** before running.\n"

    sid = _create_solution(e2e_client, headers, slug)

    connection_decl = {
        "integration_name": integ_name,
        "position": 0,
        "template": {
            "name": integ_name,
            "entity_id_name": "tenant_id",
            "default_entity_id": "common",
            "config_schema": [
                {
                    "key": "base_url", "type": "string", "required": True,
                    "description": None, "options": None, "position": 0,
                }
            ],
            "oauth": {
                "provider_name": f"ghost-{tok}", "display_name": "Ghost",
                "oauth_flow_type": "authorization_code",
                "authorization_url": "https://a", "token_url": "https://t",
                "audience": None, "token_url_defaults": {},
                "entity_id_source": None, "scopes": [], "redirect_uri": None,
            },
        },
    }
    deploy_body = {
        "python_files": {},
        "workflows": [],
        "config_schemas": [],
        "connection_schemas": [connection_decl],
        "readme": readme_md,
    }

    # (a) First deploy creates the shell.
    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy", headers=headers, json=deploy_body
    )
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text
    assert dep.json()["integrations_shell_created"] == 1, dep.text

    # The shell really landed: an Integration row + empty-secret OAuth provider.
    integ = (
        await db_session.execute(
            select(Integration).where(Integration.name == integ_name)
        )
    ).scalar_one()
    schema = (
        await db_session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == integ.id
            )
        )
    ).scalars().all()
    assert {s.key for s in schema} == {"base_url"}
    provider = (
        await db_session.execute(
            select(OAuthProvider).where(OAuthProvider.integration_id == integ.id)
        )
    ).scalar_one()
    assert provider.client_id == "", "shell OAuth must have empty client_id"
    assert provider.encrypted_client_secret == b""

    # (b) Re-deploy with the same declaration must NOT clobber → 0 shells created.
    dep2 = e2e_client.post(
        f"/api/solutions/{sid}/deploy", headers=headers, json=deploy_body
    )
    dep2 = wait_for_deploy(e2e_client, dep2, headers)
    assert dep2.status_code == 200, dep2.text
    assert dep2.json()["integrations_shell_created"] == 0, (
        "re-deploy must not re-create / clobber the existing integration"
    )

    # (c) README round-trips to the readme endpoint.
    rd = e2e_client.get(f"/api/solutions/{sid}/readme", headers=headers)
    assert rd.status_code == 200, rd.text
    assert rd.json()["readme"] == readme_md, "README did not round-trip"


async def test_zip_export_install_round_trip_surfaces_connection_and_readme(
    e2e_client, platform_admin, db_session
):
    """TRUE distribution round-trip: export a solution to a ZIP, install the zip
    into a FRESH scope, and prove the connection declaration + README travel.

    This is the gap Task 14b closes: before the fix, the export zip carried
    NEITHER ``.bifrost/connections.yaml`` NOR ``README.md``, the zip parser read
    neither back, and install persisted no SolutionConnectionSchema row — so an
    installed (not captured-in-place) solution surfaced ZERO connection items at
    /setup and lost its README. This test would FAIL before the fix.

    Steps:
      1. Source solution (global) with a workflow referencing a GLOBAL Integration
         + a README set via /readme.
      2. POST /export → zip (capture writes the connection decl + serializes
         connections.yaml + README.md into the zip).
      3. POST /install into a FRESH ORG scope (a brand-new Solution row, distinct
         from the source). The global integration already exists.
      4. Assert the installed solution's /setup surfaces the connection item, its
         /readme round-trips, and the integration shell exists.
    """
    headers = platform_admin.headers
    tok = uuid.uuid4().hex[:8]
    integ_name = f"HaloPSA-rt-{tok}"
    slug = f"conn-rt-{tok}"
    readme_md = f"# {slug}\n\nConnect **{integ_name}** before running.\n"

    sid = _create_solution(e2e_client, headers, slug)

    # Global Integration + OAuth provider + a config-schema item (a real, present
    # integration — its shell is created, then capture scrubs the template).
    integ = Integration(id=uuid.uuid4(), name=integ_name, entity_id_name="tenant_id")
    db_session.add(integ)
    await db_session.flush()
    db_session.add(
        IntegrationConfigSchema(
            integration_id=integ.id, key="base_url", type="string",
            required=True, position=0,
        )
    )
    db_session.add(
        OAuthProvider(
            id=uuid.uuid4(),
            provider_name=f"halopsa-rt-{tok}",
            display_name="HaloPSA",
            oauth_flow_type="authorization_code",
            client_id="rt-secret-client-id",
            encrypted_client_secret=b"rt-secret",
            integration_id=integ.id,
        )
    )

    # A solution-owned workflow whose source references the integration.
    wf_path = f"workflows/rt_{tok}.py"
    src = (
        f'def run(sdk):\n'
        f'    return sdk.integrations.get("{integ_name}").list()\n'
    )
    wr = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": wf_path, "content": src, "encoding": "utf-8"},
    )
    assert wr.status_code in (200, 201), wr.text
    wf = Workflow(
        id=uuid.uuid4(),
        name=f"rt-{tok}",
        function_name="run",
        path=wf_path,
        type="workflow",
        is_active=True,
        solution_id=UUID(sid),
    )
    db_session.add(wf)
    await db_session.commit()

    # README on the SOURCE install — it must travel via the zip's README.md.
    rd_put = e2e_client.put(
        f"/api/solutions/{sid}/readme", headers=headers, json={"readme": readme_md}
    )
    assert rd_put.status_code == 200, rd_put.text

    # (2) Export to a shareable zip — capture writes the connection decl, then
    # build_workspace_zip serializes connections.yaml + README.md into the zip.
    exp = e2e_client.post(
        f"/api/solutions/{sid}/export?mode=shareable", headers=headers, json={}
    )
    assert exp.status_code == 200, exp.text
    zip_bytes = exp.content

    # The zip really carries both files now (the heart of the fix).
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert ".bifrost/connections.yaml" in names, f"connections.yaml missing: {names}"
        assert "README.md" in names, f"README.md missing: {names}"
        assert zf.read("README.md").decode("utf-8") == readme_md
    # And no secret leaked (hermetic, decompressed scan).
    _assert_secret_absent(zip_bytes, b"rt-secret-client-id", b"rt-secret")

    # (3) Install the zip into a FRESH ORG scope → a brand-new Solution row.
    org_resp = e2e_client.post(
        "/api/organizations", headers=headers, json={"name": f"rt-org-{tok}"}
    )
    assert org_resp.status_code == 201, org_resp.text
    org_id = org_resp.json()["id"]

    inst = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=_upload_headers(headers),
            files={"file": (f"{slug}.zip", zip_bytes, "application/zip")},
            data={"organization_id": org_id},
        ),
        headers,
    )
    assert inst.status_code in (200, 201), inst.text
    installed_id = inst.json()["id"]
    # It is a genuinely fresh install (different row from the global source).
    assert installed_id != sid, "install must create a fresh org-scoped row"

    # (4a) The installed solution's /setup surfaces the connection item — the
    # SolutionConnectionSchema row was persisted on install (Part D), not capture.
    setup = e2e_client.get(f"/api/solutions/{installed_id}/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    conn_items = [i for i in setup.json()["items"] if i.get("kind") == "connection"]
    conn = next((i for i in conn_items if i.get("key") == integ_name), None)
    assert conn is not None, (
        f"installed solution surfaced NO connection item for {integ_name}: {conn_items}"
    )

    # The persisted row really exists for the installed (not source) solution.
    inst_rows = (
        await db_session.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == UUID(installed_id)
            )
        )
    ).scalars().all()
    assert any(r.integration_name == integ_name for r in inst_rows), (
        f"no SolutionConnectionSchema persisted on install: {inst_rows}"
    )

    # (4b) README round-trips to the installed solution's /readme.
    rd = e2e_client.get(f"/api/solutions/{installed_id}/readme", headers=headers)
    assert rd.status_code == 200, rd.text
    assert rd.json()["readme"] == readme_md, "README did not round-trip through the zip"

    # (4c) The integration shell exists (it pre-existed globally; deploy never
    # clobbers it — but it must be present for the connection to be satisfiable).
    shell = (
        await db_session.execute(
            select(Integration).where(Integration.name == integ_name)
        )
    ).scalar_one_or_none()
    assert shell is not None, "integration shell missing after install"
