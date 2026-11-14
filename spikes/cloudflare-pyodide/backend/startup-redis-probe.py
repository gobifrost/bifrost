"""Actual candidate/cache operations; synthetic transport/session, idle loops."""
import asyncio
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, cast
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from src.worker.remote.remote_pool import ThinRemoteProcessPool


async def observe(mode, observer):
    worker = f'synthetic-startup-{uuid4()}'
    key = f'bifrost:pool:{worker}'

    async def command(action, payload):
        if action == 'configure':
            return {}
        if action == 'package_tool' and payload == {'operation': 'inventory'}:
            return {'packages': [{'name': 'synthetic-package', 'version': '1.0.0'}]}
        raise AssertionError('No dispatch or other control allowed')

    transport = SimpleNamespace(connected=True, hostname='synthetic-redis-probe', request=AsyncMock(side_effect=command))
    pool = ThinRemoteProcessPool(transport, worker_id=worker)
    client = Redis(host='127.0.0.1', port=6379, decode_responses=True)
    entered, hold = asyncio.Event(), asyncio.Event()
    snapshots = []
    original_hset = client.hset

    async def get_client():
        pool._redis = client
        return client

    async def hset(*args, **kwargs):
        if 'runtime' in kwargs.get('mapping', {}) and mode != 'healthy':
            snapshots.append(await observer.hgetall(key))
            if mode == 'superseded':
                await observer.hset(key, 'owner_marker', 'replacement')
            if mode == 'cancel':
                entered.set()
                await hold.wait()
            raise RuntimeError('synthetic final tagging failure')
        return await cast(Awaitable[int], original_hset(*args, **kwargs))

    async def idle():
        await hold.wait()

    patches = [patch.object(pool, '_get_redis', side_effect=get_client),
               patch.object(pool, '_read_remote_memory', Mock()),
               patch.object(pool, '_owns_session', AsyncMock(return_value=mode != 'superseded')),
               patch('src.core.pubsub.publish_worker_event', AsyncMock()), patch.object(client, 'hset', hset)]
    patches += [patch.object(pool, name, idle) for name in
                ('_heartbeat_loop', '_lease_refresh_loop', '_cancel_listener_loop', '_command_listener_loop')]
    for context in patches:
        context.start()
    try:
        fault = None
        if mode == 'delete_failure':
            fault = patch.object(client, 'delete', AsyncMock(side_effect=RedisConnectionError('synthetic delete failure')))
        if mode == 'readback_failure':
            fault = patch.object(client, 'exists', AsyncMock(side_effect=RedisConnectionError('synthetic readback failure')))
        if fault:
            fault.start()
        try:
            error = None
            if mode == 'cancel':
                task = asyncio.create_task(pool.start())
                await entered.wait()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError as caught:
                    error = caught
            else:
                try:
                    await pool.start()
                except (RuntimeError, BaseExceptionGroup) as caught:
                    error = caught
            row = await observer.hgetall(key)
            if mode == 'healthy':
                assert error is None and row['status'] == 'online'
                assert row['runtime'] == 'thin_remote'
                await pool.start()
                assert transport.request.await_count == 2  # configure and inventory, only once
                await pool.stop()
            else:
                assert error is not None
                assert snapshots and snapshots[0]['status'] == 'online'
                if mode in ('delete_failure', 'readback_failure'):
                    assert isinstance(error, BaseExceptionGroup) and pool._started and pool._shutdown
                    refused = False
                    try:
                        await pool.start()
                    except RuntimeError:
                        refused = True
                    assert refused
                else:
                    assert not pool._started and pool._redis is None
                if mode == 'delete_failure':
                    assert row['status'] == 'online'
                elif mode == 'superseded':
                    assert row['owner_marker'] == 'replacement'
                else:
                    assert not row
        finally:
            if fault:
                fault.stop()
        if mode in ('delete_failure', 'readback_failure'):
            await pool.stop()
        after = await observer.hgetall(key)
        if mode == 'superseded':
            assert after['owner_marker'] == 'replacement'
            # Explicit fixture cleanup; not old-owner platform compensation.
            await observer.delete(key)
        else:
            assert not after and not pool._started and pool._redis is None
        return {'mode': mode, 'partial_registration_observed': bool(snapshots),
                'row_after_first_outcome': row, 'row_after_cleanup_retry': after,
                'final_fixture_key_exists': await observer.exists(key),
                'error_type': None if error is None else type(error).__name__}
    finally:
        for task in (pool._heartbeat_task, pool._lease_refresh_task, pool._cancel_task, pool._command_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (pool._heartbeat_task, pool._lease_refresh_task,
            pool._cancel_task, pool._command_task) if task is not None), return_exceptions=True)
        await client.aclose()
        for context in reversed(patches):
            context.stop()
        await observer.delete(key)


async def main():
    observer = Redis(host='127.0.0.1', port=6379, decode_responses=True)
    try:
        await cast(Awaitable[bool], observer.ping())
        records = [await observe(mode, observer) for mode in
                   ('healthy', 'tagging', 'delete_failure', 'readback_failure', 'cancel', 'superseded')]
        remaining = await observer.keys('bifrost:pool:synthetic-startup-*')
        assert not remaining
        report = {'scope': 'actual candidate registration/unregistration and independent real Redis reads; synthetic transport/session and idle loops',
                  'candidate_sha256': hashlib.sha256(Path(inspect.getfile(ThinRemoteProcessPool)).read_bytes()).hexdigest(),
                  'records': records, 'keys_remaining': remaining, 'cloud_invocations': 0,
                  'signed_startup_verified': False, 'execution_admission_qualified': False}
        Path('/evidence/redis-result.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Six real Redis startup cases passed; no signed/public startup claim')
    finally:
        await observer.aclose()


if __name__ == '__main__':
    asyncio.run(main())
