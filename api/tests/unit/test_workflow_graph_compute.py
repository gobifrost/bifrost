"""Pure worker graph parity, bounded resolution and API publication fencing."""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shared import workflow_graph_compute as codec
from shared.file_compute import FileComputeResult
from shared.workflow_conversion_analysis import SourceFile, analyze_workflow, summarize_source, MAX_SOURCE_BYTES
from shared.worker_transfer import ReceivedFileTransfer, FileTransferManifest
from src.services.workflow_graph_compute import prepare_workflow_graph
from src.worker.remote import workflow_graph_analyze as recipe


def source(path, text, *, prefix=''):
    return SourceFile(prefix+path, path, text, hashlib.sha256(text.encode()).hexdigest())


async def compute_round(operation, transfer):
    assert operation == codec.OPERATION
    result = await codec.analyze_round(codec.decode_inputs(ReceivedFileTransfer(transfer.manifest, transfer.path)))
    encoded = codec.encode_results(result, transfer.manifest.sha256)
    return FileComputeResult(ReceivedFileTransfer(encoded.manifest, encoded.path), {})


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['helper', 'package', 'cycle', 'missing', 'syntax', 'oversize', 'namespace'])
async def test_worker_rounds_match_local_graph_without_executing_source(case, tmp_path):
    entry = source('run.py', 'from helpers.work import work\ndef run(): return work()\n')
    helper = source('helpers/work.py', 'from bifrost import tables\ndef work(): return tables.get("orders")\n')
    package = source('helpers/__init__.py', 'from bifrost import roles\nroles.list()\n')
    modules = {'helpers.work': helper}
    if case == 'package':
        modules['helpers'] = package
    elif case == 'cycle':
        helper = source('helpers/work.py', 'from run import run\ndef work(): return run()\n')
        modules.update({'helpers.work': helper, 'run': entry})
    elif case == 'missing':
        modules.clear()
    elif case == 'syntax':
        modules['helpers.work'] = source('helpers/work.py', 'def broken(:')
    elif case == 'oversize':
        modules['helpers.work'] = source('helpers/work.py', '#'*(MAX_SOURCE_BYTES+1))
    elif case == 'namespace':
        key = 'helpers/'
        modules['helpers'] = SourceFile(key, 'helpers', '', hashlib.sha256(f'namespace:{key}'.encode()).hexdigest(), 'namespace')
    marker = tmp_path / 'must-not-execute'
    helper = modules.get('helpers.work')
    if helper and case not in {'syntax', 'oversize'}:
        modules['helpers.work'] = source(helper.path, f'open({str(marker)!r}, "w").write("executed")\n'+helper.source)
    async def resolve(_source, module, level):
        assert level in (0, 1)
        return modules.get(module)
    async def summarize(sf):
        return summarize_source(sf.source)
    expected = await analyze_workflow(entry, 'run', resolve, summarize)
    prepared = await prepare_workflow_graph(entry, 'run', frozenset(), resolve, compute_round)
    assert prepared.analysis == expected
    assert not marker.exists()
    assert all(not sf.source for sf in prepared.sources.values() if sf.key in prepared.summaries)


@pytest.mark.asyncio
async def test_fixed_child_recipe_returns_bound_source_graph_with_no_platform_environment(tmp_path):
    operation = recipe.workflow_graph_operation()
    assert operation.name == codec.OPERATION
    assert operation.child().kwargs['env'].keys() == {'PATH'}
    assert operation.workspace_max_bytes == codec.MAX_TRANSFER_BYTES
    entry = source('run.py', 'def run(): return 1')
    snapshot = codec.snapshot(entry, 'run', frozenset())
    with codec.encode_inputs(snapshot) as transfer:
        output = tmp_path/'graph.json'
        receipt = await recipe._graph_instruction({'input_path': str(transfer.path),
            'input_manifest': transfer.manifest.to_dict(), 'output_path': str(output)})
        assert receipt['state'] == 'succeeded'
        manifest = FileTransferManifest.from_dict(receipt['result_manifest'], max_total_size=codec.MAX_TRANSFER_BYTES)
        with ReceivedFileTransfer(manifest, output) as result:
            decoded = codec.decode_results(result, digest=transfer.manifest.sha256, snapshot=snapshot)
        assert decoded['analysis']['entrypoint_found'] and not decoded['requests']


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['input', 'namespace', 'answered', 'hash', 'summary'])
async def test_worker_result_cannot_rebind_or_widen_captured_dependencies(fault):
    entry = source('run.py', 'def run(): return 1')
    snapshot = codec.snapshot(entry, 'run', frozenset())
    result = await codec.analyze_round(snapshot)
    digest = 'a'*64
    if fault == 'input':
        digest = 'b'*64
    elif fault == 'namespace':
        result['requests'] = [['uncaptured/source.py', 'secret', 0]]
    elif fault == 'answered':
        snapshot['resolutions'] = [[entry.key, 'known', 0, None]]
        result['requests'] = [[entry.key, 'known', 0]]
    elif fault == 'hash':
        result['analysis']['dependencies'][entry.key] = 'f'*64
    else:
        snapshot['summaries'] = result['summaries'].copy()
        result['summaries'] = {entry.key: {'version': 'wrong'}}
    with codec.encode_results(result, digest) as transfer:
        with pytest.raises(ValueError):
            codec.decode_results(ReceivedFileTransfer(transfer.manifest, transfer.path), digest='a'*64, snapshot=snapshot)


