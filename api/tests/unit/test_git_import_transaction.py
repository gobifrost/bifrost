"""Progress cannot open a second job-row transaction inside entity import."""
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.models.contracts.github import WorkspaceSyncPlan, PushResult
from src.services.github_sync import GitHubSyncService
from src.jobs.platform.base import PlatformJobCancelled


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_entity_import_checkpoint_precedes_progress_and_publication(monkeypatch, cancelled):
    db = SimpleNamespace(active=False, commit=AsyncMock(), rollback=AsyncMock())
    async def commit():
        db.active = False
    async def rollback():
        db.active = False
    db.commit.side_effect = commit
    db.rollback.side_effect = rollback
    @asynccontextmanager
    async def nested():
        assert db.active
        yield
    db.begin_nested = nested
    records = []
    async def authorize(session):
        assert session is db
        if cancelled:
            return False
        db.active = True
        return True
    async def checkpoint(session, result, *, phase):
        assert session is db and db.active
        assert result['retry_plan']['db_applied']
        records.append(result)
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.db = db
    service.repo_url = "https://github.com/fixture/private.git"
    service.branch = "main"
    service._job_operation = None
    service._job_context = SimpleNamespace(authorize_effect=authorize, checkpoint_in_transaction=checkpoint)
    service.repo_manager = SimpleNamespace(delete_workspace_checkpoint=AsyncMock())
    @asynccontextmanager
    async def staged(work_dir, plan):
        assert not db.active
        yield 'fixture-checkpoint', Path('/verified')
    service._staged_sync_checkpoint = staged
    async def apply(path, *, progress_fn):
        assert path == Path('/verified') and db.active
        await progress_fn('Resolving entities', current=1, total=1)
        return 1, []
    service._import_all_entities = AsyncMock(side_effect=apply)
    service._update_file_index = AsyncMock()
    service._do_push = AsyncMock(return_value=PushResult(success=False, error='fixture unavailable'))
    monkeypatch.setattr('src.services.github_sync._workspace_fingerprint', lambda _path: 'a'*64)
    repo = SimpleNamespace(head=SimpleNamespace(is_valid=lambda: True, commit=SimpleNamespace(hexsha='head')))
    plan = WorkspaceSyncPlan(merge_sha='head', workspace_fingerprint='a'*64)
    progress = []
    async def report(*args, **kwargs):
        assert not db.active
        progress.append(args)
    if cancelled:
        with pytest.raises(PlatformJobCancelled):
            await service.apply_desktop_sync(Path('/live'), repo, plan, confirm_deletes=True, progress_fn=report)
        service._import_all_entities.assert_not_awaited()
        service._do_push.assert_not_awaited()
        service.repo_manager.delete_workspace_checkpoint.assert_awaited_once_with('fixture-checkpoint')
        assert not records
    else:
        result = await service.apply_desktop_sync(Path('/live'), repo, plan, confirm_deletes=True, progress_fn=report)
        assert result.retry_plan.db_applied and records[0]['retry_plan'] == result.retry_plan.model_dump(mode='json')
        assert progress[-1] == ('Pushing to remote...',)
        service._import_all_entities.assert_awaited_once()
        db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('cleanup_fails', [False, True])
async def test_completion_receipt_is_durable_before_deleting_import_snapshot(monkeypatch, cleanup_fails):
    from uuid import uuid4
    from src.jobs.platform.base import PlatformJobFailure
    db = SimpleNamespace(active=False, commit=AsyncMock(), in_transaction=lambda: False)
    async def commit():
        db.active = False
    db.commit.side_effect = commit
    records = []
    async def authorize(session):
        db.active = True
        return True
    async def checkpoint(session, result, *, phase):
        db.active = True
        records.append(result)
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.db = db
    service.repo_url = "https://github.com/fixture/private.git"
    service.branch = "main"
    service._job_operation = None
    service._job_operation = 'sync'
    service._job_context = SimpleNamespace(job_id=uuid4(), authorize_effect=authorize, checkpoint_in_transaction=checkpoint)
    async def cleanup(identifier):
        assert records[-1]['git_publication_complete'] and not db.active
        if cleanup_fails:
            raise OSError('fixture cleanup acknowledgement lost')
    service.repo_manager = SimpleNamespace(sync_up=AsyncMock(), delete_workspace_checkpoint=AsyncMock(side_effect=cleanup))
    service._do_push = AsyncMock(return_value=PushResult(success=True, pushed_commits=1, commit_sha='head'))
    service._sync_app_previews = AsyncMock()
    monkeypatch.setattr('src.core.module_cache.refresh_modules_from_directory', AsyncMock())
    monkeypatch.setattr('src.services.github_sync._workspace_fingerprint', lambda _root: 'a'*64)
    repo = SimpleNamespace(head=SimpleNamespace(is_valid=lambda: True, commit=SimpleNamespace(hexsha='head')))
    plan = WorkspaceSyncPlan(merge_sha='head', workspace_fingerprint='a'*64, db_applied=True, checkpoint_id=str(uuid4()))
    async def progress(*_args):
        assert not db.active
    if cleanup_fails:
        with pytest.raises(PlatformJobFailure) as failure:
            await service.apply_desktop_sync(Path('/work'), repo, plan, confirm_deletes=True, progress_fn=progress)
        assert failure.value.preserve_checkpoint and failure.value.code == 'git_checkpoint_cleanup_failed'
    else:
        result = await service.apply_desktop_sync(Path('/work'), repo, plan, confirm_deletes=True, progress_fn=progress)
        assert result.success
    assert records[-1]['result']['success']
    assert records[-1]['job_id'] == str(service._job_context.job_id)
    assert db.commit.await_count == 2
