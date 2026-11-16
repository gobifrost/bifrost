"""Direct DB/storage import commit witnesses; no remote push or worker jobs."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from git import Repo, Actor
import pytest
from sqlalchemy import delete, select

from src.jobs.platform.base import PlatformJobContext, PlatformJobFailure
from src.models.orm.config import SystemConfig
from src.models.orm.platform_jobs import PlatformJob
from src.models.contracts.github import WorkspaceSyncPlan, PushResult
from src.services.github_sync import GitHubSyncService, _workspace_fingerprint
from src.config import get_settings
from src.services import platform_jobs as jobs

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize('mode', ['success', 'rollback', 'lost_ack', 'unknown'])
async def test_git_entity_commit_and_job_receipt_have_one_outcome(async_session_factory, tmp_path: Path, monkeypatch, mode):
    job_id, token = uuid4(), uuid4()
    key = str(job_id)
    async with async_session_factory() as db:
        db.add(PlatformJob(id=job_id, job_type='workspace.git', status='running', payload={},
            lease_token=token, lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5),
            title='Git import fixture', requested_by_user_id=str(uuid4()),
            requested_by_email='fixture@example.test', requested_by_name='Fixture'))
        await db.commit()
    root = tmp_path/'workspace'
    repo = Repo.init(root, initial_branch='main')
    (root/'data.txt').write_text('reviewed source')
    repo.index.add(['data.txt'])
    actor = Actor('Fixture', 'fixture@example.test')
    repo.index.commit('fixture', author=actor, committer=actor)
    plan = WorkspaceSyncPlan(merge_sha=repo.head.commit.hexsha, workspace_fingerprint=_workspace_fingerprint(root))
    context = PlatformJobContext(job_id, token, None, str(uuid4()), 'fixture@example.test', 'Fixture')
    created = []
    service = None
    try:
        async with async_session_factory() as db:
            service = GitHubSyncService(db, 'unused://private-fixture', settings=get_settings(), job_context=context)
            checkpoint = service.repo_manager.checkpoint_workspace
            async def track(path):
                assert not db.in_transaction()
                identifier = await checkpoint(path)
                created.append(identifier)
                return identifier
            monkeypatch.setattr(service.repo_manager, 'checkpoint_workspace', track)
            async def apply(path, *, progress_fn):
                assert path != root and (path/'data.txt').read_text() == 'reviewed source'
                db.add(SystemConfig(category='git-transaction-fixture', key=key, value_json={'applied': True}))
                await progress_fn('Import fixture')
                return 1, []
            monkeypatch.setattr(service, '_import_all_entities', AsyncMock(side_effect=apply))
            monkeypatch.setattr(service, '_update_file_index', AsyncMock())
            monkeypatch.setattr(service, '_do_push', AsyncMock(return_value=PushResult(success=False, error='fixture only')))
            commit = db.commit
            if mode != 'success':
                async def broken_commit():
                    if mode != 'rollback':
                        await commit()
                    raise RuntimeError('fixture commit response lost')
                monkeypatch.setattr(db, 'commit', broken_commit)
            if mode == 'unknown':
                @asynccontextmanager
                async def unavailable():
                    raise OSError('fixture witness unavailable')
                    yield
                monkeypatch.setattr('src.core.database.get_db_context', unavailable)
            async def progress(*_args, **_kwargs):
                assert not db.in_transaction()
            if mode == 'rollback':
                with pytest.raises(RuntimeError, match='commit response lost'):
                    await service.apply_desktop_sync(root, repo, plan, confirm_deletes=True, progress_fn=progress)
            elif mode == 'unknown':
                with pytest.raises(PlatformJobFailure) as failure:
                    await service.apply_desktop_sync(root, repo, plan, confirm_deletes=True, progress_fn=progress)
                assert failure.value.code == 'git_commit_unconfirmed' and failure.value.preserve_checkpoint
                monkeypatch.setattr(jobs, 'publish_platform_job_update', AsyncMock())
                await jobs.retry_platform_job_failure(job_id, token, failure.value, enabled=False,
                    preserve_checkpoint=failure.value.preserve_checkpoint)
            else:
                result = await service.apply_desktop_sync(root, repo, plan, confirm_deletes=True, progress_fn=progress)
                assert result.retry_plan.db_applied and result.retry_plan.checkpoint_id == created[0]
            service._import_all_entities.assert_awaited_once()
        async with async_session_factory() as db:
            entity = await db.scalar(select(SystemConfig).where(SystemConfig.key == key))
            job = await db.get(PlatformJob, job_id)
            assert (entity is not None) == (mode != 'rollback')
            if mode == 'rollback':
                assert job.result is None
            else:
                assert job.result['retry_plan']['db_applied']
                assert job.result['retry_plan']['checkpoint_id'] == created[0]
                if mode == 'unknown':
                    assert job.status == 'failed'
    finally:
        if service is not None:
            for identifier in created:
                await service.repo_manager.delete_workspace_checkpoint(identifier)
        async with async_session_factory() as db:
            await db.execute(delete(SystemConfig).where(SystemConfig.key == key, SystemConfig.category == 'git-transaction-fixture'))
            await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.commit()


@pytest.mark.parametrize('preserve', [False, True])
async def test_terminal_cancel_keeps_only_the_current_canonical_receipt_when_requested(async_session_factory, monkeypatch, preserve):
    job_id, token = uuid4(), uuid4()
    async with async_session_factory() as db:
        db.add(PlatformJob(id=job_id, job_type='workspace.git', status='running', payload={},
            result={'canonical': 'committed'}, lease_token=token, title='Receipt fixture',
            requested_by_user_id=str(uuid4()), requested_by_email='fixture@example.test', requested_by_name='Fixture'))
        await db.commit()
    monkeypatch.setattr(jobs, 'publish_platform_job_update', AsyncMock())
    try:
        assert await jobs.finish_platform_job(job_id, token, status='cancelled', preserve_checkpoint=preserve)
        async with async_session_factory() as db:
            assert (await db.get(PlatformJob, job_id)).result == ({'canonical': 'committed'} if preserve else None)
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.commit()
