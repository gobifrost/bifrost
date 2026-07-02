"""Capstone E2E: deployed Solution storage survives execute -> export -> install.

This test intentionally uses the production-facing surfaces in sequence:

1. Deploy a solution with a declared file location, table, and workflow.
2. Execute the workflow with ``solution_id`` so the Python SDK scopes files and
   tables to the install.
3. Export a full backup with runtime table rows and encrypted file payloads.
4. Install that ZIP into a fresh org and verify both data classes survive.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.file_metadata import FileMetadata
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.file_policy_helpers import grant_file_policy
from tests.e2e.platform.conftest import deploy_solution, wait_for_install

pytestmark = pytest.mark.e2e


LOCATION = "finance"
FILE_PATH = "reports/q1.csv"
FILE_CONTENT = "account,total\nnorth,42\n"


def _upload_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _create_org(e2e_client, headers: dict[str, str], prefix: str) -> str:
    token = uuid.uuid4().hex[:8]
    response = e2e_client.post(
        "/api/organizations",
        headers=headers,
        json={"name": f"{prefix} {token}", "domain": f"{prefix}-{token}.test"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_solution(
    e2e_client,
    headers: dict[str, str],
    *,
    slug: str,
    org_id: str,
) -> str:
    response = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": org_id},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _workflow_source(table_name: str) -> str:
    return (
        "from bifrost import files, tables, workflow\n\n"
        "@workflow\n"
        "async def run():\n"
        f"    await files.write({FILE_PATH!r}, {FILE_CONTENT!r}, location={LOCATION!r})\n"
        f"    file_body = await files.read({FILE_PATH!r}, location={LOCATION!r})\n"
        f"    await tables.upsert({table_name!r}, id='sdk-row', data={{'account': 'north', 'total': 42}})\n"
        f"    docs = await tables.query({table_name!r}, where={{'account': 'north'}}, limit=10)\n"
        "    return {\n"
        "        'file_body': file_body,\n"
        "        'total': docs.total,\n"
        "        'rows': [doc.data for doc in docs.documents],\n"
        "    }\n"
    )


def _execute_solution_workflow(
    e2e_client,
    headers: dict[str, str],
    *,
    solution_id: str,
    org_id: str,
) -> dict:
    response = e2e_client.post(
        "/api/workflows/execute",
        headers=headers,
        json={
            "workflow_id": "workflows/finance.py::run",
            "solution_id": solution_id,
            "org_id": org_id,
            "sync": True,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "Success", result
    return result["result"]


def _query_table_rows(e2e_client, headers: dict[str, str], table_id: str) -> list[dict]:
    response = e2e_client.post(
        f"/api/tables/{table_id}/documents/query",
        headers=headers,
        json={"limit": 20},
    )
    assert response.status_code == 200, response.text
    return response.json()["documents"]


def _read_solution_file(
    e2e_client,
    headers: dict[str, str],
    *,
    solution_id: str,
    org_id: str,
) -> str:
    response = e2e_client.post(
        f"/api/files/read?solution={solution_id}",
        headers=headers,
        json={
            "path": FILE_PATH,
            "location": LOCATION,
            "mode": "cloud",
            "binary": False,
            "scope": org_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["content"]


async def _assert_file_metadata(db_session, *, solution_id: str) -> None:
    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(solution_id),
            FileMetadata.location == LOCATION,
            FileMetadata.path == FILE_PATH,
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.organization_id != UUID(solution_id)
    assert row.size_bytes == len(FILE_CONTENT.encode("utf-8"))
    assert row.s3_key == f"{LOCATION}/{solution_id}/{FILE_PATH}"


async def test_deployed_solution_storage_round_trips_through_full_export_install(
    e2e_client,
    platform_admin,
    db_session,
):
    headers = platform_admin.headers
    src_org_id = _create_org(e2e_client, headers, "storage-src")
    target_org_id = _create_org(e2e_client, headers, "storage-target")
    slug = f"storage-full-{uuid.uuid4().hex[:8]}"
    source_sid = _create_solution(e2e_client, headers, slug=slug, org_id=src_org_id)
    table_manifest_id = str(uuid.uuid4())
    workflow_manifest_id = str(uuid.uuid4())
    table_name = f"finance_rows_{uuid.uuid4().hex[:8]}"

    grant_file_policy(
        e2e_client,
        headers,
        location=LOCATION,
        scope=src_org_id,
        allow_all=True,
    )
    grant_file_policy(
        e2e_client,
        headers,
        location=LOCATION,
        scope=target_org_id,
        allow_all=True,
    )

    deploy = deploy_solution(
        e2e_client,
        source_sid,
        headers,
        {
            "file_locations": [LOCATION],
            "python_files": {"workflows/finance.py": _workflow_source(table_name)},
            "workflows": [
                {
                    "id": workflow_manifest_id,
                    "name": f"finance_{slug}",
                    "function_name": "run",
                    "path": "workflows/finance.py",
                    "type": "workflow",
                }
            ],
            "tables": [
                {
                    "id": table_manifest_id,
                    "name": table_name,
                    "schema": {
                        "columns": [
                            {"name": "account"},
                            {"name": "total"},
                        ]
                    },
                    "policies": None,
                }
            ],
        },
    )
    assert deploy.status_code == 200, deploy.text

    workflow_result = _execute_solution_workflow(
        e2e_client,
        headers,
        solution_id=source_sid,
        org_id=src_org_id,
    )
    assert workflow_result == {
        "file_body": FILE_CONTENT,
        "total": 1,
        "rows": [{"account": "north", "total": 42}],
    }
    await _assert_file_metadata(db_session, solution_id=source_sid)

    source_table_id = str(solution_entity_id(UUID(source_sid), UUID(table_manifest_id)))
    source_rows = _query_table_rows(e2e_client, headers, source_table_id)
    assert [row["data"] for row in source_rows] == [{"account": "north", "total": 42}]

    exported = e2e_client.post(
        f"/api/solutions/{source_sid}/export?mode=full&include_data=true",
        headers=headers,
        json={"password": "pw-storage"},
    )
    assert exported.status_code == 200, exported.text

    installed = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=_upload_headers(headers),
            files={"file": ("storage.zip", exported.content, "application/zip")},
            data={"organization_id": target_org_id, "password": "pw-storage"},
        ),
        headers,
    )
    assert installed.status_code in (200, 201), installed.text
    target_sid = installed.json()["id"]

    assert _read_solution_file(
        e2e_client,
        headers,
        solution_id=target_sid,
        org_id=target_org_id,
    ) == FILE_CONTENT
    await _assert_file_metadata(db_session, solution_id=target_sid)

    target_table_id = str(solution_entity_id(UUID(target_sid), UUID(table_manifest_id)))
    target_rows = _query_table_rows(e2e_client, headers, target_table_id)
    assert [row["data"] for row in target_rows] == [{"account": "north", "total": 42}]
