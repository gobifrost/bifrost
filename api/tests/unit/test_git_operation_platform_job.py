"""Workspace Git platform-job dispatch contracts."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest

from src.jobs.platform.git_operation import (
    GIT_OPERATION_DEFINITION,
    GitOperationPayload,
    dispatch_git_operation,
    run_git_operation,
)
from src.models.contracts.github import GitConnectRequest, SyncResult, WorkspaceSyncPlan
from src.services.github_config import GitHubConfig, _get_fernet


def _context(**kwargs):
    defaults = dict(job_id=uuid4(), checkpoint=None, report=AsyncMock(),
        authorize_effect=AsyncMock(return_value=True), checkpoint_in_transaction=AsyncMock())
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_git_payload_version_fences_old_executors_and_retains_queued_schema():
    assert GIT_OPERATION_DEFINITION.payload_version == 4
    assert GIT_OPERATION_DEFINITION.payload_model_for_version(1) is GitOperationPayload
    assert GIT_OPERATION_DEFINITION.payload_model_for_version(2) is GitOperationPayload
    assert GIT_OPERATION_DEFINITION.payload_model_for_version(3) is GitOperationPayload
    assert GIT_OPERATION_DEFINITION.payload_model_for_version(4) is GitOperationPayload


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{}, {"connection_generation": "old", "connection_repository": "owner/old"}])
async def test_app_jobs_reject_unbound_or_replaced_credentials_before_git(monkeypatch, options):
    from src.jobs.platform.base import PlatformJobFailure
    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace(commit=AsyncMock(), get=AsyncMock(return_value=SimpleNamespace(result=None)))
    monkeypatch.setattr("src.jobs.platform.git_operation.get_db_context", db_context)
    config = GitHubConfig("owner/current", None, "main", "connected", auth_kind="app", generation="new", app={"id": 42})
    monkeypatch.setattr("src.jobs.platform.git_operation.get_github_config", AsyncMock(return_value=config))
    service = Mock()
    monkeypatch.setattr("src.jobs.platform.git_operation.GitHubSyncService", service)
    context = _context(organization_id=None, checkpoint=None, report=AsyncMock())
    with pytest.raises(PlatformJobFailure) as error:
        await run_git_operation(context, GitOperationPayload(operation="sync", options=options))
    assert error.value.code == "github_connection_changed"
    service.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_version", [1, 2, 3])
async def test_old_git_runner_refuses_app_aware_workspace_jobs(monkeypatch, legacy_version):
    from src.jobs.platform import runner

    job_id, lease_token = uuid4(), uuid4()
    job = SimpleNamespace(id=job_id, status="running", job_type="workspace.git", payload_version=4)
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = job

    @asynccontextmanager
    async def context():
        yield db

    handler = AsyncMock()
    legacy = replace(
        GIT_OPERATION_DEFINITION, payload_version=legacy_version, historical_payload_models={}, handler=handler,
    )
    monkeypatch.setattr(runner, "get_db_context", context)
    monkeypatch.setattr(runner, "get_platform_job_definition", Mock(return_value=legacy))
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr(runner, "finish_platform_job", finish)
    assert not await runner.run_claimed_platform_job(job_id, lease_token)
    handler.assert_not_awaited()
    assert finish.await_args.kwargs["error_code"] == "unsupported_payload_version"


@pytest.mark.asyncio
async def test_git_job_calls_sync_service_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace Git work runs in the durable job, not through Scheduler."""
    sync = AsyncMock(return_value=SyncResult(success=True))

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace(commit=AsyncMock(), get=AsyncMock(return_value=SimpleNamespace(result=None)))

    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_db_context", db_context,
    )
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_github_config",
        AsyncMock(
            return_value=GitHubConfig(
                token="token", repo_url="owner/repo", branch="main", status="connected"
            )
        ),
    )
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.GitHubSyncService",
        lambda **_kwargs: SimpleNamespace(desktop_sync=sync),
    )
    monkeypatch.setattr("src.core.repo_dirty.clear_repo_dirty", AsyncMock())

    context = _context(
        job_id=uuid4(),
        checkpoint=None,
        organization_id=uuid4(),
        report=AsyncMock(),
    )
    result = await run_git_operation(context, GitOperationPayload(operation="sync"))

    assert result["success"] is True
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_git_job_passes_server_validated_retry_plan_to_sync_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A publication retry reaches desktop_sync as the exact durable plan."""
    plan = WorkspaceSyncPlan(
        merge_sha="a" * 40,
        workspace_fingerprint="b" * 64,
        db_applied=True,
    )
    sync = AsyncMock(return_value=SyncResult(success=True))

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace(commit=AsyncMock(), get=AsyncMock(return_value=SimpleNamespace(result=None)))

    monkeypatch.setattr("src.jobs.platform.git_operation.get_db_context", db_context)
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_github_config",
        AsyncMock(return_value=GitHubConfig(token="token", repo_url="owner/repo", branch="main", status="connected")),
    )
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.GitHubSyncService",
        lambda **_kwargs: SimpleNamespace(desktop_sync=sync),
    )
    monkeypatch.setattr(
        "src.core.repo_dirty.clear_repo_dirty", AsyncMock(),
    )

    context = _context(job_id=uuid4(), organization_id=uuid4(), checkpoint=None, report=AsyncMock())
    await run_git_operation(
        context,
        GitOperationPayload(
            operation="sync", options={"retry_plan": plan.model_dump(mode="json")}
        ),
    )

    assert sync.await_args.kwargs["retry_plan"] == plan


@pytest.mark.asyncio
async def test_git_job_dispatches_reviewed_connect_through_the_existing_workspace_job() -> None:
    """First connection is another typed workspace.git operation, not a bespoke worker."""
    connect = AsyncMock(return_value=SyncResult(success=True))
    service = SimpleNamespace(desktop_connect=connect)
    context = _context(
        requested_by_user_id="requester",
        organization_id=None,
        checkpoint=None,
        report=AsyncMock(),
    )

    result = await dispatch_git_operation(
        service,
        GitOperationPayload(operation="connect", options={"request": GitConnectRequest(
            preview_token="preview", strategy="publish_local",
        ).model_dump(mode="json")}),
        context,
    )

    assert result["success"] is True
    assert connect.await_args.args[0].preview_token == "preview"
    assert connect.await_args.kwargs["requested_by_user_id"] == "requester"


@pytest.mark.asyncio
async def test_connect_job_revalidates_its_preview_and_persists_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable runner is the only place that marks a reviewed repo connected."""
    connect = AsyncMock(return_value=SyncResult(success=True))
    monkeypatch.setattr("src.jobs.platform.git_operation.lock_github_config", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.git_operation.validate_current_connection", AsyncMock())
    save = AsyncMock()

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace(commit=AsyncMock(), get=AsyncMock(return_value=SimpleNamespace(result=None)))

    monkeypatch.setattr("src.jobs.platform.git_operation.get_db_context", db_context)
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_github_config",
        AsyncMock(return_value=GitHubConfig(token="token", repo_url=None, branch="main", status="connected")),
    )
    class ServiceFactory:
        load_connect_preview = AsyncMock(return_value=SimpleNamespace(
            repository_url="https://github.com/acme/workspace", branch="main",
            app_setup_id=None,
        ))

        def __new__(cls, **_kwargs):
            return SimpleNamespace(desktop_connect=connect)

    monkeypatch.setattr(
        "src.jobs.platform.git_operation.GitHubSyncService",
        ServiceFactory,
    )
    monkeypatch.setattr("src.jobs.platform.git_operation.save_github_config", save)

    organization_id = uuid4()
    context = _context(
        job_id=uuid4(),
        organization_id=organization_id,
        requested_by_user_id="requester",
        requested_by_email="admin@example.com",
        report=AsyncMock(),
    )
    result = await run_git_operation(
        context,
        GitOperationPayload(
            operation="connect",
            options={"request": {"preview_token": "preview", "strategy": "publish_local"}},
        ),
    )

    assert result["success"] is True
    save.assert_awaited_once_with(
        db=ANY,
        org_id=organization_id,
        token="token",
        repo_url="https://github.com/acme/workspace",
        branch="main",
        updated_by="admin@example.com",
        commit=False,
    )