@pytest.mark.asyncio
async def test_api_resolver_scope_is_preserved_and_no_storage_client_reaches_compute():
    entry = source('run.py', 'from helpers import work\ndef run(): return work()\n', prefix='_solutions/install/')
    helper = source('helpers.py', 'from bifrost import roles\ndef work(): return roles.list()\n', prefix='_solutions/install/')
    seen = []
    async def resolve(sf, module, level):
        assert sf.key.startswith('_solutions/install/')
        seen.append(module)
        return helper if module == 'helpers' else None
    prepared = await prepare_workflow_graph(entry, 'run', frozenset(), resolve, compute_round)
    assert 'helpers' in seen
    assert prepared.analysis['sdk_calls'] == ['bifrost.roles.list']
    assert set(prepared.analysis['dependencies']) == {entry.key, helper.key}


@pytest.mark.asyncio
async def test_cancellation_closes_transfer_and_does_not_resolve_more_source():
    paths = []
    async def compute(_operation, transfer):
        paths.append(transfer.path)
        raise asyncio.CancelledError
    resolver = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await prepare_workflow_graph(source('run.py', 'def run(): pass'), 'run', frozenset(), resolver, compute)
    assert paths and not paths[0].exists()
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_analysis_without_compute_fails_before_reading_inventory(monkeypatch):
    from shared import workflow_conversions as conversion
    from src.jobs.platform.base import PlatformJobFailure
    read = AsyncMock()
    monkeypatch.setattr(conversion, 'read_runtime_modules', read)
    with pytest.raises(PlatformJobFailure, match='source graph compute'):
        await conversion.run_analysis(SimpleNamespace(file_compute=None), object())
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_rounds_reuse_summaries_without_retransmitting_raw_source(monkeypatch):
    entry = source('run.py', 'from helper import work\ndef run(): return work()')
    helper = source('helper.py', 'def work(): return 1')
    parsed, raw = [], []
    from shared import workflow_conversion_analysis as analyzer
    original = analyzer.summarize_source
    def summarize(text):
        parsed.append(text)
        return original(text)
    monkeypatch.setattr(analyzer, 'summarize_source', summarize)
    async def compute(operation, transfer):
        captured = codec.decode_inputs(ReceivedFileTransfer(transfer.manifest, transfer.path))
        raw.extend(item['key'] for item in captured['files'] if item['source'])
        return await compute_round(operation, transfer)
    async def resolve(_source, module, _level):
        return helper if module == 'helper' else None
    await prepare_workflow_graph(entry, 'run', frozenset(), resolve, compute)
    assert raw.count(entry.key) == raw.count(helper.key) == 1
    assert len(parsed) == 2


@pytest.mark.asyncio
async def test_scope_preparation_closes_database_scope_before_compute(monkeypatch):
    from contextlib import asynccontextmanager
    from shared import workflow_conversions as conversion
    from shared.runtime_modules import RuntimeModuleInventory
    active = 0
    @asynccontextmanager
    async def db_context():
        nonlocal active
        active += 1
        try:
            yield object()
        finally:
            active -= 1
    async def compute(operation, transfer):
        assert active == 0
        return await compute_round(operation, transfer)
    monkeypatch.setattr(conversion, 'get_db_context', db_context)
    monkeypatch.setattr(conversion, '_require_current_conversion_admin', AsyncMock())
    workflow = SimpleNamespace(solution_id=None, function_name='run')
    prepared = await conversion._prepare_source_graph(SimpleNamespace(file_compute=compute), workflow,
        source('run.py', 'def run(): return 1'), RuntimeModuleInventory(frozenset(), True))
    assert prepared.allow_outbound_access
    assert prepared.analysis['entrypoint_found'] and active == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('refusal', ['owner', 'metadata'])
