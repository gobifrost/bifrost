"""Experimental opaque physical adapter; not public runtime admission."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import time
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from src.services.execution.process_pool import ProcessState
from src.worker.remote.remote_pool import ThinRemoteProcessPool
from src.worker.remote.host import RemoteControlHost
from src.worker.remote.protocol import Hello, ProtocolError, sign_hello
from tests.unit.dev.test_thin_remote_pool import _Transport, _active

PROFILE = "cloudflare-python-v1"


async def dispatch(ack):
    transport = _Transport()
    transport.request = AsyncMock(return_value=ack)
    callback = AsyncMock()
    pool = ThinRemoteProcessPool(transport, runtime_profile=PROFILE, on_result=callback)
    with (patch.object(pool, '_write_context_to_redis', new_callable=AsyncMock),
          patch.object(pool, '_write_active_execution_lease', new_callable=AsyncMock)):
        await pool.route_execution('opaque-run', {'timeout_seconds': 30}, _active('opaque-run'))
    route = transport.request.await_args.args[1]['route_id']
    return pool, transport, callback, route


@pytest.mark.asyncio
async def test_opaque_ack_is_counted_without_inventing_a_pid_or_measurements():
    pool, _, callback, route = await dispatch({'runtime_handle': 'invocation:opaque-1'})
    handle = pool.processes[route]
    assert handle.pid is None and handle.process.remote_pid is None
    assert handle.process.runtime_handle == 'invocation:opaque-1'
    heartbeat = pool._build_heartbeat()
    assert heartbeat['processes'] == []
    assert heartbeat['busy_count'] == heartbeat['pool_size'] == 1
    assert heartbeat['runtime_invocations'] == [{
        'runtime_profile': PROFILE, 'runtime_handle': 'invocation:opaque-1',
        'route_id': route, 'execution_id': 'opaque-run', 'state': 'busy',
        'memory_bytes': None, 'cpu_seconds': None}]
    event = {'event': 'result', 'route_id': route, 'result': {'success': True, 'execution_id': 'other-run'}}
    await pool.handle_event(event)
    await pool.handle_event(event)
    callback.assert_awaited_once()
    assert callback.await_args.args[0]['execution_id'] == 'opaque-run'
    assert pool._build_heartbeat()['busy_count'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('ack', [None, [], {}, {'pid': 123}, {'runtime_handle': True},
    {'runtime_handle': 'x' * 256}, {'runtime_handle': 'https://not-a-handle.invalid/'},
    {'runtime_handle': 'opaque', 'pid': None}])
async def test_invalid_opaque_ack_retains_uncertain_invocation(ack):
    pool, transport, callback, route = await dispatch(ack)
    transport.request.assert_awaited_once()
    assert pool.processes[route].process.runtime_handle is None
    assert pool._build_heartbeat()['busy_count'] == 1
    await pool.handle_event({'event': 'result', 'route_id': route,
                             'result': {'success': False, 'execution_id': 'opaque-run', 'error': 'fixed failure'}})
    callback.assert_awaited_once()
    assert not pool.processes


@pytest.mark.asyncio
async def test_unqualified_cancellation_does_not_claim_stop_or_release_route():
    pool, transport, _, route = await dispatch({'runtime_handle': 'opaque'})
    with pytest.raises(RuntimeError, match='cancellation is not qualified'):
        await pool._handle_cancel_request('opaque-run')
    transport.request.assert_awaited_once()  # Only dispatch, no invented terminate command.
    assert pool.processes[route].state == ProcessState.BUSY
    assert pool.processes[route].killed_at is None
    assert pool._build_heartbeat()['busy_count'] == 1


@pytest.mark.asyncio
async def test_persistent_service_rejected_before_any_effect():
    transport = _Transport()
    transport.request = AsyncMock()
    pool = ThinRemoteProcessPool(transport, runtime_profile=PROFILE)
    with pytest.raises(RuntimeError, match='persistent services'):
        await pool.route_service(service_id='service', attempt_id='attempt', lease_token='lease', context={})
    transport.request.assert_not_awaited()
    assert pool.service_processes == {}


def test_unknown_profile_rejected_instead_of_falling_back_to_linux():
    with pytest.raises(ValueError, match='Unsupported physical runtime profile'):
        ThinRemoteProcessPool(_Transport(), runtime_profile='unknown-runtime')


def test_opaque_inventory_adoption_binds_handle_and_refuses_replacement():
    pool = ThinRemoteProcessPool(_Transport(), runtime_profile=PROFILE)
    handle = pool._adopt_handle('route', {'runtime_handle': 'opaque-original'}, datetime.now(timezone.utc))
    assert handle.process.runtime_handle == 'opaque-original'
    assert handle.process.remote_pid is None
    with pytest.raises(ConnectionError, match='handle changed'):
        pool._bind_workflow_handle(handle, {'runtime_handle': 'opaque-replacement'})
    assert handle.process.runtime_handle == 'opaque-original'


@pytest.mark.asyncio
async def test_experimental_pool_profile_is_still_rejected_by_public_control_host():
    declared = Hello(launcher_id=uuid4(), incarnation_id=uuid4(), job_uid=None, pod_uid=None,
                     hostname='opaque-test', form='static', issued_at=int(time.time()),
                     nonce='n' * 32, hello_sequence=1, runtime_profile=PROFILE)
    frame = {'event': 'hello', 'hello': sign_hello('test-secret', declared).to_dict(),
             'sample': {'current': 1, 'limit': 100}, 'requirements': {}}
    socket = Mock(accept=AsyncMock(), receive_text=AsyncMock(return_value=json.dumps(frame)), close=AsyncMock())
    with (patch('src.worker.remote.host.get_settings', return_value=Mock(secret_key='test-secret')),
          patch('src.worker.remote.host.acquire_remote_owner', new_callable=AsyncMock) as acquire,
          patch('src.worker.remote.host.get_liveness_db_context') as db):
        with pytest.raises(ProtocolError, match='unsupported physical runtime'):
            await RemoteControlHost().connect(socket)
    acquire.assert_not_awaited()
    db.assert_not_called()
    socket.close.assert_awaited_once_with(code=1008)


@pytest.mark.asyncio
async def test_opaque_reconnect_keeps_original_expired_timing_and_canonical_claim(async_session_factory, monkeypatch):
    from sqlalchemy import delete
    from src.models.enums import ExecutionStatus
    from src.models.orm import Execution
    from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim

    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr('src.core.database.get_db_context', context)
    worker, run = uuid4(), uuid4()
    async with async_session_factory() as session:
        session.add(ExecutionWorker(id=worker, form='static', state='ready', resource_class='standard', slots=0))
        session.add(Execution(id=run, workflow_name='opaque', executed_by_name='test', status=ExecutionStatus.RUNNING))
        await session.flush()
        session.add(ExecutionWorkerClaim(worker_id=worker, family='workflow_execution', subject_id=run))
        await session.commit()
    pool = ThinRemoteProcessPool(_Transport(), worker_id=str(worker), runtime_profile=PROFILE)
    pool._write_active_execution_lease = AsyncMock()
    started = datetime.now(timezone.utc) - timedelta(seconds=35)
    entry = {'route_id': 'opaque-route', 'kind': 'workflow', 'subject_id': str(run),
             'runtime_handle': 'opaque-original', 'memory_request_bytes': 128 * 2**20,
             'started_at': started.isoformat(), 'timeout_seconds': 30, 'active_execution': _active(str(run))}
    try:
        first = await pool.reconcile_inventory({'version': 1, 'routes': [entry]})
        assert first['workflow_requests'] == {str(run): 128 * 2**20}
        same = await pool.reconcile_inventory({'version': 1, 'terminal_routes': [entry]})
        assert same['workflow_requests'] == first['workflow_requests']
        changed = {**entry, 'started_at': datetime.now(timezone.utc).isoformat()}
        with pytest.raises(ValueError, match='immutable workflow timing'):
            await pool.reconcile_inventory({'version': 1, 'routes': [changed]})
        changed = {**entry, 'runtime_handle': 'replacement'}
        with pytest.raises(ConnectionError, match='handle changed'):
            await pool.reconcile_inventory({'version': 1, 'routes': [changed]})
        info = pool.processes['opaque-route'].current_execution
        assert info.started_at == started and info.timeout_seconds == 30
        assert pool._build_heartbeat()['busy_count'] == 1
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker))
            await session.execute(delete(Execution).where(Execution.id == run))
            await session.commit()