@pytest.mark.asyncio
async def test_admitted_connect_job_survives_setup_expiry_before_execution(monkeypatch):
    monkeypatch.setattr("src.jobs.platform.git_operation.lock_github_config", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.git_operation.validate_current_connection", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.git_operation.cleanup_setup_credentials", AsyncMock())
    setup_id = uuid4()
    payload = {
        "stage": "ready", "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "app": {"id": 42}, "installation_id": 7, "workflows": False,
        "repository": {"id": 91, "full_name": "owner/repo", "default_branch": "main"},
    }
    row = SimpleNamespace(created_by="requester", value_bytes=_get_fernet().encrypt(json.dumps(payload).encode()))
    db = SimpleNamespace(commit=AsyncMock(), get=AsyncMock(return_value=SimpleNamespace(result=None)), execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)))

    @asynccontextmanager
    async def db_context():
        yield db

    monkeypatch.setattr("src.jobs.platform.git_operation.get_db_context", db_context)
    monkeypatch.setattr("src.jobs.platform.git_operation.get_github_config", AsyncMock(return_value=None))
    monkeypatch.setattr("shared.github_git_app.GitAppCredentials.get_token", AsyncMock(return_value="installation-secret"))
    connect = AsyncMock(return_value=SyncResult(success=True))

    class ServiceFactory:
        load_connect_preview = AsyncMock(return_value=SimpleNamespace(
            repository_url="https://github.com/owner/repo", branch="main", app_setup_id=setup_id,
        ))

        def __new__(cls, **_kwargs):
            return SimpleNamespace(desktop_connect=connect)

    monkeypatch.setattr("src.jobs.platform.git_operation.GitHubSyncService", ServiceFactory)
    activate = AsyncMock()
    monkeypatch.setattr("src.jobs.platform.git_operation.activate", activate)
    context = _context(organization_id=None, requested_by_user_id="requester", requested_by_email="actor@example.com", report=AsyncMock())
    result = await run_git_operation(context, GitOperationPayload(operation="connect", options={"request": {
        "app_setup_id": str(setup_id), "preview_token": "valid-preview", "strategy": "publish_local",
    }}))

    assert result["success"]
    assert activate.await_args.args[1].setup_executing
    connect.assert_awaited_once()


