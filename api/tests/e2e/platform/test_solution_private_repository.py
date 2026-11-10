"""Scoped private-source setup and a real install -> Git update transition.

Two jobs are intentional: installation consumes staged authorized bytes; the
update resolves credentials again in the isolated runner. All refusal cases
stay at the request/database boundary and enqueue no jobs.
"""
import asyncio
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from git import Repo
from sqlalchemy import delete

from src.models.orm.config import SystemConfig
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.github_config import get_github_config
from tests.e2e.platform.conftest import wait_for_install
from tests.e2e.platform.thin_live_helpers import wait_for_job

pytestmark = pytest.mark.e2e


def setup_source(client, admin, scope, repository_id=1619):
    result = client.post('/api/github/app/manifest', headers=admin.headers, json={
        'owner': 'thin-git-app', 'name': 'Solution Fixture App',
        'purpose': 'solution', 'organization_id': scope,
    })
    assert result.status_code == 200, result.text
    setup = result.json()
    assert setup['manifest']['default_permissions'] == {'contents': 'read'}
    state = parse_qs(urlsplit(setup['registration_url']).query)['state'][0]
    response = client.post('/api/github/app/manifest/complete', headers=admin.headers,
        json={'code': 'fixture-manifest-' + setup['setup_id'], 'state': state})
    assert response.status_code == 200, response.text
    response = client.post('/api/github/app/authorize', headers=admin.headers, json={'setup_id': setup['setup_id']})
    state = parse_qs(urlsplit(response.json()['authorization_url']).query)['state'][0]
    response = client.post('/api/github/app/authorize/complete', headers=admin.headers,
        json={'code': 'fixture-oauth-code', 'state': state})
    assert response.status_code == 200, response.text
    response = client.post('/api/github/app/select', headers=admin.headers,
        json={'setup_id': setup['setup_id'], 'installation_id': 2718, 'repository_id': repository_id})
    assert response.status_code == 200, response.text
    assert response.json()['stage'] == 'connected'
    return response.json()


@pytest_asyncio.fixture
async def source(e2e_client, platform_admin, org1, db_session):
    setup = setup_source(e2e_client, platform_admin, org1['id'])
    yield setup
    await db_session.execute(delete(Solution).where(Solution.git_source_id == UUID(setup['source_id'])))
    await db_session.execute(delete(SystemConfig).where(SystemConfig.id == UUID(setup['source_id'])))
    await db_session.commit()


@pytest.mark.asyncio
async def test_source_binding_is_encrypted_exact_scope_and_independent_of_workspace(e2e_client, platform_admin, org1, org2, source, db_session):
    row = await db_session.get(SystemConfig, UUID(source['source_id']))
    assert row.value_json is None
    assert b'PRIVATE KEY' not in row.value_bytes and b'fixture-secret' not in row.value_bytes
    actor = await db_session.get(User, platform_admin.user_id)
    before = await get_github_config(db_session, actor.organization_id)
    second = setup_source(e2e_client, platform_admin, org1['id'], 1618)
    assert second['source_id'] == source['source_id']
    listed = e2e_client.get('/api/solutions/git-sources', params={'organization_id': org1['id']}, headers=platform_admin.headers)
    assert listed.status_code == 200, listed.text
    assert {repo['id'] for repo in listed.json()['sources'][0]['repositories']} == {1618, 1619}
    for secret in ['private_key', 'client_secret', 'fixture-secret', 'PRIVATE KEY']:
        assert secret not in listed.text
    after = await get_github_config(db_session, actor.organization_id)
    assert (before.generation if before else None) == (after.generation if after else None)
    for foreign_scope in [None, org2['id']]:
        response = e2e_client.post('/api/solutions/install/preview-repo', headers=platform_admin.headers,
            json={'repo_url': 'https://github.com/thin-git-app/solutions', 'git_source_id': source['source_id'], 'organization_id': foreign_scope})
        assert response.status_code == 404, response.text
    refused = e2e_client.post('/api/solutions/install/preview-repo', headers=platform_admin.headers,
        json={'repo_url': 'https://github.com/thin-git-app/other', 'git_source_id': source['source_id'], 'organization_id': org1['id']})
    assert refused.status_code == 403, refused.text


