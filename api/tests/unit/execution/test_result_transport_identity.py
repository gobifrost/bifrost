"""A physical result cannot choose another canonical execution identity."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.services.execution.process_pool import ExecutionInfo, ProcessHandle, ProcessPoolManager, ProcessState
from src.worker.remote.remote_pool import ThinRemoteProcessPool
from tests.unit.dev.test_thin_remote_pool import _Transport, _active


@pytest.mark.parametrize('reported_id', ['another-execution', None, 'trusted-execution'])
def test_transport_metadata_binds_execution_identity_without_mutating_result(reported_id):
    info = ExecutionInfo('trusted-execution', datetime.now(timezone.utc), 30, _active('trusted-execution'))
    result = {'success': True, 'sync': False}
    if reported_id is not None:
        result['execution_id'] = reported_id
    original = dict(result)

    attached = info.attach_transport_metadata(result)

    assert attached['execution_id'] == 'trusted-execution'
    assert attached['sync'] is True
    assert result == original


@pytest.mark.asyncio
@pytest.mark.parametrize('reported_id', ['another-execution', None, 'trusted-execution'])
async def test_native_result_callback_uses_parent_execution_identity(reported_id):
    callback = AsyncMock()
    pool = ProcessPoolManager(on_result=callback)
    now = datetime.now(timezone.utc)
    handle = ProcessHandle(id='unit-handle', process=None, pid=None, state=ProcessState.BUSY,
                           work_queue=None, result_queue=None, started_at=now,
                           current_execution=ExecutionInfo('trusted-execution', now, 30, _active('trusted-execution')))
    pool.processes[handle.id] = handle
    result = {'success': True, 'sync': False}
    if reported_id is not None:
        result['execution_id'] = reported_id

    await pool._handle_result(handle, result)

    callback.assert_awaited_once()
    delivered = callback.await_args.args[0]
    assert delivered['execution_id'] == 'trusted-execution'
    assert delivered['sync'] is True
    assert pool.processes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('reported_id', ['another-execution', None, 'trusted-execution'])
async def test_remote_result_is_bound_to_the_parent_route_before_canonical_callback(reported_id):
    transport = _Transport()
    callback = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_result=callback)
    with (
        patch.object(pool, '_write_context_to_redis', new_callable=AsyncMock),
        patch.object(pool, '_write_active_execution_lease', new_callable=AsyncMock),
    ):
        await pool.route_execution('trusted-execution', {'timeout_seconds': 30}, _active('trusted-execution'))
    route = transport.requests[-1][1]['route_id']
    result = {'success': True, 'sync': False}
    if reported_id is not None:
        result['execution_id'] = reported_id

    await pool.handle_event({'event': 'result', 'route_id': route, 'result': result})

    callback.assert_awaited_once()
    delivered = callback.await_args.args[0]
    assert delivered['execution_id'] == 'trusted-execution'
    assert delivered['sync'] is True
    assert pool.processes == {}
