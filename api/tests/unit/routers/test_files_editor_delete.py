"""Destructive editor paths must never expand into a workspace-root listing."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.routers import files
from src.services.repo_storage import RepoStorage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "", "/", "///", ".", "./", "..", "../outside", "folder/..",
        "folder/../other", "folder/./file", "folder//file", "folder//",
        "/folder", "\\folder", "folder\\file", "C:/repo", "C:repo",
        "folder\x00/file", ".git", ".git/config", "folder/.env",
    ],
)
async def test_invalid_delete_target_rejected_before_storage(monkeypatch, path):
    monkeypatch.setattr(files, "authorize_operation", AsyncMock())
    storage_factory = MagicMock()
    repo_factory = MagicMock()
    monkeypatch.setattr("src.services.file_storage.FileStorageService", storage_factory)
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", repo_factory)
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await files.delete_file_editor(MagicMock(), MagicMock(), path=path, db=db)

    assert exc.value.status_code == 400
    storage_factory.assert_not_called()
    repo_factory.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["folder", "folder/"])
async def test_folder_delete_preserves_siblings_and_manifests(monkeypatch, path):
    monkeypatch.setattr(files, "authorize_operation", AsyncMock())
    objects = {"_repo/folder/child.txt", "_repo/folder/", "_repo/folder"}
    retained = {"_repo/folder-other/keep.txt", "_repo/.bifrost/forms.yaml"}
    objects.update(retained)
    repo = RepoStorage()

    async def list_objects(prefix):
        key = repo._repo_key(prefix)
        return [p.removeprefix("_repo/") for p in objects if p.startswith(key)]

    async def delete_object(target):
        objects.discard(repo._repo_key(target))

    monkeypatch.setattr(repo, "list", list_objects)
    monkeypatch.setattr(repo, "delete", delete_object)
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", lambda: repo)
    storage = MagicMock()
    storage.delete_file = AsyncMock(side_effect=delete_object)
    monkeypatch.setattr("src.services.file_storage.FileStorageService", lambda _db: storage)
    db = AsyncMock()

    await files.delete_file_editor(MagicMock(), MagicMock(), path=path, db=db)

    assert objects == retained
    storage.delete_file.assert_awaited_once_with("folder/child.txt")
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["folder/file.txt", "folder/file.txt/"])
async def test_single_file_delete_uses_canonical_target(monkeypatch, path):
    monkeypatch.setattr(files, "authorize_operation", AsyncMock())
    repo = MagicMock()
    repo.list = AsyncMock(return_value=[])
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", lambda: repo)
    storage = MagicMock()
    storage.delete_file = AsyncMock()
    monkeypatch.setattr("src.services.file_storage.FileStorageService", lambda _db: storage)

    await files.delete_file_editor(MagicMock(), MagicMock(), path=path, db=AsyncMock())

    storage.delete_file.assert_awaited_once_with("folder/file.txt")


@pytest.mark.asyncio
async def test_editor_delete_keeps_authorization_gate(monkeypatch):
    monkeypatch.setattr(
        files, "authorize_operation",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden")),
    )
    storage_factory = MagicMock()
    monkeypatch.setattr("src.services.file_storage.FileStorageService", storage_factory)

    with pytest.raises(HTTPException) as exc:
        await files.delete_file_editor(MagicMock(), MagicMock(), path="folder", db=AsyncMock())

    assert exc.value.status_code == 403
    storage_factory.assert_not_called()


@pytest.mark.asyncio
async def test_editor_delete_does_not_commit_after_required_cleanup_failure(monkeypatch):
    monkeypatch.setattr(files, "authorize_operation", AsyncMock())
    repo = MagicMock()
    repo.list = AsyncMock(return_value=[])
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", lambda: repo)
    storage = MagicMock()
    storage.delete_file = AsyncMock(side_effect=RuntimeError("retirement failed"))
    monkeypatch.setattr("src.services.file_storage.FileStorageService", lambda _db: storage)
    db = AsyncMock()

    with pytest.raises(RuntimeError, match="retirement failed"):
        await files.delete_file_editor(
            MagicMock(), MagicMock(), path="workflow.py", db=db,
        )

    db.commit.assert_not_awaited()