@pytest.mark.asyncio
async def test_source_management_requires_platform_and_solution_permissions(e2e_client, org1_user, org1, source):
    response = e2e_client.delete(f"/api/github/app/solution-sources/{source['source_id']}",
        params={'organization_id': org1['id']}, headers=org1_user.headers)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_private_solution_install_then_update_uses_fresh_runner_credentials(e2e_client, platform_admin, org1, source, db_session, tmp_path):
    remote_path = Path('/tmp/bifrost/solution-repo-fixtures/thin-github/thin-git-app/solutions.git')
    remote_path.parent.mkdir(parents=True, exist_ok=True)
    assert not remote_path.exists(), 'Fixture must own its remote'
    remote = Repo.init(remote_path, bare=True, initial_branch='main')
    work = Repo.init(tmp_path, initial_branch='main')
    slug = 'private-' + uuid4().hex[:8]
    descriptor = tmp_path / 'bifrost.solution.yaml'

    def publish(version):
        descriptor.write_text(f'slug: {slug}\nname: Private Fixture\nversion: {version}\nscope: org\n')
        work.index.add(['bifrost.solution.yaml'])
        work.index.commit('Fixture version ' + version)
        work.git.push(str(remote_path), 'main')

    try:
        publish('1.0.0')
        body = {'repo_url': 'https://github.com/thin-git-app/solutions', 'git_source_id': source['source_id'], 'organization_id': org1['id']}
        preview = e2e_client.post('/api/solutions/install/preview-repo', headers=platform_admin.headers, json=body)
        assert preview.status_code == 200, preview.text
        assert preview.json()['version'] == '1.0.0'
        installed = await asyncio.to_thread(wait_for_install, e2e_client,
            e2e_client.post('/api/solutions/install/from-repo', headers=platform_admin.headers, json=body), platform_admin.headers)
        assert installed.status_code == 201, installed.text
        solution = installed.json()
        assert solution['git_source_id'] == source['source_id']
        assert solution['version'] == '1.0.0'
        publish('1.1.0')
        # Evict the read token to prove the subprocess mints a fresh scoped token.
        from src.core.cache.redis_client import get_shared_redis
        redis = await get_shared_redis()
        async for key in redis.scan_iter('github:git-token:*'):
            await redis.delete(key)
        queued = e2e_client.post(f"/api/solutions/{solution['id']}/sync", headers=platform_admin.headers)
        assert queued.status_code == 202, queued.text
        job = await asyncio.to_thread(wait_for_job, e2e_client, platform_admin.headers, queued.json()['job_id'], 'solution.git_sync')
        assert job['attempt'] == 1
        updated = e2e_client.get(f"/api/solutions/{solution['id']}", headers=platform_admin.headers)
        assert updated.json()['version'] == '1.1.0'
        assert 'fixture-git-app-installation' not in str(job)
        disconnected = e2e_client.delete(f"/api/github/app/solution-sources/{source['source_id']}", headers=platform_admin.headers, params={'organization_id': org1['id']})
        assert disconnected.status_code == 204, disconnected.text
        refused = e2e_client.post('/api/solutions/install/preview-repo', headers=platform_admin.headers, json=body)
        assert refused.status_code == 409, refused.text
        saved = await db_session.get(Solution, UUID(solution['id']), populate_existing=True)
        assert str(saved.git_source_id) == source['source_id']
        manual = e2e_client.patch(f"/api/solutions/{solution['id']}", headers=platform_admin.headers, json={"git_connected": False})
        assert manual.status_code == 200, manual.text
    finally:
        work.close()
        remote.close()
        shutil.rmtree(remote_path)
