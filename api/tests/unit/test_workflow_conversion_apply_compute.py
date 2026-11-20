"""Apply preparation and final API mutation are distinct, fenced phases."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from shared import workflow_conversions as conversion
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from tests.unit.test_workflow_conversion_service import _workflow, _context, _admin
from tests.unit.test_workflow_graph_compute import source


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['root', 'dependency', 'missing_appeared', 'unchanged'])
async def test_final_source_verification_rechecks_both_bytes_and_resolution_answers(monkeypatch, change):
    root = source('run.py', 'def run(): pass')
    helper = source('helper.py', 'def work(): return 1')
    prepared = SimpleNamespace(allow_outbound_access=False, sources={root.key: root, helper.key: helper},
        resolutions=((root.key, 'helper', 0, helper.key), (root.key, 'missing', 0, None)))
    monkeypatch.setattr(conversion, '_source_for_workflow', AsyncMock(return_value=
        source('run.py', 'def changed(): pass') if change == 'root' else root))
    async def resolve(sf, module, level, *, workflow, allow_outbound_access):
        assert sf.key == root.key and level == 0 and not allow_outbound_access
        if module == 'helper':
            return source('helper.py', 'def changed(): pass') if change == 'dependency' else helper
        return helper if change == 'missing_appeared' else None
    monkeypatch.setattr(conversion, '_resolve_import', resolve)
    assert await conversion._verify_prepared_source_graph(object(), root, prepared) is (change == 'unchanged')


@pytest.mark.asyncio
@pytest.mark.parametrize('refusal', ['owner', 'admin', 'metadata', 'source', 'missing_source', 'fingerprint', 'unknowns'])
async def test_apply_computes_without_db_scope_and_refuses_before_grant_publication(monkeypatch, refusal):
    workflow = _workflow()
    fingerprint = 'a'*64
    report = SimpleNamespace(status='ready', source_fingerprint=fingerprint, review=None, report={})
    db = SimpleNamespace(execute=AsyncMock(), add=Mock(), commit=AsyncMock(), flush=AsyncMock())
    active = 0
    @asynccontextmanager
    async def database():
        nonlocal active
        active += 1
        try:
            yield db
        finally:
            active -= 1
    monkeypatch.setattr(conversion, 'get_db_context', database)
    monkeypatch.setattr(conversion, 'read_runtime_modules', AsyncMock())
    monkeypatch.setattr(conversion, '_apply_selection_rows', AsyncMock(return_value=(workflow, report)))
    monkeypatch.setattr(conversion, '_require_current_conversion_admin', AsyncMock(side_effect=[
        _admin(), PlatformJobFailure('authorization_changed', 'Revoked') if refusal == 'admin' else _admin()]))
    monkeypatch.setattr(conversion, '_fenced_job', AsyncMock(side_effect=PlatformJobCancelled if refusal == 'owner' else None))
    monkeypatch.setattr(conversion, '_source_for_workflow', AsyncMock(side_effect=FileNotFoundError if refusal == 'missing_source' else None, return_value=object()))
    async def prepare(context, captured, source, modules, *, operation):
        assert active == 0 and operation == 'workflows.conversions.update'
        if refusal == 'metadata':
            captured.function_name = 'changed'
        return SimpleNamespace(allow_outbound_access=True, analysis={'dependencies': {}}, summaries={}, sources={})
    monkeypatch.setattr(conversion, '_prepare_source_graph', prepare)
    monkeypatch.setattr(conversion, '_verify_prepared_source_graph', AsyncMock(return_value=refusal != 'source'))
    monkeypatch.setattr(conversion, '_report_for_workflow', AsyncMock(return_value={
        'source_fingerprint': 'b'*64 if refusal == 'fingerprint' else fingerprint,
        'unknowns': ['dynamic code'] if refusal == 'unknowns' else [], 'proposed_permissions': ['roles.read']}))
    stale = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(conversion, '_mark_stale', stale)
    monkeypatch.setattr(conversion, 'emit_audit', audit)
    context = _context()
    selection = SimpleNamespace(workflow_id=workflow.id, source_fingerprint=fingerprint, acknowledged_unknowns=False)
    payload = SimpleNamespace(selections=[selection], analysis_job_id=uuid4(), acknowledged_solution_requests=False)
    with pytest.raises(PlatformJobCancelled if refusal == 'owner' else PlatformJobFailure) as failure:
        await conversion.run_apply(context, payload)
    if refusal != 'owner':
        assert failure.value.result == {'completed': []}
    assert bool(stale.await_count) == (refusal in {'metadata', 'source', 'missing_source', 'fingerprint'})
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    audit.assert_not_awaited()
    context.checkpoint_in_transaction.assert_not_awaited()
    assert active == 0


@pytest.mark.asyncio
async def test_apply_requires_compute_before_source_or_inventory(monkeypatch):
    inventory = AsyncMock()
    monkeypatch.setattr(conversion, 'read_runtime_modules', inventory)
    with pytest.raises(PlatformJobFailure, match='source graph compute'):
        await conversion.run_apply(SimpleNamespace(file_compute=None), object())
    inventory.assert_not_awaited()
