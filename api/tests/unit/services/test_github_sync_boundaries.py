"""Focused git-sync boundary tests."""

import pytest


def _import_github_sync_service(monkeypatch):
    """Import github_sync without requiring GitPython in host-only test runs."""
    import sys
    import types

    fake_git = types.ModuleType("git")
    fake_git.Repo = object
    monkeypatch.setitem(sys.modules, "git", fake_git)

    from src.services.github_sync import GitHubSyncService

    return GitHubSyncService


class _Rows:
    def all(self):
        return [
            ("workflows/old.py", "old-hash"),
            ("workflows/other-workspace.py", "other-hash"),
        ]


class _FakeDb:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows()


@pytest.mark.asyncio
async def test_file_index_cleanup_requires_explicit_git_deleted_paths(tmp_path, monkeypatch):
    GitHubSyncService = _import_github_sync_service(monkeypatch)
    service = object.__new__(GitHubSyncService)
    service.db = _FakeDb()

    await GitHubSyncService._update_file_index(
        service,
        tmp_path,
        removed_paths=set(),
    )

    assert len(service.db.statements) == 1


@pytest.mark.asyncio
async def test_file_index_cleanup_deletes_only_explicit_git_deleted_paths(tmp_path, monkeypatch):
    GitHubSyncService = _import_github_sync_service(monkeypatch)
    service = object.__new__(GitHubSyncService)
    service.db = _FakeDb()

    await GitHubSyncService._update_file_index(
        service,
        tmp_path,
        removed_paths={"workflows/old.py"},
    )

    assert len(service.db.statements) == 2
