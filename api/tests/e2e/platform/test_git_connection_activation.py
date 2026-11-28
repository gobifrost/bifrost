"""Connection and canonical receipt share a real SQL commit; no provider calls."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from shared import github_git_app as app
from src.jobs.platform import git_operation as git
from src.jobs.platform.base import PlatformJobContext, PlatformJobCancelled, PlatformJobFailure
from src.models.orm.config import SystemConfig
from src.models.orm.organizations import Organization
from src.models.orm.platform_jobs import PlatformJob
from src.services.github_config import _get_fernet, get_github_config, save_github_config

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize('kind', ['pat', 'app'])
@pytest.mark.parametrize('mode', ['success', 'cancelled', 'replaced', 'rollback', 'lost_ack'])
async def test_connection_activation_and_receipt_are_atomic(async_session_factory, monkeypatch, kind, mode):
    scope, job_id, token, setup_id = uuid4(), uuid4(), uuid4(), uuid4()
    actor = str(uuid4())
    context = PlatformJobContext(job_id, token, scope, actor, 'fixture@example.test', 'Fixture')
    result = {'success': True, 'pushed_commits': 1}
    connection = {'repository_url': 'https://github.com/fixture/private', 'branch': 'main'}
    provider = AsyncMock(side_effect=AssertionError('activation must not call a provider'))
    monkeypatch.setattr(app, '_request', provider)
    try:
        async with async_session_factory() as db:
            db.add(Organization(id=scope, name=f'git-activation-{scope}', created_by='fixture'))
            await db.commit()
            db.add(PlatformJob(id=job_id, job_type='workspace.git', organization_id=scope,
                status='cancelled' if mode == 'cancelled' else 'running', payload={},
                lease_token=token, title='Activation fixture', requested_by_user_id=actor,
                requested_by_email='fixture@example.test', requested_by_name='Fixture'))
            await save_github_config(db, scope, 'synthetic-fixture-pat', None, 'main', 'fixture')
            original = await get_github_config(db, scope)
            assert original is not None
            if kind == 'app':
                payload = {'stage': 'ready', 'prior_generation': original.generation,
                    'expires_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                    'purpose': 'workspace', 'app': {'id': 42, 'private_key': 'synthetic-fixture-key'},
                    'installation_id': 7, 'workflows': False,
                    'repository': {'id': 91, 'full_name': 'fixture/private', 'default_branch': 'main'}}
                db.add(SystemConfig(category='github_setup', key=str(setup_id), organization_id=scope,
                    created_by=actor, value_json={'stage': 'ready'},
                    value_bytes=_get_fernet().encrypt(json.dumps(payload).encode())))
                candidate = app._selected_config(setup_id, payload, actor, scope)
                candidate.setup_executing = True
            else:
                candidate = original
            if mode == 'replaced':
                await save_github_config(db, scope, 'synthetic-replacement-pat', None, 'main', 'fixture')
            await db.commit()

        interrupt_commits = True
        @asynccontextmanager
        async def database():
            async with async_session_factory() as db:
                commit = db.commit
                if interrupt_commits and mode in {'rollback', 'lost_ack'}:
                    async def interrupted():
                        if mode == 'lost_ack':
                            await commit()
                        raise OSError('fixture commit interrupted')
                    monkeypatch.setattr(db, 'commit', interrupted)
                try:
                    yield db
                    await db.commit()
                except BaseException:
                    await db.rollback()
                    raise
        monkeypatch.setattr(git, 'get_db_context', database)
        if mode == 'cancelled':
            with pytest.raises(PlatformJobCancelled):
                await git._activate_connection(context, candidate, connection, result)
        elif mode == 'replaced':
            with pytest.raises(HTTPException):
                await git._activate_connection(context, candidate, connection, result)
        elif mode in {'rollback', 'lost_ack'}:
            with pytest.raises(PlatformJobFailure) as error:
                await git._activate_connection(context, candidate, connection, result)
            assert error.value.code == 'git_activation_commit_unconfirmed' and error.value.preserve_checkpoint
        else:
            await git._activate_connection(context, candidate, connection, result)
        interrupt_commits = False  # Recovery has a healthy database connection.
        async with async_session_factory() as db:
            current = await get_github_config(db, scope)
            job = await db.get(PlatformJob, job_id)
            committed = mode in {'success', 'lost_ack'}
            assert bool(current.repo_url) == committed
            assert bool(job.result and job.result.get('git_connection_complete')) == committed
            if committed:
                assert job.result['job_id'] == str(job_id)
                assert job.result['result'] == result
                assert 'synthetic' not in json.dumps(job.result)
                # Activation recovery must not need the stripped pending App
                # private key, a live preview, Git or current credentials.
                recovered = replace(context, checkpoint=job.result)
                monkeypatch.setattr(git, 'get_github_config', AsyncMock(side_effect=AssertionError('unexpected credentials')))
                assert await git.run_git_operation(recovered, git.GitOperationPayload(operation='connect')) == result
        provider.assert_not_awaited()
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.execute(delete(SystemConfig).where(SystemConfig.organization_id == scope))
            await db.execute(delete(Organization).where(Organization.id == scope))
            await db.commit()
