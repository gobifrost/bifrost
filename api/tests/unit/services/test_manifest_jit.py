"""Tests for JIT manifest generation before git commit."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


@pytest.mark.asyncio
async def test_desktop_commit_regenerates_manifest_before_staging():
    """desktop_commit() should regenerate manifest into working tree before git add."""
    from src.services.github_sync import GitHubSyncService

    # Track call order
    call_order = []

    mock_db = AsyncMock()
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.db = mock_db
    service.branch = "main"

    mock_work_dir = MagicMock(spec=Path)
    mock_repo = MagicMock()
    mock_repo.head.is_valid.return_value = True
    mock_repo.index.diff.return_value = [MagicMock()]  # Has changes
    mock_repo.untracked_files = []
    mock_repo.index.commit.return_value = MagicMock(hexsha="abc12345")

    # Desktop operations retain local Git state through the durable workspace.
    mock_rm = MagicMock()
    mock_rm.workspace.return_value.__aenter__ = AsyncMock(return_value=mock_work_dir)
    mock_rm.workspace.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_rm.ensure_storage_unchanged = AsyncMock()
    service.repo_manager = mock_rm

    with patch.object(service, '_open_or_init', return_value=mock_repo), \
         patch.object(service, '_regenerate_manifest_to_dir') as mock_regen, \
         patch.object(service, '_run_preflight') as mock_preflight:

        # Track call order
        async def regen_side_effect(db, work_dir):
            call_order.append("regenerate_manifest")
            return []
        mock_regen.side_effect = regen_side_effect

        def add_side_effect(*args, **kwargs):
            call_order.append("git_add")
        mock_repo.git.add.side_effect = add_side_effect

        mock_preflight.return_value = MagicMock(valid=True)

        await service.desktop_commit("test commit")

        # Manifest must be regenerated BEFORE git add
        assert call_order == ["regenerate_manifest", "git_add"]
        mock_regen.assert_called_once_with(mock_db, mock_work_dir)


@pytest.mark.asyncio
async def test_reimport_regenerates_manifest_only_for_legacy_workspace(tmp_path):
    """A workspace without any manifest keeps the legacy bootstrap behavior."""
    from src.services.github_sync import GitHubSyncService

    mock_db = AsyncMock()
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.db = mock_db
    service.branch = "main"

    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "legacy.py").write_text("def legacy(): pass\n")

    # Set repo_manager before patching (bypass __init__)
    mock_rm = MagicMock()
    mock_rm.checkout.return_value.__aenter__ = AsyncMock(return_value=tmp_path)
    mock_rm.checkout.return_value.__aexit__ = AsyncMock(return_value=False)
    service.repo_manager = mock_rm

    # begin_nested() must return an async context manager (not a coroutine)
    mock_nested = MagicMock()
    mock_nested.__aenter__ = AsyncMock()
    mock_nested.__aexit__ = AsyncMock(return_value=False)
    mock_db.begin_nested = MagicMock(return_value=mock_nested)
    mock_db.commit = AsyncMock()

    # Create a mock resolver so we can patch _resolve_deletions on it
    mock_resolver = MagicMock()
    mock_resolver._resolve_deletions = AsyncMock(return_value=[])
    service._resolver = mock_resolver

    with patch.object(service, '_regenerate_manifest_to_dir') as mock_regen, \
         patch.object(service, '_reindex_registered_workflows') as mock_reindex, \
         patch.object(service, '_import_all_entities', return_value=(5, [])), \
         patch.object(service, '_update_file_index'), \
         patch.object(service, '_sync_app_previews'):

        result = await service.reimport_from_repo()

        mock_regen.assert_called_once_with(mock_db, tmp_path)
        mock_reindex.assert_called_once()
        assert result.entities_imported == 5
        # Reimport only previews deletions; it never applies them.
        assert all(
            call.kwargs.get("dry_run") is True
            for call in mock_resolver._resolve_deletions.await_args_list
        )


@pytest.mark.asyncio
async def test_reimport_preserves_checked_out_manifest_when_database_differs(tmp_path):
    """An authored workspace manifest is imported instead of being regenerated from DB."""
    from bifrost.manifest import Manifest, ManifestWorkflow, write_manifest_to_dir
    from src.services.github_sync import GitHubSyncService
    from uuid import uuid4

    workflow_id = str(uuid4())
    source_path = "workflows/authored.py"
    source = tmp_path / source_path
    source.parent.mkdir()
    source.write_text("def authored(): pass\n")
    write_manifest_to_dir(
        Manifest(workflows={
            workflow_id: ManifestWorkflow(
                id=workflow_id, name="authored", path=source_path, function_name="authored",
            )
        }),
        tmp_path / ".bifrost",
    )

    mock_db = AsyncMock()
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.db = mock_db
    service.branch = "main"
    mock_rm = MagicMock()
    mock_rm.checkout.return_value.__aenter__ = AsyncMock(return_value=tmp_path)
    mock_rm.checkout.return_value.__aexit__ = AsyncMock(return_value=False)
    service.repo_manager = mock_rm
    nested = MagicMock()
    nested.__aenter__ = AsyncMock()
    nested.__aexit__ = AsyncMock(return_value=False)
    mock_db.begin_nested = MagicMock(return_value=nested)
    mock_db.commit = AsyncMock()
    resolver = MagicMock()
    resolver._resolve_deletions = AsyncMock(return_value=[])
    service._resolver = resolver

    with patch.object(service, "_regenerate_manifest_to_dir") as regenerate, \
         patch.object(service, "_reindex_registered_workflows"), \
         patch.object(service, "_import_all_entities", return_value=(1, [])), \
         patch.object(service, "_update_file_index"), \
         patch.object(service, "_sync_app_previews"):
        result = await service.reimport_from_repo()

    regenerate.assert_not_called()
    assert result.entities_imported == 1
