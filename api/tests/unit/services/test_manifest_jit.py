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

    # desktop_commit uses repo_manager.lock(), not checkout()
    mock_rm = MagicMock()
    mock_rm.lock.return_value.__aenter__ = AsyncMock(return_value=mock_work_dir)
    mock_rm.lock.return_value.__aexit__ = AsyncMock(return_value=False)
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
async def test_reimport_regenerates_manifest_and_reindexes_workflows():
    """reimport_from_repo() should regenerate manifest and re-run workflow indexer."""
    from src.services.github_sync import GitHubSyncService

    mock_db = AsyncMock()
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.db = mock_db
    service.branch = "main"

    mock_work_dir = MagicMock(spec=Path)

    # Set repo_manager before patching (bypass __init__)
    mock_rm = MagicMock()
    mock_rm.checkout.return_value.__aenter__ = AsyncMock(return_value=mock_work_dir)
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
         patch.object(service, '_import_all_entities', return_value=(5, [], set())), \
         patch.object(service, '_update_file_index'), \
         patch.object(service, '_sync_app_previews'), \
         patch("src.services.github_sync.GitRepo"), \
         patch("src.services.github_sync._deleted_paths_in_head", return_value=set()):

        result = await service.reimport_from_repo()

        mock_regen.assert_called_once()
        mock_reindex.assert_called_once()
        assert result == 5
