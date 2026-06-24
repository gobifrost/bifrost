from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.file_metadata import FileMetadata
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.tables import Table
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.file_policy_helpers import grant_file_policy
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str, org_id: str | None = None) -> dict:
    response = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": org_id},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def _declare_file_location(db_session, solution_id: str, location: str) -> None:
    db_session.add(
        SolutionFileLocation(solution_id=UUID(solution_id), location=location)
    )
    await db_session.commit()


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


async def _repo_table_by_name(db_session, name: str) -> Table | None:
    result = await db_session.execute(
        select(Table).where(Table.name == name, Table.solution_id.is_(None))
    )
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_solution_write_to_declared_file_location_succeeds(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"decl-file-{uuid.uuid4().hex[:8]}",
        org_id=org1["id"],
    )
    solution_id = solution["id"]
    await _declare_file_location(db_session, solution_id, "finance")
    grant_file_policy(
        e2e_client,
        headers,
        location="finance",
        scope=org1["id"],
        prefix="",
        allow_all=True,
    )

    path = f"declared/{uuid.uuid4().hex}.txt"
    response = e2e_client.post(
        f"/api/files/write?solution={solution_id}",
        headers=headers,
        json={
            "location": "finance",
            "path": path,
            "content": "declared write",
            "mode": "cloud",
        },
    )

    assert response.status_code == 204, response.text
    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(solution_id),
            FileMetadata.location == "finance",
            FileMetadata.path == path,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_solution_write_to_undeclared_file_location_returns_404_without_metadata(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"undecl-file-{uuid.uuid4().hex[:8]}",
        org_id=org1["id"],
    )
    solution_id = solution["id"]
    grant_file_policy(
        e2e_client,
        headers,
        location="finance",
        scope=org1["id"],
        prefix="",
        allow_all=True,
    )

    path = f"undeclared/{uuid.uuid4().hex}.txt"
    response = e2e_client.post(
        f"/api/files/write?solution={solution_id}",
        headers=headers,
        json={
            "location": "finance",
            "path": path,
            "content": "must not persist",
            "mode": "cloud",
        },
    )

    assert response.status_code == 404, response.text
    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(solution_id),
            FileMetadata.location == "finance",
            FileMetadata.path == path,
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_non_solution_custom_file_location_write_still_succeeds(
    e2e_client,
    platform_admin,
    db_session,
):
    headers = platform_admin.headers
    grant_file_policy(
        e2e_client,
        headers,
        location="finance",
        scope="global",
        prefix="",
        allow_all=True,
    )

    path = f"custom/{uuid.uuid4().hex}.txt"
    response = e2e_client.post(
        "/api/files/write",
        headers=headers,
        json={
            "location": "finance",
            "scope": "global",
            "path": path,
            "content": "plain custom write",
            "mode": "cloud",
        },
    )

    assert response.status_code == 204, response.text
    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id.is_(None),
            FileMetadata.location == "finance",
            FileMetadata.path == path,
        )
    )
    assert result.scalar_one_or_none() is not None


def test_solution_table_insert_declared_succeeds(e2e_client, platform_admin):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"decl-table-{uuid.uuid4().hex[:8]}",
    )
    solution_id = solution["id"]
    table_name = f"declared_{uuid.uuid4().hex[:8]}"
    _deploy_table(e2e_client, headers, solution_id, table_name)

    response = e2e_client.post(
        f"/api/tables/{table_name}/documents?solution={solution_id}",
        headers=headers,
        json={"id": "row-1", "data": {"label": "ok"}},
    )

    assert response.status_code in (200, 201), response.text
    assert response.json()["data"]["label"] == "ok"


@pytest.mark.asyncio
async def test_solution_table_insert_undeclared_returns_404_without_repo_table(
    e2e_client,
    platform_admin,
    db_session,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"undecl-insert-{uuid.uuid4().hex[:8]}",
    )
    solution_id = solution["id"]
    table_name = f"undeclared_insert_{uuid.uuid4().hex[:8]}"

    response = e2e_client.post(
        f"/api/tables/{table_name}/documents?solution={solution_id}",
        headers=headers,
        json={"id": "row-1", "data": {"label": "blocked"}},
    )

    assert response.status_code == 404, response.text
    assert await _repo_table_by_name(db_session, table_name) is None


@pytest.mark.asyncio
async def test_solution_table_upsert_undeclared_returns_404_without_repo_table(
    e2e_client,
    platform_admin,
    db_session,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"undecl-upsert-{uuid.uuid4().hex[:8]}",
    )
    solution_id = solution["id"]
    table_name = f"undeclared_upsert_{uuid.uuid4().hex[:8]}"

    response = e2e_client.post(
        f"/api/tables/{table_name}/documents/upsert?solution={solution_id}",
        headers=headers,
        json={"id": "row-1", "data": {"label": "blocked"}},
    )

    assert response.status_code == 404, response.text
    assert await _repo_table_by_name(db_session, table_name) is None


@pytest.mark.asyncio
async def test_solution_table_insert_undeclared_does_not_fall_back_to_repo_table(
    e2e_client,
    platform_admin,
    db_session,
):
    headers = platform_admin.headers
    solution = _create_solution(
        e2e_client,
        headers,
        f"undecl-repo-{uuid.uuid4().hex[:8]}",
    )
    solution_id = solution["id"]
    table_name = f"repo_same_name_{uuid.uuid4().hex[:8]}"

    repo_create = e2e_client.post(
        "/api/tables?scope=global",
        headers=headers,
        json={"name": table_name, "schema": {"columns": [{"name": "label"}]}},
    )
    assert repo_create.status_code in (200, 201), repo_create.text

    response = e2e_client.post(
        f"/api/tables/{table_name}/documents?solution={solution_id}",
        headers=headers,
        json={"id": "row-1", "data": {"label": "must not hit repo"}},
    )

    assert response.status_code == 404, response.text
    repo_table = await _repo_table_by_name(db_session, table_name)
    assert repo_table is not None
    row = e2e_client.get(
        f"/api/tables/{repo_table.id}/documents/row-1",
        headers=headers,
    )
    assert row.status_code == 404, row.text


@pytest.mark.asyncio
async def test_non_solution_table_insert_still_auto_creates(
    cli_client,
    db_session,
):
    from bifrost import tables

    table_name = f"plain_auto_{uuid.uuid4().hex[:8]}"
    doc = await tables.insert(table_name, {"label": "plain"}, scope="global")

    assert doc.data["label"] == "plain"
    repo_table = await _repo_table_by_name(db_session, table_name)
    assert repo_table is not None
    assert repo_table.solution_id is None
