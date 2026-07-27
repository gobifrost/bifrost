"""Live runtime coverage for Solution access to shared instance resources."""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from uuid import UUID

import pytest

from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(
    e2e_client,
    headers: dict[str, str],
    *,
    slug: str,
    global_repo_access: bool,
) -> str:
    response = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": slug,
            "name": slug.upper(),
            "organization_id": None,
            "global_repo_access": global_repo_access,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _probe_workspace_zip(
    *,
    slug: str,
    global_repo_access: bool,
    table_name: str,
    config_key: str,
    integration_name: str,
    app_manifest_id: str,
    workflow_manifest_id: str,
) -> bytes:
    source = (
        "from bifrost import config, integrations, tables, workflow\n\n"
        "@workflow\n"
        "async def probe():\n"
        f"    docs = await tables.query({table_name!r}, limit=10)\n"
        f"    config_value = await config.get({config_key!r})\n"
        f"    integration = await integrations.get({integration_name!r})\n"
        "    return {\n"
        "        'rows': [doc.data for doc in docs.documents],\n"
        "        'config': config_value,\n"
        "        'integration_entity': integration.entity_id if integration else None,\n"
        "    }\n"
    )
    workflows = {
        "workflows": {
            workflow_manifest_id: {
                "id": workflow_manifest_id,
                "name": f"probe_{slug}",
                "function_name": "probe",
                "path": "workflows/probe.py",
                "type": "workflow",
                "access_level": "authenticated",
            }
        }
    }
    apps = {
        "apps": {
            app_manifest_id: {
                "id": app_manifest_id,
                "slug": f"app-{slug}",
                "name": f"App {slug}",
                "path": f"apps/{slug}",
                "app_model": "standalone_v2",
                "access_level": "authenticated",
                "dependencies": {},
                "dist_files": {
                    "index.html": "<!doctype html><div id=\"root\"></div>"
                },
            }
        }
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "bifrost.solution.yaml",
            (
                f"slug: {slug}\n"
                f"name: {slug.upper()}\n"
                "scope: global\n"
                f"global_repo_access: {str(global_repo_access).lower()}\n"
            ),
        )
        archive.writestr(
            ".bifrost/workflows.yaml",
            json.dumps(workflows),
        )
        archive.writestr(".bifrost/apps.yaml", json.dumps(apps))
        archive.writestr("workflows/probe.py", source)
    return buffer.getvalue()


def _deploy_workspace(
    e2e_client,
    headers: dict[str, str],
    *,
    solution_id: str,
    slug: str,
    workspace_zip: bytes,
) -> None:
    upload_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() != "content-type"
    }
    response = e2e_client.post(
        f"/api/solutions/{solution_id}/deploy",
        headers=upload_headers,
        files={
            "file": (
                f"{slug}.zip",
                workspace_zip,
                "application/zip",
            )
        },
    )
    result = wait_for_deploy(e2e_client, response, headers)
    assert result.status_code == 200, result.text


def _create_shared_resources(
    e2e_client,
    headers: dict[str, str],
    *,
    table_name: str,
    config_key: str,
    integration_name: str,
) -> None:
    table = e2e_client.post(
        "/api/tables?scope=global",
        headers=headers,
        json={
            "name": table_name,
            "schema": {"columns": [{"name": "marker", "type": "string"}]},
            "policies": {
                "policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                    {
                        "name": "authenticated_read",
                        "actions": ["read"],
                        "when": None,
                    },
                ]
            },
        },
    )
    assert table.status_code == 201, table.text
    row = e2e_client.post(
        f"/api/tables/{table.json()['id']}/documents",
        headers=headers,
        json={"id": "shared-row", "data": {"marker": "table-ok"}},
    )
    assert row.status_code == 201, row.text

    config = e2e_client.post(
        "/api/config",
        headers=headers,
        json={
            "key": config_key,
            "value": "config-ok",
            "type": "string",
            "organization_id": None,
        },
    )
    assert config.status_code == 201, config.text

    integration = e2e_client.post(
        "/api/integrations",
        headers=headers,
        json={
            "name": integration_name,
            "default_entity_id": "integration-ok",
        },
    )
    assert integration.status_code == 201, integration.text


