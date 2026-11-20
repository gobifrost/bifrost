"""Platform-job handler behavior for the Maintenance workspace reimport."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.reimport import (
    WorkspaceReimportPayload,
    run_workspace_reimport,
)
from src.services.github_sync import WorkspaceSourceMissing


@pytest.mark.asyncio
async def test_reimport_reports_missing_workspace_source_as_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def fake_db_context():
        yield SimpleNamespace()

    message = "Reimport refused: workspace storage is empty. Nothing was changed."
    monkeypatch.setattr("src.jobs.platform.reimport.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.github_sync.GitHubSyncService.reimport_from_repo",
        AsyncMock(side_effect=WorkspaceSourceMissing(message)),
    )

    with pytest.raises(PlatformJobFailure) as error:
        await run_workspace_reimport(
            SimpleNamespace(report=AsyncMock(), log=AsyncMock(), file_compute=AsyncMock()),
            WorkspaceReimportPayload(),
        )

    assert error.value.code == "workspace_source_missing"
    assert error.value.message == message
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_reimport_reports_kept_deletions_in_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.models.contracts.github import EntityChange
    from src.services.github_sync import ReimportResult

    @asynccontextmanager
    async def fake_db_context():
        yield SimpleNamespace()

    pending = EntityChange(
        action="removed", entity_type="workflows", name="Old Workflow", entity_id="wf-1",
    )
    monkeypatch.setattr("src.jobs.platform.reimport.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.github_sync.GitHubSyncService.reimport_from_repo",
        AsyncMock(return_value=ReimportResult(entities_imported=3, pending_deletes=[pending])),
    )

    result = await run_workspace_reimport(
        SimpleNamespace(report=AsyncMock(), log=AsyncMock(), file_compute=AsyncMock()),
        WorkspaceReimportPayload(),
    )

    assert result["entities_imported"] == 3
    assert [change["entity_id"] for change in result["pending_deletes"]] == ["wf-1"]
    assert "Old Workflow" in result["message"]
    assert "git sync" in result["message"]


@pytest.mark.asyncio
async def test_reimport_requires_compute_before_opening_source(monkeypatch):
    service = AsyncMock()
    monkeypatch.setattr("src.services.github_sync.GitHubSyncService.reimport_from_repo", service)
    with pytest.raises(PlatformJobFailure, match="API-owned preview compute"):
        await run_workspace_reimport(SimpleNamespace(file_compute=None), WorkspaceReimportPayload())
    service.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('lose_after_capture', [False, True])
async def test_reimport_fences_mutations_without_holding_transaction_during_compute(tmp_path, monkeypatch, lose_after_capture):
    from src.jobs.platform.base import PlatformJobCancelled
    from src.services.github_sync import GitHubSyncService
    from unittest.mock import MagicMock

    service = GitHubSyncService.__new__(GitHubSyncService)
    db = AsyncMock()
    active = False
    authorizations = 0
    effects = []

    async def commit():
        nonlocal active
        active = False
    db.commit.side_effect = commit

    @asynccontextmanager
    async def nested():
        assert active
        yield
    db.begin_nested = nested
    service.db = db
    (tmp_path / '.bifrost').mkdir()
    (tmp_path / '.bifrost' / 'workflows.yaml').write_text('workflows: {}\n')
    (tmp_path / 'source.py').write_text('def main(): pass\n')

    @asynccontextmanager
    async def checkout(**kwargs):
        assert kwargs == {'isolated': True}
        yield tmp_path
        assert active  # final source publication remains inside its owner fence
        effects.append('workspace-published')
    service.repo_manager = SimpleNamespace(checkout=checkout)
    service._resolver = SimpleNamespace(_resolve_deletions=AsyncMock(return_value=[]))
    service._import_all_entities = AsyncMock(return_value=(3, []))
    service._update_file_index = AsyncMock()
    service._reindex_registered_workflows = AsyncMock()
    service._regenerate_manifest_to_dir = AsyncMock()
    context = SimpleNamespace(file_compute=AsyncMock())

    async def authorize(session):
        nonlocal active, authorizations
        assert session is db
        authorizations += 1
        active = True
        return not (lose_after_capture and authorizations == 2)
    context.authorize_effect = authorize

    async def previews(work_dir, *, context):
        assert work_dir == tmp_path and context.file_compute is not None
        assert not active
        effects.append('compute-without-transaction')
    service._sync_app_previews = AsyncMock(side_effect=previews)
    monkeypatch.setattr('src.services.github_sync._validate_manifest_paths_or_raise', MagicMock())
    if lose_after_capture:
        with pytest.raises(PlatformJobCancelled):
            await service.reimport_from_repo(context=context)
        service._import_all_entities.assert_not_awaited()
        service._update_file_index.assert_not_awaited()
        service._sync_app_previews.assert_not_awaited()
        assert not effects
    else:
        result = await service.reimport_from_repo(context=context)
        assert result.entities_imported == 3
        assert authorizations == 4
        assert effects == ['compute-without-transaction', 'workspace-published']
        assert not active
    service._regenerate_manifest_to_dir.assert_not_awaited()
