"""Real DB lease/owner fences protect one conditional retained-state pointer.

No Git remote, platform worker job, or public repository write is performed.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from git import Repo
import pytest
from sqlalchemy import delete

from src.jobs.platform.base import PlatformJobContext, PlatformJobCancelled
from src.jobs.platform.git_operation import workspace_publication_authorization
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.execution_workers import ExecutionWorker
from src.services.git_repo_manager import GitRepoManager
from src.services.git_workspace_state import GitWorkspaceState
from src.worker.remote.fencing import LeaseRef, remote_owner_context
from src.worker.remote.ownership import RemoteOwnerLease
from tests.e2e.platform import test_git_workspace_handoff as handoff_fixtures

# Reuse its unique-prefix S3 fixture without importing neighboring DB fixtures.
backend = handoff_fixtures.backend

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize('loss', ['cancel', 'job_lease', 'remote_owner'])
async def test_retained_state_publication_refuses_lost_attempt_with_real_storage(
    async_session_factory, backend, tmp_path: Path, monkeypatch, loss,
):
    settings, _client, prefix = backend
    monkeypatch.setattr('src.services.git_workspace_state.STATE_KEY', prefix+'pointer.json')
    job_id, token, worker_id, owner_id, launcher_id = (uuid4() for _ in range(5))
    expires = datetime.now(timezone.utc)+timedelta(minutes=5)
    async with async_session_factory() as db:
        db.add(ExecutionWorker(id=worker_id, form='static', state='ready', supported_handlers=[],
            remote_owner_id=owner_id, remote_launcher_id=launcher_id, remote_session_generation=1,
            remote_owner_lease_expires_at=expires, heartbeat_at=datetime.now(timezone.utc)))
        db.add(PlatformJob(id=job_id, job_type='workspace.git', payload={}, status='running',
            lease_token=token, lease_expires_at=expires, title='Private publication fixture',
            requested_by_user_id=str(uuid4()), requested_by_email='fixture@example.test', requested_by_name='Fixture'))
        await db.commit()
    context = PlatformJobContext(job_id, token, None, str(uuid4()), 'fixture@example.test', 'Fixture')
    manager = GitRepoManager(settings, publication_authorization=lambda: workspace_publication_authorization(context))
    created = []
    checkpoint = manager.checkpoint_workspace
    async def track_checkpoint(root):
        identifier = await checkpoint(root)
        created.append(identifier)
        return identifier
    monkeypatch.setattr(manager, 'checkpoint_workspace', track_checkpoint)
    root = tmp_path/'workspace'
    Repo.init(root, initial_branch='main')
    manager._write_storage_baseline(root, 'a'*64)
    (root/'data.txt').write_text('first')
    state = GitWorkspaceState(manager)
    ref = LeaseRef(RemoteOwnerLease(worker_id, owner_id, launcher_id, 1))
    try:
        with remote_owner_context(ref):
            await state.persist(root, None, None)
        previous, etag = await state.read()
        receipt = state._receipt_path(root).read_bytes()
        (root/'data.txt').write_text('second')
        async with async_session_factory() as db:
            job = await db.get(PlatformJob, job_id)
            if loss == 'cancel':
                job.status = 'cancel_requested'
            elif loss == 'job_lease':
                job.lease_token = uuid4()
            else:
                (await db.get(ExecutionWorker, worker_id)).remote_session_generation = 2
            await db.commit()
        with remote_owner_context(ref), pytest.raises(PlatformJobCancelled):
            await state.persist(root, previous, etag)
        assert await state.read() == (previous, etag)
        assert state._receipt_path(root).read_bytes() == receipt
        restored = tmp_path/'readback'
        await manager.restore_workspace_checkpoint(previous.checkpoint, restored)
        assert (restored/'data.txt').read_text() == 'first'
    finally:
        for identifier in created:
            await manager.delete_workspace_checkpoint(identifier)
        async with async_session_factory() as db:
            await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await db.commit()