async def test_report_publication_refuses_changed_owner_or_workflow_after_compute(monkeypatch, refusal):
    from contextlib import asynccontextmanager
    from datetime import datetime, UTC
    from uuid import uuid4
    from unittest.mock import Mock
    from shared import workflow_conversions as conversion
    from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
    from tests.unit.test_workflow_conversion_service import _workflow
    workflow = _workflow()
    workflow.created_at = datetime.now(UTC)
    batch = Mock()
    batch.scalars.return_value = [workflow.id]
    db = SimpleNamespace(execute=AsyncMock(return_value=batch), get=AsyncMock(return_value=workflow),
        add=Mock(), commit=AsyncMock())
    @asynccontextmanager
    async def database():
        yield db
    monkeypatch.setattr(conversion, 'get_db_context', database)
    monkeypatch.setattr(conversion, '_require_current_conversion_admin', AsyncMock())
    monkeypatch.setattr(conversion, 'read_runtime_modules', AsyncMock())
    monkeypatch.setattr(conversion, '_source_for_workflow', AsyncMock(return_value=object()))
    async def prepare(*_args):
        if refusal == 'metadata':
            workflow.function_name = 'changed'
        return SimpleNamespace(analysis={}, summaries={}, sources={}, allow_outbound_access=True)
    monkeypatch.setattr(conversion, '_prepare_source_graph', prepare)
    monkeypatch.setattr(conversion, '_fenced_job', AsyncMock(side_effect=PlatformJobCancelled if refusal == 'owner' else None))
    report = AsyncMock()
    monkeypatch.setattr(conversion, '_report_for_workflow', report)
    context = SimpleNamespace(job_id=uuid4(), file_compute=AsyncMock(),
        checkpoint={'total': 1, 'completed': 0}, save_checkpoint=AsyncMock(),
        checkpoint_in_transaction=AsyncMock(), report=AsyncMock())
    payload = SimpleNamespace(workflow_ids=[workflow.id], scope='all', platform_cutover=False)
    with pytest.raises(PlatformJobCancelled if refusal == 'owner' else PlatformJobFailure):
        await conversion.run_analysis(context, payload)
    report.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    context.checkpoint_in_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_dependency_limit_remains_a_conservative_finding_not_a_transport_failure(monkeypatch):
    from shared import workflow_conversion_analysis as analyzer
    monkeypatch.setattr(analyzer, 'MAX_DEPENDENCIES', 2)
    entry = source('run.py', 'from first import work\ndef run(): return work()')
    modules = {
        'first': source('first.py', 'from second import next_step\ndef work(): return next_step()'),
        'second': source('second.py', 'def next_step(): return 1'),
    }
    async def resolve(_source, module, _level):
        return modules.get(module)
    async def summarize(sf):
        return summarize_source(sf.source)
    expected = await analyze_workflow(entry, 'run', resolve, summarize)
    prepared = await prepare_workflow_graph(entry, 'run', frozenset(), resolve, compute_round)
    assert prepared.analysis == expected
    assert any('dependency limit reached' in item for item in prepared.analysis['unknowns'])


@pytest.mark.asyncio
async def test_api_capture_refuses_two_versions_of_the_same_source_key():
    entry = source('run.py', 'from a import first\nfrom b import second\ndef run(): return first()+second()')
    async def resolve(_source, module, _level):
        return source('shared.py', f'def {"first" if module == "a" else "second"}(): return 1')
    with pytest.raises(ValueError, match='Dependency changed'):
        await prepare_workflow_graph(entry, 'run', frozenset(), resolve, compute_round)


@pytest.mark.asyncio
async def test_evidence_cannot_name_an_uncaptured_file():
    entry = source('run.py', 'from bifrost import roles\ndef run(): return roles.list()')
    state = codec.snapshot(entry, 'run', frozenset())
    result = await codec.analyze_round(state)
    result['analysis']['evidence'][0]['path'] = 'uncaptured.py'
    with codec.encode_results(result, 'a'*64) as transfer:
        with pytest.raises(ValueError, match='evidence'):
            codec.decode_results(ReceivedFileTransfer(transfer.manifest, transfer.path), digest='a'*64, snapshot=state)


def test_compute_registry_metadata_does_not_load_analyzer_or_sdk_contracts():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        "import sys; import shared.workflow_graph_compute; "
        "assert 'shared.workflow_conversion_analysis' not in sys.modules; "
        "assert 'shared.workflow_conversion_sdk' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
