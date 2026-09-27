"""Solution distribution E2E through one source deploy and one target install.

The source deploy combines the critical contracts that previously used separate
jobs: it creates a declared absent-integration shell, owns an app and workflow,
and carries a README. The workflow references a second, existing integration,
so shareable export captures its safe connection template. Installing that one
ZIP into a second org then proves the connections, README, entities, and binary
asset survive the distribution boundary.
"""

from __future__ import annotations

import base64
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


# A tiny but real 1x1 PNG. Its bytes are deliberately not valid UTF-8, so the
# test exercises the binary ``bin_dist_files`` export/install path.
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xcf\x00\x00\x00\x02\x00\x01\xe5'\xde\xfc"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
ASSET_REL = "assets/logo.png"
_SECRET_CLIENT_ID = "super-secret-client-id-DO-NOT-LEAK-{tok}"


def _upload_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip Content-Type so httpx sets the multipart boundary itself."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _assert_secret_absent(zip_bytes: bytes, *secrets: bytes | str) -> None:
    """Assert every decompressed ZIP member omits each supplied secret."""
    needles = [secret.encode() if isinstance(secret, str) else secret for secret in secrets]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            content = zf.read(name)
            for needle in needles:
                assert needle not in content, f"secret leaked into zip member {name!r}"


