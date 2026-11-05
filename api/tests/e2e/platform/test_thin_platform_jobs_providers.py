"""Real provider-bound handler success through the thin physical runner.

Four jobs: one seeded embedding rewrite, two video submit/poll/download/store
paths, and the public models.dev catalog refresh. Providers are local except
for the credential-free catalog GET. No handler or process boundary is mocked.
Uses the canonical test stack's local HTTP/TLS provider fixtures.
"""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.agents import Conversation, Message
from src.models.orm.ai_models import AIEmbeddingConfig, AIModelAssignment, AIModelProfile, AIProviderConnection
from src.models.orm.ai_usage import AIUsage
from src.models.orm.artifacts import Artifact
from src.models.orm.knowledge import KnowledgeStore
from src.models.orm.platform_jobs import PlatformJob
from tests.e2e.conftest import execute_workflow_sync, write_and_register
from tests.e2e.platform.thin_live_helpers import wait_for_job

pytestmark = pytest.mark.e2e


@pytest.fixture
def video_profile(e2e_client, platform_admin, async_session_factory):
    headers = platform_admin.headers
    suffix = uuid4().hex[:12]
    async def snapshot():
        async with async_session_factory() as db:
            return dict((await db.execute(select(
                AIModelAssignment.assignment_key, AIModelAssignment.profile_id))).all())
    previous = asyncio.run(snapshot())
    response = e2e_client.post('/api/admin/ai/connections', headers=headers, json={
        'name': f'Video live {suffix}', 'provider': 'openai_compatible',
        'api_key': 'fixture-key', 'endpoint': 'https://fixture.openrouter.ai:8443/v1',
    })
    assert response.status_code == 201, response.text
    connection_id = UUID(response.json()['id'])
    response = e2e_client.post('/api/admin/ai/profiles', headers=headers, json={
        'name': f'Video live {suffix}', 'connection_id': str(connection_id), 'model': 'fixture-video',
    })
    assert response.status_code == 201, response.text
    profile_id = UUID(response.json()['id'])

    async def assign():
        async with async_session_factory() as db:
            row = await db.get(AIModelAssignment, 'video_generation')
            if row:
                row.profile_id = profile_id
            else:
                db.add(AIModelAssignment(assignment_key='video_generation', profile_id=profile_id))
            await db.commit()

    asyncio.run(assign())
    try:
        yield
    finally:
        async def cleanup():
            async with async_session_factory() as db:
                assignments = (await db.execute(select(AIModelAssignment).where(
                    AIModelAssignment.profile_id == profile_id))).scalars().all()
                for assignment in assignments:
                    if assignment.assignment_key in previous:
                        assignment.profile_id = previous[assignment.assignment_key]
                    else:
                        await db.delete(assignment)
                await db.flush()
                await db.execute(delete(AIModelProfile).where(AIModelProfile.id == profile_id))
                await db.execute(delete(AIProviderConnection).where(AIProviderConnection.id == connection_id))
                await db.commit()
        asyncio.run(cleanup())


@pytest.mark.parametrize('job_type', ['sdk.video_generation', 'chat.video_generation'])
def test_video_handler_completes_and_stores_artifact(
    e2e_client, platform_admin, async_session_factory, video_profile, job_type,
):
    conversation_id, message_id = uuid4(), uuid4()
    workflow_path = None
    execution_id = None
    if job_type == 'sdk.video_generation':
        tag = uuid4().hex[:12]
        name, filename = f'thin_video_{tag}', f'thin-video-{tag}.mp4'
        workflow_path = f'{name}.py'
        source = f'''from bifrost import workflow
from bifrost.artifacts import artifacts

@workflow(name="{name}")
async def {name}():
    ref = await artifacts.create_video("{filename}", prompt="A deterministic test video",
                                       timeout_seconds=60, poll_interval_seconds=0.1)
    content = await artifacts.read(ref)
    assert content[4:8] == b"ftyp"
    return ref.model_dump()
'''
        registered = write_and_register(e2e_client, platform_admin.headers, workflow_path, source, name)
        result = execute_workflow_sync(e2e_client, platform_admin.headers, registered['id'], max_wait=90)
        assert result['status'] == 'Success', result
        execution_id = UUID(result['execution_id'])
        async def find_video_job():
            async with async_session_factory() as db:
                return str((await db.execute(select(PlatformJob.id).where(
                    PlatformJob.job_type == job_type, PlatformJob.resource_id == filename))).scalar_one())
        job_id = asyncio.run(find_video_job())
    else:
        async def seed_and_enqueue():
            from src.jobs.platform.registry import get_platform_job_definition
            from src.models.enums import MessageRole
            from src.services.platform_jobs import enqueue_platform_job

            async with async_session_factory() as db:
                db.add(Conversation(id=conversation_id, user_id=platform_admin.user_id, title='Thin video fixture'))
                await db.flush()
                db.add(Message(id=message_id, conversation_id=conversation_id, role=MessageRole.TOOL_CALL,
                    tool_name='generate_video', tool_state='running', sequence=0))
                definition = get_platform_job_definition(job_type)
                assert definition is not None
                job, _ = await enqueue_platform_job(db, definition,
                    {'conversation_id': str(conversation_id), 'message_id': str(message_id),
                     'filename': 'thin-live.mp4', 'prompt': 'A deterministic test video'},
                    dedupe_key=str(message_id), organization_id=None,
                    requested_by_user_id=platform_admin.user_id, requested_by_email=platform_admin.email,
                    requested_by_name='Thin fixture', resource_type='conversation', resource_id=str(conversation_id),
                    title='Thin video fixture', action_url=None)
                await db.commit()
                return str(job.id)
        job_id = asyncio.run(seed_and_enqueue())

    artifact_id = None
    try:
        job = wait_for_job(e2e_client, platform_admin.headers, job_id, job_type)
        artifact_id = UUID(job['result']['artifact']['id'])

        async def verify():
            from src.services.artifacts import ArtifactService
            async with async_session_factory() as db:
                artifact = await db.get(Artifact, artifact_id)
                assert artifact is not None and artifact.content_type == 'video/mp4'
                assert artifact.size_bytes == 24
                # Observe the actual object, not just a durable success flag.
                storage = ArtifactService(db)
                content = await storage.read(artifact)
                assert content[4:8] == b'ftyp'
                usage = (await db.execute(select(AIUsage).where(
                    AIUsage.conversation_id == conversation_id) if job_type == 'chat.video_generation'
                    else select(AIUsage).where(AIUsage.execution_id == execution_id))).scalars().all()
                assert usage and any(row.input_tokens == 3 and row.output_tokens == 5 for row in usage)
                if job_type == 'chat.video_generation':
                    message = await db.get(Message, message_id)
                    assert message is not None and message.tool_state == 'completed'
                    assert message.tool_result['artifact']['id'] == str(artifact_id)
        asyncio.run(verify())
    finally:
        async def cleanup():
            async with async_session_factory() as db:
                if job_type == 'chat.video_generation':
                    await db.execute(delete(AIUsage).where(AIUsage.conversation_id == conversation_id))
                    await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
                else:
                    await db.execute(delete(AIUsage).where(AIUsage.execution_id == execution_id))
                if artifact_id:
                    from src.services.artifacts import ArtifactService
                    artifact = await db.get(Artifact, artifact_id)
                    if artifact:
                        await ArtifactService(db).delete(artifact)
                await db.commit()
        asyncio.run(cleanup())
        if workflow_path:
            response = e2e_client.delete(f'/api/files/editor?path={workflow_path}', headers=platform_admin.headers)
            assert response.status_code in (200, 204), response.text


