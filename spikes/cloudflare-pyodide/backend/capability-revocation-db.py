"""Real owner-lock serialization plus real Pyodide effect revocation, not business-job cancellation."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import delete, text

from attempt_journal import AttemptJournal
from capability_revocation import AttemptCapabilityRevoker
from http_invocation import HttpInvocation
from src.core.database import init_db, close_db, get_db_context
from src.models.orm.execution_workers import ExecutionWorker
from src.worker.remote import ownership
from src.worker.remote.protocol import CredentialRouteChanged


async def main():
    target = urlsplit(os.environ.get('BIFROST_DATABASE_URL', ''))
    if (os.environ.get('BIFROST_ENVIRONMENT') != 'testing' or target.hostname != 'pgbouncer'
        or target.path != '/bifrost_test' or os.environ.get('TEST_API_URL') != 'http://api:8000'):
        raise RuntimeError('Disposable owned test stack required')
    await init_db()
    worker, owner, launcher, execution = uuid4(), uuid4(), uuid4(), str(uuid4())
    release_close, entered, pid_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks, report, closes = [], {'capability_revocation_probe': True, 'canonical_execution_verified': False,
        'physical_termination_verified': False, 'cloud_invocations': 0}, 0
    pid = None
    try:
        async with get_db_context() as db:
            db.add(ExecutionWorker(id=worker, form='static', state='ready', resource_class='standard',
                                   slots=1, supported_handlers=['workflow.execute']))

        async def acquire(sequence):
            lease = await ownership.acquire_remote_owner(worker_id=worker, owner_id=owner, launcher_id=launcher,
                nonce_digest=f'{sequence:064x}', hello_sequence=sequence, credential_protocol=2)
            if lease is None:
                raise RuntimeError('Synthetic owner acquisition failed')
            return lease

        first = await acquire(1)
        accepted = asyncio.Event()
        invocation = HttpInvocation('http://bifrost-pyodide-ddf316c1:8799/',
            {'mode': 'canonical', 'execution_id': execution, 'context': {'execution_id': execution}},
            lambda _handle: accepted.set())
        journal = AttemptJournal()
        receipt = await journal.dispatch(first, execution, execution, invocation.collect)
        tasks.append(journal.routes[execution].task)
        await asyncio.wait_for(accepted.wait(), 15)
        handle = invocation.runtime_handle
        revoker = AttemptCapabilityRevoker(journal)
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            async def snapshot():
                return (await client.get('http://bifrost-pyodide-ddf316c1:8820/snapshot')).json()

            async with asyncio.timeout(15):
                while not (await snapshot())['received']:
                    await asyncio.sleep(0.05)

            async def close():
                nonlocal closes
                closes += 1
                entered.set()
                await release_close.wait()
                response = await client.post(f'http://bifrost-pyodide-ddf316c1:8799/revoke?id={execution}')
                response.raise_for_status()
                return response.json()

            revoker.bind(execution, handle, close)
            current = await acquire(2)
            try:
                await revoker.revoke(first, execution)
            except CredentialRouteChanged:
                report['stale_owner_refused_before_close'] = closes == 0
            else:
                raise RuntimeError('Stale owner reached explicit revocation')
            revoke = asyncio.create_task(revoker.revoke(current, execution))
            tasks.append(revoke)
            await asyncio.wait_for(entered.wait(), 5)
            original_context = ownership.get_liveness_db_context

            @asynccontextmanager
            async def observed_context():
                nonlocal pid
                async with original_context() as db:
                    pid = await db.scalar(text('SELECT pg_backend_pid()'))
                    pid_ready.set()
                    yield db

            with patch.object(ownership, 'get_liveness_db_context', observed_context):
                rotation = asyncio.create_task(acquire(3))
                tasks.append(rotation)
                await asyncio.wait_for(pid_ready.wait(), 5)
                async with asyncio.timeout(5):
                    while True:
                        async with get_db_context() as db:
                            wait = await db.scalar(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'), {'pid': pid})
                        if wait == 'Lock':
                            break
                        await asyncio.sleep(0.025)
                if rotation.done():
                    raise RuntimeError('Owner rotated while capability callback held the fence')
                report['owner_rotation_observed_waiting_on_postgres_lock'] = True
                release_close.set()
                report['revocation'] = await revoke
                replacement = await rotation

            replay = await revoker.revoke(replacement, execution)
            if replay != report['revocation'] or closes != 1:
                raise RuntimeError('Revocation replay resent a physical control')
            route = journal.routes[execution]
            if route.acknowledged or route.task.done():
                raise RuntimeError('Capability close prematurely acknowledged collection')
            try:
                await journal.retire()
            except RuntimeError:
                report['retirement_refused_while_unsettled'] = True
            else:
                raise RuntimeError('Revocation released unsettled route')
            report['vendor_before_release'] = await snapshot()
            if report['vendor_before_release']['committed']:
                raise RuntimeError('Held destination committed before parent release')
            release = await client.post('http://bifrost-pyodide-ddf316c1:8820/release')
            release.raise_for_status()
            evidence = await asyncio.wait_for(receipt, 10)
            if evidence['result']['result']['statuses'] != [200, 200, 410]:
                raise RuntimeError('Explicit revocation did not refuse new Python effect')
            report['vendor_after_release'] = await snapshot()
            if report['vendor_after_release']['effects'] != ['/before', '/hold']:
                raise RuntimeError('Explicitly revoked capability permitted follow-up effect')
            report['evidence'] = evidence

            async def accept(_route, _execution, _result):
                # Fixture transport retirement only; no canonical execution row.
                return True

            await journal.reconcile_terminal(replacement, execution, accept)
            await journal.retire()
            report.update(physical_close_calls=closes, completed_collection_retired=True,
                          revocation_did_not_settle_collection=True)
    finally:
        release_close.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with get_db_context() as db:
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker))
        await close_db()
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(main())
