from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.tables import Document, Table
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(
    e2e_client,
    headers,
    slug: str,
    *,
    org_id: str | None = None,
    global_repo_access: bool = False,
) -> dict:
    response = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": slug,
            "name": slug.upper(),
            "organization_id": org_id,
            "global_repo_access": global_repo_access,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _deploy_table(e2e_client, headers, solution_id: str, table_name: str) -> str:
    manifest_id = str(uuid.uuid4())
    response = e2e_client.post(
        f"/api/solutions/{solution_id}/deploy",
        headers=headers,
        json={
            "tables": [
                {
                    "id": manifest_id,
                    "name": table_name,
                    "schema": {"columns": [{"name": "label"}]},
                    "policies": None,
                }
            ],
        },
    )
    deployed = wait_for_deploy(e2e_client, response, headers)
    assert deployed.status_code in (200, 201), deployed.text
    return str(solution_entity_id(UUID(solution_id), UUID(manifest_id)))


def _create_repo_table(
    e2e_client,
    headers,
    table_name: str,
    *,
    scope: str,
) -> str:
    response = e2e_client.post(
        f"/api/tables?scope={scope}",
        headers=headers,
        json={"name": table_name, "schema": {"columns": [{"name": "label"}]}},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _insert_row(
    e2e_client,
    headers,
    table_id: str,
    row_id: str,
    label: str,
) -> None:
    response = e2e_client.post(
        f"/api/tables/{table_id}/documents",
        headers=headers,
        json={"id": row_id, "data": {"label": label}},
    )
    assert response.status_code in (200, 201), response.text


def _query_labels(
    e2e_client,
    headers,
    table_name: str,
    *,
    solution_id: str,
    scope: str,
) -> list[str]:
    response = e2e_client.post(
        f"/api/tables/{table_name}/documents/query?solution={solution_id}&scope={scope}",
        headers=headers,
        json={"where": {}, "order_by": "label"},
    )
    assert response.status_code == 200, response.text
    return [row["data"]["label"] for row in response.json()["documents"]]


async def _repo_table_by_name(db_session, name: str) -> Table | None:
    result = await db_session.execute(
        select(Table).where(Table.name == name, Table.solution_id.is_(None))
    )
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_open_solution_reads_own_then_org_then_global_tables_by_name(
    e2e_client,
    platform_admin,
    org1,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"open-tables-{uuid.uuid4().hex[:8]}",
        org_id=org1["id"],
        global_repo_access=True,
    )
    solution_id = solution["id"]

    own_name = f"open_own_{uuid.uuid4().hex[:8]}"
    own_table_id = _deploy_table(e2e_client, headers, solution_id, own_name)
    org_shadow_id = _create_repo_table(
        e2e_client, headers, own_name, scope=org1["id"]
    )
    global_shadow_id = _create_repo_table(e2e_client, headers, own_name, scope="global")
    _insert_row(e2e_client, headers, own_table_id, "row", "own")
    _insert_row(e2e_client, headers, org_shadow_id, "row", "org-shadow")
    _insert_row(e2e_client, headers, global_shadow_id, "row", "global-shadow")

    assert _query_labels(
        e2e_client, headers, own_name, solution_id=solution_id, scope=org1["id"]
    ) == ["own"]

    org_name = f"open_org_{uuid.uuid4().hex[:8]}"
    org_table_id = _create_repo_table(e2e_client, headers, org_name, scope=org1["id"])
    global_table_id = _create_repo_table(e2e_client, headers, org_name, scope="global")
    _insert_row(e2e_client, headers, org_table_id, "row", "org")
    _insert_row(e2e_client, headers, global_table_id, "row", "global-shadow")

    assert _query_labels(
        e2e_client, headers, org_name, solution_id=solution_id, scope=org1["id"]
    ) == ["org"]

    global_name = f"open_global_{uuid.uuid4().hex[:8]}"
    global_only_id = _create_repo_table(e2e_client, headers, global_name, scope="global")
    _insert_row(e2e_client, headers, global_only_id, "row", "global")

    assert _query_labels(
        e2e_client, headers, global_name, solution_id=solution_id, scope=org1["id"]
    ) == ["global"]


def test_sealed_solution_reads_only_own_table_by_name(
    e2e_client,
    platform_admin,
    org1,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"sealed-tables-{uuid.uuid4().hex[:8]}",
        org_id=org1["id"],
        global_repo_access=False,
    )
    solution_id = solution["id"]

    own_name = f"sealed_own_{uuid.uuid4().hex[:8]}"
    own_table_id = _deploy_table(e2e_client, headers, solution_id, own_name)
    _insert_row(e2e_client, headers, own_table_id, "row", "own")
    assert _query_labels(
        e2e_client, headers, own_name, solution_id=solution_id, scope=org1["id"]
    ) == ["own"]

    org_name = f"sealed_org_{uuid.uuid4().hex[:8]}"
    org_table_id = _create_repo_table(e2e_client, headers, org_name, scope=org1["id"])
    _insert_row(e2e_client, headers, org_table_id, "row", "org")
    org_response = e2e_client.post(
        f"/api/tables/{org_name}/documents/query?solution={solution_id}&scope={org1['id']}",
        headers=headers,
        json={"where": {}},
    )
    assert org_response.status_code == 404, org_response.text

    global_name = f"sealed_global_{uuid.uuid4().hex[:8]}"
    global_table_id = _create_repo_table(e2e_client, headers, global_name, scope="global")
    _insert_row(e2e_client, headers, global_table_id, "row", "global")
    global_response = e2e_client.post(
        f"/api/tables/{global_name}/documents/query?solution={solution_id}&scope={org1['id']}",
        headers=headers,
        json={"where": {}},
    )
    assert global_response.status_code == 404, global_response.text


@pytest.mark.asyncio
async def test_solution_context_missing_table_name_404s_without_auto_create(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"missing-table-{uuid.uuid4().hex[:8]}",
        org_id=org1["id"],
        global_repo_access=True,
    )
    solution_id = solution["id"]
    table_name = f"missing_{uuid.uuid4().hex[:8]}"

    response = e2e_client.post(
        f"/api/tables/{table_name}/documents?solution={solution_id}&scope={org1['id']}",
        headers=headers,
        json={"id": "row-1", "data": {"label": "must not persist"}},
    )

    assert response.status_code == 404, response.text
    assert await _repo_table_by_name(db_session, table_name) is None


@pytest.mark.asyncio
async def test_open_solution_cannot_write_to_org_or_global_fallback_table(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"fallback-write-{uuid.uuid4().hex[:8]}",
        org_id=org1["id"],
        global_repo_access=True,
    )
    solution_id = solution["id"]
    table_name = f"fallback_write_{uuid.uuid4().hex[:8]}"
    table_id = _create_repo_table(e2e_client, headers, table_name, scope=org1["id"])
    _insert_row(e2e_client, headers, table_id, "existing", "existing")

    response = e2e_client.post(
        f"/api/tables/{table_name}/documents?solution={solution_id}&scope={org1['id']}",
        headers=headers,
        json={"id": "blocked", "data": {"label": "blocked"}},
    )

    assert response.status_code in (403, 404), response.text
    repo_table = await _repo_table_by_name(db_session, table_name)
    assert repo_table is not None
    result = await db_session.execute(
        select(Document).where(
            Document.table_id == repo_table.id,
            Document.id == "blocked",
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_non_solution_table_auto_create_still_works(cli_client, db_session):
    from bifrost import tables

    table_name = f"plain_auto_{uuid.uuid4().hex[:8]}"
    doc = await tables.insert(table_name, {"label": "plain"}, scope="global")

    assert doc.data["label"] == "plain"
    repo_table = await _repo_table_by_name(db_session, table_name)
    assert repo_table is not None
    assert repo_table.solution_id is None
