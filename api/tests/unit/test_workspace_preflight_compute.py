"""Source-only preflight recipe, snapshot privacy and result binding."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shared import workspace_preflight_compute as codec
from shared.file_compute import FileComputeResult
from shared.worker_transfer import FileTransferManifest, ReceivedFileTransfer
from src.jobs.platform.base import PlatformJobCancelled
from src.services.github_sync import GitHubSyncService, _preflight_source_snapshot, WorkspacePlanStale
from src.worker.remote import workspace_preflight as worker


def received(transfer):
    return ReceivedFileTransfer(transfer.manifest, transfer.path)


@pytest.mark.asyncio
async def test_fixed_recipe_checks_source_without_executing_it(tmp_path):
    recipe = worker.workspace_preflight_operation()
    assert recipe.name == codec.OPERATION
    assert recipe.child().kwargs['env'] == {'PATH': __import__('os').defpath}
    source = "raise RuntimeError('authored code must not execute')\nfrom bifrost import workflow\n@workflow(timeout_seconds=30)\ndef run(): pass\n"
    original_analyze = codec.analyze
    def owned_analyze(value, *, workspace):
        assert workspace == tmp_path
        return original_analyze(value, workspace=workspace)
    from unittest.mock import patch
    with patch.object(codec, 'analyze', owned_analyze), codec.encode_inputs({'sources': {'workflows/a.py': source}, 'configs': {}}) as inputs:
        output = tmp_path / 'result.json'
        receipt = await worker._preflight_instruction({'input_path': str(inputs.path),
            'input_manifest': inputs.manifest.to_dict(), 'output_path': str(output)})
        assert receipt['state'] == 'succeeded'
        manifest = FileTransferManifest.from_dict(receipt['result_manifest'], max_total_size=codec.MAX_RESULT_BYTES)
        with ReceivedFileTransfer(manifest, output) as result:
            issues = codec.decode_results(result, digest=inputs.manifest.sha256, paths={'workflows/a.py'})
        assert any(i.category == 'syntax' and i.line == 3 for i in issues)
        assert any(i.category == 'lint' for i in issues)


def test_capture_excludes_credentials_and_preserves_ruff_extension_and_target(tmp_path):
    (tmp_path/'a.py').write_text('import os\n')
    (tmp_path/'.bifrost').mkdir()
    (tmp_path/'.bifrost/configs.yaml').write_text('secret: manifest-value')
    (tmp_path/'.git').mkdir()
    (tmp_path/'.git/config').write_text('token = git-value')
    (tmp_path/'.env.private').write_text('KEY=env-value')
    (tmp_path/'pyproject.toml').write_text('[project]\nrequires-python=">=3.11"\n[tool.private]\nkey="project-secret"\n[tool.ruff]\nextend="base.toml"\n')
    (tmp_path/'base.toml').write_text('[lint]\nignore=["F401"]\n')
    value = _preflight_source_snapshot(tmp_path)
    assert set(value['sources']) == {'a.py'}
    assert set(value['configs']) == {'base.toml', 'pyproject.toml'}
    assert 'project-secret' not in str(value) and 'git-value' not in str(value) and 'manifest-value' not in str(value)
    assert not codec.analyze(value)


@pytest.mark.parametrize('path', ['../outside.py', '/root/a.py', '.git/x.py', '.bifrost/x.py', '.env/x.py', 'a\\b.py', 'a.txt'])
def test_rejects_non_source_and_unsafe_paths(path):
    with pytest.raises(ValueError):
        codec.encode_inputs({'sources': {path: 'pass'}, 'configs': {}})


@pytest.mark.parametrize('extension', ['/etc/credentials', '../../outside.toml', '$HOME/.secret'])
def test_rejects_ruff_extensions_outside_snapshot(extension):
    with pytest.raises(ValueError):
        codec.encode_inputs({'sources': {'a.py': 'pass'}, 'configs': {'ruff.toml': f'extend="{extension}"'}})


@pytest.mark.parametrize('fault', ['digest', 'path', 'category', 'severity', 'count'])
def test_result_rejects_foreign_or_invalid_diagnostics(fault, monkeypatch):
    from src.models.contracts.github import PreflightIssue
    issue = PreflightIssue(path='a.py', message='syntax', severity='error', category='syntax')
    if fault == 'path':
        issue.path = 'another.py'
    if fault == 'category':
        issue.category = 'manifest'
    if fault == 'severity':
        issue.severity = 'warning'
    if fault == 'count':
        monkeypatch.setattr(codec, 'MAX_ISSUES', 0)
    with codec.encode_results([issue], 'a'*64) as transfer:
        with pytest.raises(ValueError):
            codec.decode_results(received(transfer), digest='b'*64 if fault == 'digest' else 'a'*64, paths={'a.py'})


@pytest.mark.asyncio
async def test_cancellation_closes_owned_input():
    paths = []
    async def cancelled(operation, transfer):
        paths.append(transfer.path)
        raise PlatformJobCancelled
    with pytest.raises(PlatformJobCancelled):
        await codec.analyze_using({'sources': {'a.py': 'pass'}, 'configs': {}}, cancelled)
    assert paths and not paths[0].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('change_source', [False, True])
async def test_git_preflight_releases_read_scope_and_keeps_manifest_data_in_api(tmp_path, change_source):
    (tmp_path/'a.py').write_text('pass\n')
    (tmp_path/'.bifrost').mkdir()
    (tmp_path/'.bifrost/configs.yaml').write_text('configs: {}\n')
    active = True
    async def rollback():
        nonlocal active
        active = False
    async def compute(operation, transfer):
        assert operation == codec.OPERATION and not active
        value = codec.decode_inputs(received(transfer))
        assert set(value) == {'sources', 'configs'} and not value['configs']
        if change_source:
            (tmp_path/'a.py').write_text('new_value = 1\n')
        result = codec.encode_results(codec.analyze(value), transfer.manifest.sha256)
        return FileComputeResult(received(result), {})
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.db = SimpleNamespace(in_transaction=lambda: active, new=set(), dirty=set(), deleted=set(), rollback=AsyncMock(side_effect=rollback))
    service._job_context = SimpleNamespace(file_compute=compute)
    if change_source:
        with pytest.raises(WorkspacePlanStale):
            await service._run_preflight(tmp_path)
    else:
        assert (await service._run_preflight(tmp_path)).valid
    service.db.rollback.assert_awaited_once()


@pytest.mark.parametrize('target', ['.bifrost/secret.py', '.env.private'])
def test_alias_cannot_transfer_reserved_workspace_content(tmp_path, target):
    secret = tmp_path / target
    secret.parent.mkdir(exist_ok=True)
    secret.write_text('private value')
    (tmp_path/'alias.py').symlink_to(secret)
    with pytest.raises(ValueError):
        _preflight_source_snapshot(tmp_path)


@pytest.mark.parametrize('bound', ['MAX_FILES', 'MAX_FILE_BYTES', 'MAX_SOURCE_BYTES', 'MAX_INPUT_BYTES'])
def test_source_transfer_is_bounded(monkeypatch, bound):
    monkeypatch.setattr(codec, bound, 1)
    with pytest.raises(ValueError):
        codec.encode_inputs({'sources': {'a.py': 'pass', 'b.py': 'pass'}, 'configs': {}})


@pytest.mark.asyncio
async def test_preflight_refuses_pending_database_mutations(tmp_path):
    (tmp_path/'a.py').write_text('pass')
    compute = AsyncMock()
    service = GitHubSyncService.__new__(GitHubSyncService)
    service.db = SimpleNamespace(in_transaction=lambda: True, new={'pending'}, dirty=set(), deleted=set())
    service._job_context = SimpleNamespace(file_compute=compute)
    from src.jobs.platform.base import PlatformJobFailure
    with pytest.raises(PlatformJobFailure) as error:
        await service._run_preflight(tmp_path)
    assert error.value.code == 'git_compute_transaction_open'
    compute.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_lint_configuration_is_a_preflight_issue(tmp_path):
    (tmp_path/'a.py').write_text('pass')
    (tmp_path/'pyproject.toml').write_text('[tool.ruff')
    service = GitHubSyncService.__new__(GitHubSyncService)
    service._job_context = None
    result = await service._run_preflight(tmp_path)
    assert not result.valid
    assert any('TOML' in issue.message for issue in result.issues)


@pytest.mark.asyncio
async def test_git_job_without_compute_refuses_before_loading_credentials(monkeypatch):
    from src.jobs.platform import git_operation as git
    from src.jobs.platform.base import PlatformJobFailure
    credentials = AsyncMock()
    monkeypatch.setattr(git, 'get_github_config', credentials)
    context = SimpleNamespace(file_compute=None, checkpoint=None, organization_id=None, report=AsyncMock())
    with pytest.raises(PlatformJobFailure) as error:
        await git.run_git_operation(context, git.GitOperationPayload(operation='status'))
    assert error.value.code == 'file_compute_required'
    credentials.assert_not_awaited()