@pytest.mark.parametrize(
    ("global_repo_access", "expected_app_status", "expected_rows"),
    [
        (True, 200, [{"marker": "table-ok"}]),
        (False, 404, []),
    ],
)
def test_solution_app_and_workflow_shared_resource_matrix(
    e2e_client,
    platform_admin,
    alice_user,
    global_repo_access: bool,
    expected_app_status: int,
    expected_rows: list[dict[str, str]],
) -> None:
    """Exercise the real web-SDK transport and a deployed Python workflow.

    Tables are Solution-scope-capable, so the flag gates their shared fallback.
    Config and integration values have no Solution-owned value tier and remain
    shared instance state regardless of the flag.
    """
    token = uuid.uuid4().hex[:8]
    slug = f"shared-access-{token}"
    table_name = f"shared_table_{token}"
    config_key = f"shared_config_{token}"
    integration_name = f"shared_integration_{token}"
    app_manifest_id = str(uuid.uuid4())
    workflow_manifest_id = str(uuid.uuid4())

    _create_shared_resources(
        e2e_client,
        platform_admin.headers,
        table_name=table_name,
        config_key=config_key,
        integration_name=integration_name,
    )
    solution_id = _create_solution(
        e2e_client,
        platform_admin.headers,
        slug=slug,
        global_repo_access=global_repo_access,
    )
    _deploy_workspace(
        e2e_client,
        platform_admin.headers,
        solution_id=solution_id,
        slug=slug,
        workspace_zip=_probe_workspace_zip(
            slug=slug,
            global_repo_access=global_repo_access,
            table_name=table_name,
            config_key=config_key,
            integration_name=integration_name,
            app_manifest_id=app_manifest_id,
            workflow_manifest_id=workflow_manifest_id,
        ),
    )

    app_id = str(
        solution_entity_id(UUID(solution_id), UUID(app_manifest_id))
    )
    # BifrostProvider's data transport attaches X-Bifrost-App to this exact
    # request. Use a non-admin plus an unconditional read policy so a 200 proves
    # both Solution resolution and ordinary row-policy authorization.
    app_query = e2e_client.post(
        f"/api/tables/{table_name}/documents/query",
        headers={**alice_user.headers, "X-Bifrost-App": app_id},
        json={"where": {}, "limit": 10},
    )
    assert app_query.status_code == expected_app_status, app_query.text
    if expected_app_status == 200:
        assert [doc["data"] for doc in app_query.json()["documents"]] == expected_rows

    workflow = e2e_client.post(
        "/api/workflows/execute",
        headers=alice_user.headers,
        json={
            "workflow_id": "workflows/probe.py::probe",
            "solution_id": solution_id,
            "sync": True,
        },
    )
    assert workflow.status_code == 200, workflow.text
    execution = workflow.json()
    assert execution["status"] == "Success", execution
    assert execution["result"] == {
        "rows": expected_rows,
        "config": "config-ok",
        "integration_entity": "integration-ok",
    }


@pytest.mark.parametrize(
    ("global_repo_access", "expected_status"),
    [(True, 200), (False, 404)],
)
def test_solution_shared_workflow_fallback_obeys_global_repo_access(
    e2e_client,
    platform_admin,
    alice_user,
    global_repo_access: bool,
    expected_status: int,
) -> None:
    """A scoped caller may execute a loose workflow only from an open Solution."""
    token = uuid.uuid4().hex[:8]
    path = f"workflows/shared_{token}.py"
    function_name = f"shared_{token}"
    source = (
        "from bifrost import workflow\n\n"
        "@workflow\n"
        f"async def {function_name}():\n"
        "    return {'shared_workflow': 'executed'}\n"
    )
    write = e2e_client.put(
        "/api/files/editor/content",
        headers=platform_admin.headers,
        json={"path": path, "content": source, "encoding": "utf-8"},
    )
    assert write.status_code in (200, 201), write.text
    register = e2e_client.post(
        "/api/workflows/register",
        headers=platform_admin.headers,
        json={
            "path": path,
            "function_name": function_name,
            "organization_id": None,
            "access_level": "authenticated",
        },
    )
    assert register.status_code in (200, 201), register.text

    solution_id = _create_solution(
        e2e_client,
        platform_admin.headers,
        slug=f"shared-workflow-{token}",
        global_repo_access=global_repo_access,
    )
    response = e2e_client.post(
        "/api/workflows/execute",
        headers=alice_user.headers,
        json={
            "workflow_id": f"{path}::{function_name}",
            "solution_id": solution_id,
            "sync": True,
        },
    )
    assert response.status_code == expected_status, response.text
    if expected_status == 200:
        execution = response.json()
        assert execution["status"] == "Success", execution
        assert execution["result"] == {"shared_workflow": "executed"}
