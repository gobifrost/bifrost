"""Editor deletion refuses the workspace root and preserves other subtrees."""

from uuid import uuid4

import pytest

from src.services.repo_storage import RepoStorage


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_editor_delete_is_confined_to_selected_folder(e2e_client, platform_admin):
    repo = RepoStorage()
    name = f"e2e-delete-{uuid4().hex}"
    child = f"{name}/nested/file.txt"
    sibling = f"{name}-other/keep.txt"
    manifest = f".bifrost/{name}.yaml"
    objects = {child: b"remove", sibling: b"keep", manifest: b"keep manifest"}
    try:
        for path, content in objects.items():
            await repo.write(path, content)

        for target in ("", "/"):
            response = e2e_client.delete(
                "/api/files/editor", params={"path": target},
                headers=platform_admin.headers,
            )
            assert response.status_code == 400, response.text
        for path, content in objects.items():
            assert await repo.read(path) == content

        response = e2e_client.delete(
            "/api/files/editor", params={"path": f"{name}/"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 204, response.text
        assert await repo.list(f"{name}/") == []
        assert await repo.read(sibling) == objects[sibling]
        assert await repo.read(manifest) == objects[manifest]

        response = e2e_client.get(
            "/api/files/editor", params={"path": "."}, headers=platform_admin.headers,
        )
        assert response.status_code == 200, response.text
    finally:
        for path in objects:
            await repo.delete(path)
