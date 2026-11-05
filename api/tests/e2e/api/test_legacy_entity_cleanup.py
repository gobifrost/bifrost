"""Entity mutations remove retired companion files from real storage and search."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm.file_index import FileIndex
from src.services.file_index_service import FileIndexService
from src.services.repo_storage import RepoStorage


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize(("entities", "entity_type"), [("forms", "form"), ("agents", "agent")])
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_entity_mutation_removes_only_its_legacy_file(
    e2e_client, platform_admin, db_session, entities: str, entity_type: str, operation: str,
) -> None:
    payload: dict = {"name": f"Legacy Cleanup {uuid4().hex}", "access_level": "authenticated"}
    if entity_type == "agent":
        payload["system_prompt"] = "You are a test assistant."
    else:
        payload["form_schema"] = {"fields": []}
    response = e2e_client.post(
        f"/api/{entities}",
        headers=platform_admin.headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    entity_id = response.json()["id"]
    endpoint = f"/api/{entities}/{entity_id}"
    legacy_path = f"{entities}/{entity_id}.{entity_type}.yaml"
    ordinary_path = f"{entities}/runbook-{uuid4().hex}.{entity_type}.yaml"
    storage = RepoStorage()
    index = FileIndexService(db_session, storage)
    paths = [legacy_path, ordinary_path]
    try:
        for path in paths:
            await index.write(path, b"preserved content\n")
        await db_session.commit()
        assert set((await db_session.scalars(
            select(FileIndex.path).where(FileIndex.path.in_(paths))
        )).all()) == set(paths)
        assert legacy_path in await storage.list(f"{entities}/")

        if operation == "update":
            response = e2e_client.put(
                endpoint, headers=platform_admin.headers, json={"name": "Updated Entity"},
            )
            assert response.status_code == 200, response.text
        else:
            response = e2e_client.delete(endpoint, headers=platform_admin.headers)
            assert response.status_code == 204, response.text

        assert await storage.list(legacy_path) == []
        assert await storage.read(ordinary_path) == b"preserved content\n"
        indexed = dict((await db_session.execute(
            select(FileIndex.path, FileIndex.content).where(FileIndex.path.in_(paths))
        )).tuples().all())
        assert indexed == {ordinary_path: "preserved content\n"}
    finally:
        await db_session.rollback()
        for path in paths:
            await index.delete(path)
        await db_session.commit()
        response = e2e_client.delete(endpoint, headers=platform_admin.headers)
        assert response.status_code in (204, 404), response.text
        if entity_type == "form":
            response = e2e_client.delete(
                endpoint, headers=platform_admin.headers, params={"purge": True},
            )
            assert response.status_code == 204, response.text