PAT = "ghp_jobTestSecretToken0123456789"


async def _run_with_service(
    monkeypatch: pytest.MonkeyPatch, service: SimpleNamespace, operation: str,
) -> dict:
    captured: dict = {}

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace(commit=AsyncMock(), get=AsyncMock(return_value=SimpleNamespace(result=None)))

    def build_service(**kwargs):
        captured.update(kwargs)
        return service

    monkeypatch.setattr("src.jobs.platform.git_operation.get_db_context", db_context)
    monkeypatch.setattr(
        "src.jobs.platform.git_operation.get_github_config",
        AsyncMock(return_value=GitHubConfig(token=PAT, repo_url="owner/repo", branch="main", status="connected")),
    )
    monkeypatch.setattr("src.jobs.platform.git_operation.GitHubSyncService", build_service)
    context = _context(job_id=uuid4(), organization_id=uuid4(), checkpoint=None, report=AsyncMock())
    await run_git_operation(context, GitOperationPayload(operation=operation))  # type: ignore[arg-type]
    return captured


@pytest.mark.asyncio
async def test_git_job_keeps_the_token_out_of_the_remote_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.models.contracts.github import WorkingTreeStatus

    service = SimpleNamespace(desktop_status=AsyncMock(return_value=WorkingTreeStatus()))

    captured = await _run_with_service(monkeypatch, service, "status")

    assert captured["repo_url"] == "https://github.com/owner/repo.git"
    assert captured["token"] == PAT


@pytest.mark.asyncio
async def test_git_job_redacts_the_token_from_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform.base import PlatformJobFailure
    from src.models.contracts.github import CommitResult

    leaked = f"fatal: https://x-access-token:{PAT}@github.com/owner/repo.git denied"
    service = SimpleNamespace(
        desktop_commit=AsyncMock(return_value=CommitResult(success=False, error=leaked)),
    )

    with pytest.raises(PlatformJobFailure) as failure:
        await _run_with_service(monkeypatch, service, "commit")

    assert PAT not in failure.value.message
    assert PAT not in str(failure.value.result)


