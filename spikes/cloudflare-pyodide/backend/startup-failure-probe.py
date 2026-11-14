"""Characterize actual upstream startup with mocked transport/cache; no dispatch."""
import asyncio
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.worker.remote.remote_pool import ThinRemoteProcessPool


async def observe(case):
    transport = SimpleNamespace(connected=True, request=AsyncMock())
    pool = ThinRemoteProcessPool(transport, worker_id='synthetic-startup-probe')
    redis = SimpleNamespace(hset=AsyncMock(return_value=1))
    failure = RuntimeError('synthetic startup failure')
    register = AsyncMock(side_effect=failure if case == 'registration' else None)
    if case == 'configure':
        transport.request.side_effect = failure
    if case == 'tagging':
        redis.hset.side_effect = failure
    hold = asyncio.Event()

    async def idle():
        await hold.wait()

    with patch.object(pool, '_register_worker', register), patch.object(pool, '_get_redis', AsyncMock(return_value=redis)), \
         patch.object(pool, '_read_remote_memory', Mock()), \
         patch.object(pool, '_heartbeat_loop', idle), patch.object(pool, '_lease_refresh_loop', idle), \
         patch.object(pool, '_cancel_listener_loop', idle), patch.object(pool, '_command_listener_loop', idle):
        errors = []
        try:
            for _ in range(2):
                try:
                    await pool.start()
                    errors.append(None)
                except RuntimeError as error:
                    if error is not failure:
                        raise
                    errors.append(type(error).__name__)
            tasks = [pool._heartbeat_task, pool._lease_refresh_task, pool._cancel_task, pool._command_task]
            result = {'case': case, 'errors': errors, 'started': pool._started,
                      'configure_calls': transport.request.await_count,
                      'registration_calls': register.await_count,
                      'lifecycle_tasks_created': sum(task is not None for task in tasks)}
            if case == 'configure':
                assert errors == ['RuntimeError', 'RuntimeError'] and not pool._started
                assert result['configure_calls'] == 2 and result['lifecycle_tasks_created'] == 0
            elif case in ('registration', 'tagging'):
                assert errors == ['RuntimeError', None] and pool._started
                assert result['configure_calls'] == 1 and result['lifecycle_tasks_created'] == 0
            else:
                assert errors == [None, None] and pool._started
                assert result['configure_calls'] == 1 and result['lifecycle_tasks_created'] == 4
            return result
        finally:
            tasks = [pool._heartbeat_task, pool._lease_refresh_task, pool._cancel_task, pool._command_task]
            for task in tasks:
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in tasks if task is not None), return_exceptions=True)


async def main():
    cases = [await observe(case) for case in ('configure', 'registration', 'tagging', 'healthy')]
    source = Path(inspect.getfile(ThinRemoteProcessPool)).read_bytes()
    print(json.dumps({'scope': 'actual upstream pool with mocked transport, registration and cache; no live startup',
                      'remote_pool_sha256': hashlib.sha256(source).hexdigest(), 'cases': cases,
                      'cloud_invocations': 0, 'execution_admission_qualified': False}))


if __name__ == '__main__':
    asyncio.run(main())
