"""Canonical generated manifests survive ignore rules; other ignored files stay excluded."""
from unittest.mock import AsyncMock

import pytest
from git import Actor, Repo

from bifrost.manifest import MANIFEST_FILES, MANIFEST_LEGACY_FILE
from bifrost.definition_validation import DefinitionValidationError
from src.services.github_sync import GitHubSyncService


def test_ignored_manifests_and_tracked_removals_are_staged(tmp_path):
    repo = Repo.init(tmp_path)
    (tmp_path / "removed.py").write_text("old")
    repo.git.add(A=True)
    repo.index.commit("baseline", author=Actor("Test", "test@example.test"),
                      committer=Actor("Test", "test@example.test"))
    (tmp_path / "removed.py").unlink()
    (tmp_path / ".gitignore").write_text(".bifrost/\nignored.py\n")
    (tmp_path / "ignored.py").write_text("ignored")
    folder = tmp_path / ".bifrost"
    folder.mkdir()
    expected = {f".bifrost/{name}" for name in {*MANIFEST_FILES.values(), MANIFEST_LEGACY_FILE}}
    for relative in expected:
        (tmp_path / relative).write_text("{}\n")
    (folder / "secrets.enc").write_text("private")
    GitHubSyncService._stage_workspace_tree(tmp_path, repo)
    changes = repo.git.diff("--cached", "--name-status")
    assert "D\tremoved.py" in changes
    staged = set(repo.git.diff("--cached", "--name-only").splitlines())
    assert expected <= staged
    assert ".bifrost/secrets.enc" not in staged
    assert "ignored.py" not in staged


def test_external_manifest_link_is_rejected_before_staging(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    repo = Repo.init(root)
    (root / ".bifrost").mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("private")
    (root / ".bifrost" / "workflows.yaml").symlink_to(outside)
    with pytest.raises(DefinitionValidationError):
        GitHubSyncService._stage_workspace_tree(root, repo)
    assert repo.git.diff("--cached", "--name-only") == ""


async def test_connect_uses_shared_manifest_staging(tmp_path, monkeypatch):
    from types import SimpleNamespace
    repo = Repo.init(tmp_path)
    (tmp_path / ".gitignore").write_text(".bifrost/\n")
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "workflows.yaml").write_text("workflows: {}\n")
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test")
        cfg.set_value("user", "email", "test@example.test")
    service = GitHubSyncService.__new__(GitHubSyncService)
    monkeypatch.setattr(service, "_run_preflight", AsyncMock(return_value=SimpleNamespace(valid=True)))
    await service._commit_connect_tree(tmp_path, repo)
    assert repo.git.show("HEAD:.bifrost/workflows.yaml") == "workflows: {}"