@pytest.mark.asyncio
async def test_workspace_job_wires_attempt_fence_before_retained_publication(monkeypatch):
    from src.jobs.platform.base import PlatformJobCancelled
    @asynccontextmanager
    async def database():
        yield object()
    monkeypatch.setattr('src.jobs.platform.git_operation.get_db_context', database)
    monkeypatch.setattr('src.jobs.platform.git_operation.get_github_config', AsyncMock(
        return_value=GitHubConfig(token='fixture', repo_url='owner/repo', branch='main', status='connected')))
    writes = []
    def service(**kwargs):
        async def sync(**_args):
            async with kwargs['workspace_authorization']():
                writes.append('published')
            return SyncResult(success=True)
        return SimpleNamespace(desktop_sync=sync)
    monkeypatch.setattr('src.jobs.platform.git_operation.GitHubSyncService', service)
    context = _context(job_id=uuid4(), organization_id=None, checkpoint=None, report=AsyncMock(),
        authorize_effect=AsyncMock(return_value=False))
    with pytest.raises(PlatformJobCancelled):
        await run_git_operation(context, GitOperationPayload(operation='sync'))
    context.authorize_effect.assert_awaited_once()
    assert not writes


@pytest.mark.asyncio
@pytest.mark.parametrize('method,args', [
    ('desktop_fetch', ()), ('desktop_status', ()), ('desktop_commit', ('message',)),
    ('desktop_sync', ()), ('desktop_abort_merge', ()), ('desktop_resolve', ({},)),
    ('desktop_diff', ('file.py',)), ('desktop_discard', (['file.py'],)),
])
async def test_git_wrappers_preserve_canonical_cancellation(monkeypatch, method, args):
    from src.jobs.platform.base import PlatformJobCancelled
    from src.services.github_sync import GitHubSyncService
    @asynccontextmanager
    async def cancelled(*args, **kwargs):
        raise PlatformJobCancelled
        yield
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.repo_manager = SimpleNamespace(workspace=cancelled, checkout=cancelled)
    with pytest.raises(PlatformJobCancelled):
        await getattr(service, method)(*args)


@pytest.mark.asyncio
async def test_sync_resumes_canonical_committed_import_plan_without_repreparing():
    plan = WorkspaceSyncPlan(merge_sha="a"*40, workspace_fingerprint="b"*64,
        db_applied=True, checkpoint_id=str(uuid4()))
    service = SimpleNamespace(desktop_sync=AsyncMock(return_value=SyncResult(success=True)))
    context = _context(report=AsyncMock(), checkpoint={"retry_plan": plan.model_dump(mode="json")})
    await dispatch_git_operation(service, GitOperationPayload(operation="sync"), context)
    assert service.desktop_sync.await_args.kwargs["retry_plan"] == plan


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong_job', [False, True])
async def test_completed_sync_receipt_skips_credentials_and_git_effects(monkeypatch, wrong_job):
    from src.jobs.platform.base import PlatformJobFailure
    job_id = uuid4()
    receipt = {'git_publication_complete': True, 'job_id': str(uuid4() if wrong_job else job_id),
        'checkpoint_id': str(uuid4()), 'result': SyncResult(success=True, pushed_commits=1).model_dump(mode='json')}
    @asynccontextmanager
    async def publication():
        yield
    cleanup = AsyncMock()
    monkeypatch.setattr('src.services.git_repo_manager.GitRepoManager',
        lambda *_args, **_kwargs: SimpleNamespace(authorized_publication=publication, delete_workspace_checkpoint=cleanup))
    credentials = AsyncMock(side_effect=AssertionError('completed sync must not access credentials'))
    monkeypatch.setattr('src.jobs.platform.git_operation.get_github_config', credentials)
    context = _context(job_id=job_id, organization_id=None, checkpoint=receipt, report=AsyncMock())
    if wrong_job:
        with pytest.raises(PlatformJobFailure, match='another job'):
            await run_git_operation(context, GitOperationPayload(operation='sync'))
        cleanup.assert_not_awaited()
    else:
        result = await run_git_operation(context, GitOperationPayload(operation='sync'))
        assert result['success']
        cleanup.assert_awaited_once_with(receipt['checkpoint_id'])
    credentials.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong_job', [False, True])