def test_embedding_reindex_rewrites_seeded_vector(e2e_client, platform_admin, async_session_factory):
    suffix = uuid4().hex[:12]
    response = e2e_client.post('/api/admin/ai/connections', headers=platform_admin.headers, json={
        'name': f'Embedding live {suffix}', 'provider': 'openai_compatible',
        'api_key': 'fixture-key', 'endpoint': 'http://scheduler-fixtures:8080/v1',
    })
    assert response.status_code == 201, response.text
    connection_id = UUID(response.json()['id'])
    row_id = uuid4()

    async def seed():
        async with async_session_factory() as db:
            config = await db.get(AIEmbeddingConfig, 'default')
            previous = (config.connection_id, config.model, config.dimensions) if config else None
            if config:
                config.connection_id, config.model, config.dimensions = connection_id, 'fixture-embedding', 1536
            else:
                db.add(AIEmbeddingConfig(connection_id=connection_id, model='fixture-embedding', dimensions=1536))
            db.add(KnowledgeStore(id=row_id, namespace=f'thin-{suffix}', content='Thin runner reindex fixture',
                                 embedding=[0.0, 1.0] + [0.0] * 1534, created_by=platform_admin.user_id))
            await db.commit()
            return previous
    previous = asyncio.run(seed())
    try:
        response = e2e_client.post('/api/admin/llm/embedding-reindex', headers=platform_admin.headers)
        assert response.status_code == 200, response.text
        notification_id = response.json()['notification_id']

        async def find_job():
            async with async_session_factory() as db:
                job = (await db.execute(select(PlatformJob).where(
                    PlatformJob.job_type == 'embedding.reindex', PlatformJob.dedupe_key == notification_id))).scalar_one()
                return str(job.id)
        job = wait_for_job(e2e_client, platform_admin.headers, asyncio.run(find_job()), 'embedding.reindex')
        assert job['result']['processed'] >= 1 and job['result']['failed_batches'] == 0

        async def verify():
            async with async_session_factory() as db:
                row = await db.get(KnowledgeStore, row_id)
                assert row is not None and len(row.embedding) == 1536
                assert row.embedding[0] == 1.0 and row.embedding[1] == 0.0
        asyncio.run(verify())
    finally:
        async def cleanup():
            async with async_session_factory() as db:
                await db.execute(delete(KnowledgeStore).where(KnowledgeStore.id == row_id))
                config = await db.get(AIEmbeddingConfig, 'default')
                assert config is not None
                if previous:
                    config.connection_id, config.model, config.dimensions = previous
                else:
                    await db.delete(config)
                await db.flush()
                await db.execute(delete(AIProviderConnection).where(AIProviderConnection.id == connection_id))
                await db.commit()
        asyncio.run(cleanup())


def test_model_catalog_refresh_finishes(e2e_client, platform_admin, async_session_factory):
    response = e2e_client.post('/api/admin/ai/catalog/refresh', headers=platform_admin.headers)
    assert response.status_code == 202, response.text
    job = wait_for_job(e2e_client, platform_admin.headers, response.json()['job_id'], 'model_catalog.refresh')
    assert job['result']['model_count'] > 0 and job['result']['provider_count'] > 0

    async def verify():
        from src.models.orm.ai_models import AIModelCatalog
        async with async_session_factory() as db:
            row = await db.get(AIModelCatalog, 'models.dev')
            assert row is not None and row.model_count == job['result']['model_count']
    asyncio.run(verify())