def _create_org(e2e_client, headers: dict[str, str], suffix: str) -> str:
    domain = f"rt-{uuid.uuid4().hex[:8]}-{suffix}.test"
    response = e2e_client.post(
        "/api/organizations",
        headers=headers,
        json={"name": f"RT {suffix.upper()} Org {domain}", "domain": domain},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_shareable_export_installs_complete_solution_into_fresh_org(
    e2e_client, platform_admin, db_session
):
    """One deploy/export/install preserves the critical distribution contracts.

    ``shell_name`` is absent before source deployment and must create an empty
    Integration shell. ``captured_name`` exists globally with credentials, so
    source export must capture its template while scrubbing those credentials.
    The exported ZIP is then installed once into a second org.
    """
    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)
    tok = uuid.uuid4().hex[:8]
    slug = f"rt-src-{tok}"
    app_slug = f"rt-app-{tok}"
    captured_name = f"HaloPSA-{tok}"
    shell_name = f"GhostInteg-{tok}"
    secret = _SECRET_CLIENT_ID.format(tok=tok)
    readme_md = f"# {slug}\n\nConnect **{shell_name}** before running.\n"
    updated_readme_md = (
        f"# {slug}\n\nUpdated connection instructions for **{shell_name}**.\n"
    )

    source_org_id = _create_org(e2e_client, headers, "src")
    target_org_id = _create_org(e2e_client, headers, "target")

    # The capture target is intentionally global and present before the source
    # deploy. Its OAuth credentials must never be serialized into the ZIP.
    captured_integration = Integration(
        id=uuid.uuid4(), name=captured_name, entity_id_name="tenant_id"
    )
    db_session.add(captured_integration)
    await db_session.flush()
    db_session.add(
        IntegrationConfigSchema(
            integration_id=captured_integration.id,
            key="base_url",
            type="string",
            required=True,
            position=0,
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
            integration_id=captured_integration.id,
        )
    )
    await db_session.commit()

    source_response = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": slug,
            "name": slug.upper(),
            "scope": "org",
            "organization_id": source_org_id,
        },
    )
    assert source_response.status_code in (200, 201), source_response.text
    source_id = source_response.json()["id"]

    # Capture scans author-time workflow source under _repo/. Deploy later
    # owns the same row under _solutions/, so stage the capture source with the
    # exact per-install id the deployer derives from its manifest id.
    workflow_manifest_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/wf/main")
    workflow_id = uuid.uuid5(UUID(source_id), str(workflow_manifest_id))
    workflow_path = f"workflows/{slug}_main.py"
    workflow_source = (
        "def run(sdk):\n"
        f'    return sdk.integrations.get("{captured_name}").list()\n'
    )
    workflow_upload = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": workflow_path, "content": workflow_source, "encoding": "utf-8"},
    )
    assert workflow_upload.status_code in (200, 201), workflow_upload.text
    db_session.add(
        Workflow(
            id=workflow_id,
            name="main",
            function_name="run",
            path=workflow_path,
            type="workflow",
            is_active=True,
            solution_id=UUID(source_id),
            organization_id=UUID(source_org_id),
        )
    )
    await db_session.commit()

    # Before the source deploy persists any declarations, export capture scans
    # the author-time workflow and builds the secret-scrubbed template from the
    # configured global Integration.
    capture_export = e2e_client.post(
        f"/api/solutions/{source_id}/export?mode=shareable",
        headers=headers,
        json={},
    )
    assert capture_export.status_code == 200, capture_export.text
    _assert_secret_absent(capture_export.content, secret, b"also-secret")
    capture_declarations = (
        await db_session.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == UUID(source_id)
            )
        )
    ).scalars().all()
    captured_declaration = next(
        (
            declaration
            for declaration in capture_declarations
            if declaration.integration_name == captured_name
        ),
        None,
    )
    assert captured_declaration is not None, capture_declarations
    captured_template = captured_declaration.template

    # One source deploy both creates the absent shell and deploys the entities
    # whose live state is rebuilt into the subsequent shareable ZIP.
    deploy = wait_for_deploy(
        e2e_client,
        e2e_client.post(
            f"/api/solutions/{source_id}/deploy",
            headers=headers,
            json={
                "apps": [
                    {
                        "id": str(uuid.uuid4()),
                        "slug": app_slug,
                        "name": "Round-Trip App",
                        "app_model": "standalone_v2",
                        "dependencies": {},
                        "dist_files": {
                            "index.html": "<html><body>roundtrip</body></html>"
                        },
                        "bin_dist_files": {
                            ASSET_REL: base64.b64encode(TINY_PNG).decode("ascii")
                        },
                    }
                ],
                "workflows": [
                    {
                        "id": str(workflow_manifest_id),
                        "name": "main",
                        "function_name": "run",
                        "path": workflow_path,
                        "content": workflow_source,
                    }
                ],
                "connection_schemas": [
                    {
                        "integration_name": captured_name,
                        "position": 0,
                        "template": captured_template,
                    },
                    {
                        "integration_name": shell_name,
                        "position": 1,
                        "template": {
                            "name": shell_name,
                            "entity_id_name": "tenant_id",
                            "default_entity_id": "common",
                            "config_schema": [
                                {
                                    "key": "base_url",
                                    "type": "string",
                                    "required": True,
                                    "description": None,
                                    "options": None,
                                    "position": 0,
                                }
                            ],
                            "oauth": {
                                "provider_name": f"ghost-{tok}",
                                "display_name": "Ghost",
                                "oauth_flow_type": "authorization_code",
                                "authorization_url": "https://a",
                                "token_url": "https://t",
                                "audience": None,
                                "token_url_defaults": {},
                                "entity_id_source": None,
                                "scopes": [],
                                "redirect_uri": None,
                            },
                        },
                    }
                ],
                "readme": readme_md,
            },
        ),
        headers,
    )
    assert deploy.status_code in (200, 201), deploy.text
    deploy_body = deploy.json()
    assert deploy_body["apps_upserted"] == 1, deploy_body
    assert deploy_body["workflows_upserted"] == 1, deploy_body
    assert deploy_body["integrations_shell_created"] == 1, deploy_body

    # The absent connection became an empty shell with its safe schema.
    shell = (
        await db_session.execute(
            select(Integration).where(Integration.name == shell_name)
        )
    ).scalar_one()
    shell_schema = (
        await db_session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == shell.id
            )
        )
    ).scalars().all()
    assert {schema.key for schema in shell_schema} == {"base_url"}
    shell_oauth = (
        await db_session.execute(
            select(OAuthProvider).where(OAuthProvider.integration_id == shell.id)
        )
    ).scalar_one()
    assert shell_oauth.client_id == ""
    assert shell_oauth.encrypted_client_secret == b""

    # The source deploy sets the initial README, then the interactive write
    # endpoint updates it before that new value travels through the ZIP.
    source_deploy_readme = e2e_client.get(
        f"/api/solutions/{source_id}/readme", headers=headers
    )
    assert source_deploy_readme.status_code == 200, source_deploy_readme.text
    assert source_deploy_readme.json()["readme"] == readme_md

    readme_update = e2e_client.put(
        f"/api/solutions/{source_id}/readme",
        headers=headers,
        json={"readme": updated_readme_md},
    )
    assert readme_update.status_code == 200, readme_update.text

    source_readme = e2e_client.get(
        f"/api/solutions/{source_id}/readme", headers=headers
    )
    assert source_readme.status_code == 200, source_readme.text
    assert source_readme.json()["readme"] == updated_readme_md

    export_response = e2e_client.post(
        f"/api/solutions/{source_id}/export?mode=shareable",
        headers=headers,
        json={},
    )
    assert export_response.status_code == 200, export_response.text
    assert export_response.headers.get("content-type") == "application/zip"
    zip_bytes = export_response.content
    assert zip_bytes, "export returned an empty body"
    _assert_secret_absent(zip_bytes, secret, b"also-secret")

    # Export capture writes a scrubbed declaration for the existing integration.
    source_declarations = (
        await db_session.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == UUID(source_id)
            )
        )
    ).scalars().all()
    captured_declaration = next(
        (
            declaration
            for declaration in source_declarations
            if declaration.integration_name == captured_name
        ),
        None,
    )
    assert captured_declaration is not None, source_declarations
    template = captured_declaration.template
    assert template["name"] == captured_name
    assert {item["key"] for item in template.get("config_schema") or []} == {"base_url"}
    oauth_template = template.get("oauth") or {}
    assert oauth_template.get("provider_name") == f"halopsa-{tok}"
    assert "client_id" not in oauth_template
    assert "encrypted_client_secret" not in oauth_template

    # Source setup reads the declaration persisted by export and the existing
    # global integration state.
    source_setup = e2e_client.get(
        f"/api/solutions/{source_id}/setup", headers=headers
    )
    assert source_setup.status_code == 200, source_setup.text
    source_connections = [
        item for item in source_setup.json()["items"] if item.get("kind") == "connection"
    ]
    captured_setup = next(
        (item for item in source_connections if item.get("key") == captured_name),
        None,
    )
    assert captured_setup is not None, source_connections
    assert captured_setup["is_set"] is True
    assert captured_setup["has_oauth"] is True

    # The shareable artifact carries both the captured connection metadata and
    # README, then one target install exercises the parser and persistence path.
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert ".bifrost/connections.yaml" in names, names
        assert "README.md" in names, names
        assert zf.read("README.md").decode("utf-8") == updated_readme_md

    install = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=upload_headers,
            files={"file": (f"{slug}.zip", zip_bytes, "application/zip")},
            data={"organization_id": target_org_id},
        ),
        headers,
    )
    assert install.status_code in (200, 201), install.text
    installed_id = install.json()["id"]
    assert installed_id != source_id

    installed_detail = e2e_client.get(
        f"/api/solutions/{installed_id}", headers=headers
    )
    assert installed_detail.status_code == 200, installed_detail.text
    assert installed_detail.json()["organization_id"] == target_org_id

    installed_entities = e2e_client.get(
        f"/api/solutions/{installed_id}/entities", headers=headers
    )
    assert installed_entities.status_code == 200, installed_entities.text
    entities = installed_entities.json()
    assert len(entities["apps"]) >= 1, entities
    assert len(entities["workflows"]) >= 1, entities

    asset_response = e2e_client.get(
        f"/api/applications/{entities['apps'][0]['id']}/dist/{ASSET_REL}",
        headers=headers,
    )
    assert asset_response.status_code == 200, asset_response.text
    assert asset_response.content == TINY_PNG

    # Install persists the captured declaration and returns it through setup.
    installed_setup = e2e_client.get(
        f"/api/solutions/{installed_id}/setup", headers=headers
    )
    assert installed_setup.status_code == 200, installed_setup.text
    installed_connections = [
        item
        for item in installed_setup.json()["items"]
        if item.get("kind") == "connection"
    ]
    assert any(item.get("key") == captured_name for item in installed_connections), (
        installed_connections
    )
    installed_declarations = (
        await db_session.execute(
            select(SolutionConnectionSchema).where(
                SolutionConnectionSchema.solution_id == UUID(installed_id)
            )
        )
    ).scalars().all()
    assert any(
        declaration.integration_name == captured_name
        for declaration in installed_declarations
    ), installed_declarations

    installed_readme = e2e_client.get(
        f"/api/solutions/{installed_id}/readme", headers=headers
    )
    assert installed_readme.status_code == 200, installed_readme.text
    assert installed_readme.json()["readme"] == updated_readme_md

    assert (
        await db_session.execute(
            select(Integration).where(Integration.name == captured_name)
        )
    ).scalar_one_or_none() is not None