@pytest.mark.parametrize('requires_review', [False, True])
async def test_connect_publication_receipt_skips_expired_preview_and_git(monkeypatch, wrong_job, requires_review):
    from src.jobs.platform.base import PlatformJobFailure, PlatformJobRequiresAction
    job_id = uuid4()
    target = {'repository_url': 'https://github.com/owner/repo', 'branch': 'main'}
    receipt = {'git_connect_ready' if requires_review else 'git_publication_complete': True,
        'operation': 'connect', 'job_id': str(uuid4() if wrong_job else job_id), 'connection': target,
        'result': SyncResult(success=not requires_review,
            requires_action='confirm_deletes' if requires_review else None).model_dump(mode='json')}
    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()
    monkeypatch.setattr('src.jobs.platform.git_operation.get_db_context', db_context)
    monkeypatch.setattr('src.jobs.platform.git_operation.get_github_config', AsyncMock(
        return_value=GitHubConfig(token='fixture', repo_url=None, branch='main', status='connected')))
    service = Mock(side_effect=AssertionError('must not replay Git after publication'))
    monkeypatch.setattr('src.jobs.platform.git_operation.GitHubSyncService', service)
    activation = AsyncMock()
    monkeypatch.setattr('src.jobs.platform.git_operation._activate_connection', activation)
    context = _context(job_id=job_id, organization_id=None, checkpoint=receipt)
    if wrong_job:
        with pytest.raises(PlatformJobFailure) as error:
            await run_git_operation(context, GitOperationPayload(operation='connect'))
        assert error.value.code == 'git_checkpoint_mismatch'
        activation.assert_not_awaited()
    else:
        if requires_review:
            with pytest.raises(PlatformJobRequiresAction) as action:
                await run_git_operation(context, GitOperationPayload(operation='connect'))
            assert action.value.result['requires_action'] == 'confirm_deletes'
        else:
            assert (await run_git_operation(context, GitOperationPayload(operation='connect')))['success']
        assert activation.await_args.args[2] == target
    service.assert_not_called()


@pytest.mark.asyncio
async def test_connect_import_retry_uses_canonical_source_instead_of_expired_preview():
    from src.services.github_sync import GitHubSyncService
    plan = WorkspaceSyncPlan(merge_sha='a'*40, workspace_fingerprint='b'*64,
        checkpoint_id=str(uuid4()), db_applied=True)
    service = GitHubSyncService.__new__(GitHubSyncService)
    service._job_context = _context(checkpoint={'retry_plan': plan.model_dump(mode='json')})
    service.load_connect_preview = AsyncMock(side_effect=AssertionError('preview already accepted'))
    service.desktop_sync = AsyncMock(return_value=SyncResult(success=True))
    result = await service.desktop_connect(GitConnectRequest(preview_token='expired', strategy='publish_local'),
        requested_by_user_id='fixture', organization_id=None)
    assert result.success
    service.load_connect_preview.assert_not_awaited()
    assert service.desktop_sync.await_args.kwargs['retry_plan'] == plan


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['sync', 'connect'])
async def test_connect_publication_failure_preserves_the_canonical_target_binding(monkeypatch, operation):
    from src.jobs.platform import git_operation as git
    from src.jobs.platform.base import PlatformJobFailure
    plan = WorkspaceSyncPlan(merge_sha='a'*40, workspace_fingerprint='b'*64,
        checkpoint_id=str(uuid4()), db_applied=True)
    result = SyncResult(success=False, retryable=True, retry_plan=plan, error='push unavailable').model_dump(mode='json')
    @asynccontextmanager
    async def database():
        yield SimpleNamespace()
    monkeypatch.setattr(git, 'get_db_context', database)
    monkeypatch.setattr(git, 'get_github_config', AsyncMock(return_value=GitHubConfig(
        repo_url=None if operation == 'connect' else 'owner/repo', token='fixture', branch='main', status='connected')))
    class Service:
        load_connect_preview = AsyncMock(return_value=SimpleNamespace(
            app_setup_id=None, repository_url='https://github.com/owner/repo', branch='main'))
        def __init__(self, **kwargs):
            pass
    monkeypatch.setattr(git, 'GitHubSyncService', Service)
    monkeypatch.setattr(git, 'dispatch_git_operation', AsyncMock(return_value=result))
    context = _context(organization_id=None, requested_by_user_id='fixture')
    with pytest.raises(PlatformJobFailure) as error:
        await run_git_operation(context, GitOperationPayload(operation=operation,
            options={'request': {'preview_token': 'accepted'}}))
    assert error.value.code == 'git_operation_failed'
    assert error.value.result == result
    assert error.value.preserve_checkpoint == (operation == 'connect')
