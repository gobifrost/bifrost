"""Two fixed tenant workflows, one actual native pool; disposable guest only."""
import asyncio
import hashlib
import json
import logging
import os
import re
import runpy
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch
from typing import cast

import httpx
from sqlalchemy import delete, select

from private_http_pool import PrivateHttpWorkflowPool
from task_cleanup import cancel_and_join
from workspace_prepare import prepare_workspace, WorkspacePreparationError
from retained_http_fault import RetainedHttpFault
import http_invocation as invocation_module


async def main(*, source_change=False, late_result=False, retained_http=False, deadline_ack_loss=False, startup_inventory=False, oversized_result=False, numeric_result=False, oversized_body=False, memory_pressure=False, refresh_redaction=False):
    if sum((source_change, late_result, retained_http, oversized_result, numeric_result, oversized_body, memory_pressure, refresh_redaction)) > 1:
        raise ValueError('One fixture mode required')
    if deadline_ack_loss and not late_result:
        raise ValueError('Deadline ACK loss requires finite late-result mode')
    database = urlsplit(os.environ.get('BIFROST_DATABASE_URL', ''))
    if (os.environ.get('BIFROST_ENVIRONMENT') != 'testing'
        or os.environ.get('TEST_API_URL') != 'http://api:8000'
        or database.hostname != 'pgbouncer' or database.path != '/bifrost_test'):
        raise RuntimeError('Disposable assigned Docker test stack required')

    from shared.version import get_version
    from src.core.database import init_db, close_db, get_engine, get_db_context
    from src.core.module_cache import set_module, invalidate_module
    from src.core.redis_client import PendingExecution, pending_execution_record
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
    from src.models.enums import ConfigType, ExecutionStatus
    from src.models.orm import Execution, ExecutionLog
    from src.models.orm.config import Config
    from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
    from src.models.orm.work_dispatches import WorkDispatch
    from src.models.orm.tables import Document, Table
    from src.services.execution.memory_monitor import effective_memory_sample
    from src.services.execution.workflow_dispatch import stage_workflow_run
    from src.services.repo_storage import RepoStorage
    from src.services.worker_pool.claims import claim_next, register_static_worker, mark_static_ready, heartbeat_worker
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import acquire_remote_owner, renew_remote_owner, release_remote_owner
    from src.worker.remote.protocol import CredentialRouteChanged
    from tests.unit.authorization.committed_runs import committed_world

    helpers = runpy.run_path('/spike/backend/authorized-local.py')
    await init_db()
    storage, sources, slots, secrets = RepoStorage(), {}, {}, []
    heartbeat, ref = None, None
    fault, patcher = None, None
    runtime_cache = None

    def retained_fault():
        if fault is None:
            raise RuntimeError('Retained HTTP fixture not initialized')
        return fault
    report = {'shared_native_pool_probe': True, 'cloud_invocations': 0,
              'physical_termination_verified': False, 'launches': {}, 'attempts': {}}
    suffix = uuid4().hex
    try:
        async with committed_world(get_engine()) as world, helpers['cleanup_fixture_audit'](world, get_engine()):
            for label, n in [('fails-first', 7), ('succeeds-second', 8)]:
                org = await world.organization()
                user = await world.person(org)
                role = await world.role('configs.read', 'configs.readwrite', 'tables.read', 'tables.readwrite')
                await world.assign(user, role.id, ('organization', org.id))
                key, gate = 'shared-value-' + suffix + label, 'shared-gate-' + suffix + label
                table = 'pyodide-shared-' + suffix + label
                for name, value, kind in [(key, 42, ConfigType.INT), (gate, False, ConfigType.BOOL)]:
                    await world._add(Config(key=name, value={'value': value}, config_type=kind,
                                           organization_id=org.id, updated_by=str(user.id)))
                module_id = uuid4().hex
                path, helper = 'workflows/pyodide_qualification_' + module_id + '.py', 'pyodide_helper_' + module_id
                sources[helper + '.py'] = 'def fold(n):\n    return sum(range(1, n + 1))\n'
                workflow = await world.workflow(org)
                workflow.path, workflow.function_name, workflow.timeout_seconds = path, 'qualified', 30
                execution = await world.execution(run_user=user, workflow=workflow, organization=org, credential_version=2)
                execution.status = ExecutionStatus.PENDING
                slots[str(execution.id)] = {'label': label, 'n': n, 'org': org, 'user': user,
                    'workflow': workflow, 'execution': execution, 'key': key, 'gate': gate,
                    'table': table, 'path': path, 'helper': helper, 'value': 42 + sum(range(1, n + 1)),
                    'evidence': None, 'invocation': None, 'token': None, 'refresh': None}

            for execution_id, slot in slots.items():
                peer = next(value for key, value in slots.items() if key != execution_id)
                source = f'''from bifrost import workflow, config, tables
from {slot['helper']} import fold
import asyncio, builtins, httpx, json, logging, os
from pathlib import Path
@workflow
async def qualified(n: int, peer_scope: str, peer_key: str):
    marker = Path('/tmp/bifrost-shared-canary')
    fresh = {{'builtins': getattr(builtins, '_shared_canary', None),
             'module': getattr(json, '_shared_canary', None),
             'environment': os.environ.get('BIFROST_SHARED_CANARY'),
             'filesystem': marker.read_text() if marker.exists() else None}}
    if any(value is not None for value in fresh.values()):
        raise RuntimeError('Shared fixture contamination before effects')
    builtins._shared_canary = {slot['label']!r}
    json._shared_canary = {slot['label']!r}
    os.environ['BIFROST_SHARED_CANARY'] = {slot['label']!r}
    marker.write_text({slot['label']!r})
    try:
        await config.get(peer_key, scope=peer_scope)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        peer_status = exc.response.status_code
    else:
        raise RuntimeError('Peer tenant was accessible')
    value = await config.get({slot['key']!r})
    doc = await tables.upsert({slot['table']!r}, id='proof', data={{'value': value + fold(n)}})
    logging.getLogger(__name__).warning('shared label={slot['label']} credential=%s', os.environ['BIFROST_ACCESS_TOKEN'])
    while await config.get({slot['gate']!r}) is not True:
        await asyncio.sleep(0.25)
    if {slot['label'] == 'fails-first'!r}:
        raise ValueError('shared fixture failed credential=' + os.environ['BIFROST_ACCESS_TOKEN'])
    return {{'value': doc.data['value'], 'label': {slot['label']!r}, 'fresh': fresh,
             'peer_status': peer_status, 'parent_secret_present': 'BIFROST_SECRET_KEY' in os.environ,
             'credential': os.environ['BIFROST_ACCESS_TOKEN']}}
'''
                if numeric_result:
                    source = source.replace(
                        'async def qualified(n: int, peer_scope: str, peer_key: str):',
                        'async def qualified(n: int, peer_scope: str, peer_key: str, numeric_value: dict):\n'
                        "    if numeric_value != {'integer': 9007199254740993, 'negative': -9007199254740993, 'fraction': 0.1}:\n"
                        "        raise RuntimeError('Input numeric transport fidelity lost before effects')")
                    source = source.replace("'value': doc.data['value'],",
                        "'exact_integer': numeric_value['integer'], 'value': doc.data['value'],")
                sources[slot['path']] = source
                if (oversized_result or oversized_body) and slot['label'] == 'fails-first':
                    sources[slot['path']] = source.replace(
                        "        raise ValueError('shared fixture failed credential=' + os.environ['BIFROST_ACCESS_TOKEN'])",
                        f"        return {{'payload': 'x' * {1200000 if oversized_body else 300000}}}")
                if refresh_redaction and slot['label'] == 'fails-first':
                    sources[slot['path']] = source.replace(
                        "        raise ValueError('shared fixture failed credential=' + os.environ['BIFROST_ACCESS_TOKEN'])",
                        "        from bifrost.client import get_client\n"
                        "        original = os.environ['BIFROST_ACCESS_TOKEN']\n"
                        "        run_refresh = os.environ['BIFROST_REFRESH_TOKEN']\n"
                        "        replacement = await get_client().refresh_access_token(original)\n"
                        "        if not replacement or replacement == original:\n"
                        "            raise RuntimeError('Run access credential did not change')\n"
                        "        if os.environ['BIFROST_REFRESH_TOKEN'] != run_refresh:\n"
                        "            raise RuntimeError('Run refresh identity unexpectedly rotated')\n"
                        "        message = 'refresh_probe original=' + original + ' replacement=' + replacement + ' refresh=' + run_refresh\n"
                        "        print(message)\n"
                        "        logging.getLogger(__name__).warning(message)\n"
                        "        raise ValueError(message)")
                if memory_pressure and slot['label'] == 'fails-first':
                    sources[slot['path']] = source.replace(
                        "        raise ValueError('shared fixture failed credential=' + os.environ['BIFROST_ACCESS_TOKEN'])",
                        "        try:\n"
                        "            allocation = bytearray(160 * 1024 * 1024)\n"
                        "            allocation[0] = 1\n"
                        "            allocation[-1] = 1\n"
                        "            memory_outcome = 'allocated_160mib'\n"
                        "            del allocation\n"
                        "        except MemoryError:\n"
                        "            memory_outcome = 'python_memory_error'\n"
                        "        logging.getLogger(__name__).warning('bounded_memory_probe=%s', memory_outcome)\n"
                        "        raise ValueError('bounded_memory_probe=' + memory_outcome)")
                if late_result and slot['label'] == 'fails-first':
                    sources[slot['path']] = source.replace(
                        f"    while await config.get({slot['gate']!r}) is not True:\n        await asyncio.sleep(0.25)\n",
                        "    await asyncio.sleep(40)\n"
                        "    try:\n"
                        f"        await tables.upsert({slot['table']!r}, id='late-denied', data={{'value': 999}})\n"
                        "    except Exception as exc:\n"
                        "        late_write_error = type(exc).__name__\n"
                        "    else:\n"
                        "        raise RuntimeError('Post-deadline write unexpectedly accepted')\n"
                    ).replace("    if True:\n        raise ValueError('shared fixture failed credential=' + os.environ['BIFROST_ACCESS_TOKEN'])\n", "")
                pending = pending_execution_record(execution_id=execution_id, workflow_id=str(slot['workflow'].id),
                    parameters={'n': slot['n'], 'peer_scope': str(peer['org'].id), 'peer_key': peer['key'],
                        **({'numeric_value': {'integer': 9007199254740993, 'negative': -9007199254740993,
                                             'fraction': 0.1}} if numeric_result else {})},
                    org_id=str(slot['org'].id), user_id=str(slot['user'].id), user_name=slot['user'].name,
                    user_email=slot['user'].email, sync=False, lineage=None)
                await stage_workflow_run(world.session, pending,
                    {'execution_id': execution_id, 'workflow_id': str(slot['workflow'].id), 'sync': False}, record_exists=True)
            await world.session.commit()
            manifest = {path: hashlib.sha256(source.encode()).hexdigest() for path, source in sources.items()}
            for path, source in sources.items():
                digest = await storage.write(path, source.encode())
                if digest != manifest[path]:
                    raise RuntimeError('Fixture workspace provenance mismatch')
                await set_module(path, source, manifest[path])
            report['workspace_sha256'] = manifest
            worker_id = await register_static_worker(hostname='pyodide-shared-' + suffix,
                supported_handlers=['workflow.execute'], version=get_version())
            world._created.append((ExecutionWorker, worker_id))
            await world.session.commit()
            owner_id, launcher_id = uuid4(), uuid4()

            async def connect(sequence):
                lease = await acquire_remote_owner(worker_id=worker_id, owner_id=owner_id, launcher_id=launcher_id,
                    nonce_digest=f'{sequence:064x}', hello_sequence=sequence, credential_protocol=2)
                if lease is None:
                    raise RuntimeError('Fixture owner acquisition failed')
                return lease

            ref = LeaseRef(await connect(1))
            report['worker_id'] = str(worker_id)
            liveness = {'active_runs': 0, 'runs_handled': 0, 'memory_reserved_bytes': 0}
            requests = {}

            async def beat():
                with remote_owner_context(ref):
                    state = await heartbeat_worker(worker_id, **liveness, package_generation=0,
                        memory_pressured=False, memory_budget_bytes=2**30)
                if state not in ('ready', 'draining'):
                    raise ConnectionError('Shared worker heartbeat rejected')

            async def renew():
                while True:
                    await asyncio.sleep(5)
                    old = ref.lease
                    renewed = await renew_remote_owner(old)
                    if old != ref.lease:
                        continue
                    if not renewed:
                        raise ConnectionError('Shared worker owner renewal failed')
                    await beat()

            with remote_owner_context(ref):
                ready = await mark_static_ready(worker_id, memory_budget_bytes=2**30)
                if not ready:
                    raise RuntimeError('Shared worker ready transition failed')
            await beat()
            heartbeat = asyncio.create_task(renew())
            failure_consume_gate = asyncio.Event()
            lose_failure_ack = not late_result or deadline_ack_loss

            async def canonical_result(result):
                nonlocal lose_failure_ack
                failed_result = slots[result['execution_id']]['label'] == 'fails-first'
                if failed_result:
                    await failure_consume_gate.wait()
                await consumer._handle_result(result)
                if failed_result and lose_failure_ack:
                    lose_failure_ack = False
                    raise ConnectionError('Injected shared failure ACK loss')

            adapter = PrivateHttpWorkflowPool(endpoint='http://bifrost-pyodide-ddf316c1:8799', worker_id=worker_id,
                lease_ref=ref, memory_sample=effective_memory_sample, memory_request_bytes=requests.get,
                on_result=canonical_result, capacity=2)
            native, mux = adapter.native, adapter.mux
            class Parent:
                def credential_route(self):
                    return adapter.credential_route()

                async def route_execution(self, *, execution_id, context, credential_route, active_execution):
                    slot = slots[execution_id]
                    if context['file_path'] != slot['path'] or context['function_name'] != 'qualified':
                        raise RuntimeError('Fixture workflow identity mismatch')
                    slot['token'], slot['refresh'] = context['engine_token'], context.get('engine_refresh_token')
                    secrets.extend([slot['token'], slot['refresh']])
                    if slot['label'] == 'fails-first':
                        try:
                            await prepare_workspace('http://api:8000',
                                {**context, 'engine_token': 'invalid-preparation-qualification-token'}, [slot['helper']])
                        except WorkspacePreparationError as exc:
                            if exc.status_code != 401:
                                raise
                            report['invalid_preparation_credential_status'] = exc.status_code
                        else:
                            raise RuntimeError('Invalid preparation credential was accepted')
                    original_context = json.loads(json.dumps(context, default=str))
                    prepared = await prepare_workspace('http://api:8000', context, [slot['helper']])
                    expected_manifest = {path: manifest[path] for path in (slot['path'], slot['helper'] + '.py')}
                    if (prepared.manifest != expected_manifest
                        or json.dumps(context, default=str, sort_keys=True) != json.dumps(original_context, sort_keys=True)):
                        raise RuntimeError('API-prepared provenance differs from independently published source')
                    adapter.bind_manifest(execution_id, prepared.manifest)
                    slot['preparation'] = {'observations': prepared.observations,
                        'response_bytes': prepared.response_bytes, 'manifest': prepared.manifest,
                        'original_content_hash': original_context.get('content_hash')}
                    slot['original_context'] = prepared.context
                    slot['original_active'] = json.loads(json.dumps(active_execution, default=str))
                    if source_change and slot['label'] == 'fails-first':
                        dependency = slot['helper'] + '.py'
                        changed = 'def fold(n):\n    return 999\n'
                        changed_hash = hashlib.sha256(changed.encode()).hexdigest()
                        written = await storage.write(dependency, changed.encode())
                        if written != changed_hash:
                            raise RuntimeError('Synthetic changed source write did not match its digest')
                        await set_module(dependency, changed, changed_hash)
                        async with httpx.AsyncClient(base_url='http://api:8000', timeout=10) as reader:
                            readback = await reader.get('/api/sdk/modules/' + dependency,
                                                       headers={'Authorization': 'Bearer ' + slot['token']})
                        if (readback.status_code != 200 or readback.json()['hash'] != changed_hash
                            or readback.json()['content'] != changed):
                            raise RuntimeError('Changed helper source not independently available through the API')
                        if prepared.manifest[dependency] == changed_hash:
                            raise RuntimeError('Mutation did not change the prepared dependency')
                        report['source_mutation'] = {'path': dependency, 'prepared_sha256': prepared.manifest[dependency],
                            'changed_sha256': changed_hash, 'api_readback_status': readback.status_code}
                    await adapter.route_execution(execution_id=execution_id, context=prepared.context,
                        active_execution=active_execution, credential_route=credential_route)
                    attempt = adapter.attempt(execution_id)
                    slot.update(route=attempt.route, info=attempt.info, receipt=attempt.receipt,
                                invocation=attempt.invocation, accepted_monotonic=attempt.accepted_monotonic)

            consumer = WorkflowExecutionConsumer(pool=Parent())
            consumer.owner_worker_id = worker_id
            if retained_http:
                subject = next(run for run, slot in slots.items() if slot['label'] == 'fails-first')
                fault = RetainedHttpFault(subject, lambda: httpx.AsyncHTTPTransport(retries=0, trust_env=False))
                patcher = patch.object(invocation_module, 'httpx', SimpleNamespace(URL=httpx.URL, AsyncClient=fault.client))
                patcher.start()

            try:
                # Existing credential-free initialization avoids the separately
                # retained cold concurrent Pyodide initialization race. Each
                # credentialed run still receives a new child/interpreter.
                async with httpx.AsyncClient(timeout=30) as warmup:
                    response = await warmup.post('http://bifrost-pyodide-ddf316c1:8799/__fixture_warmup')
                    if response.status_code != 200:
                        raise RuntimeError('Credential-free local initialization failed')
                    if startup_inventory:
                        observed = response.json()
                        adapter.bind_runtime_inventory(observed)
                        with remote_owner_context(ref):
                            if not await renew_remote_owner(ref.lease):
                                raise RuntimeError('Owner lost before runtime cache registration')
                            cache = await native._get_redis()
                            runtime_cache = (native, cache, f'bifrost:pool:{worker_id}')
                            await native._register_worker()
                        raw_packages = await cast(Awaitable[str | None], cache.hget(runtime_cache[2], 'packages'))
                        if raw_packages is None:
                            raise RuntimeError('Runtime package cache missing after registration')
                        cached = json.loads(raw_packages)
                        if cached != observed['packages'] or native._started:
                            raise RuntimeError('Runtime cache differs from actual probe or claims full startup')
                        report.update(actual_runtime_inventory=observed,
                                      native_runtime_cache_projection_verified=True,
                                      full_native_startup_qualified=False)
                report['credential_free_prewarm'] = True
                with remote_owner_context(ref):
                    for _ in range(2):
                        claim = await claim_next(worker_id, fits_bytes=2**30, budget_bytes=2**30)
                        if claim is None or str(claim.subject_id) not in slots or claim.claimed is None:
                            raise RuntimeError('Exact two canonical fixture claims required')
                        requests[str(claim.subject_id)] = claim.memory_request_bytes
                        liveness['active_runs'] += 1
                        liveness['memory_reserved_bytes'] += claim.memory_request_bytes
                        await beat()
                        await consumer.process_message(claim.message, PendingExecution(**claim.claimed))
                if len(native.processes) != 2 or native._build_heartbeat()['busy_count'] != 2:
                    raise RuntimeError('One native pool did not retain both invocations')
                report['native_busy_counts'] = [native._build_heartbeat()['busy_count']]
                if not source_change and any(mux.collection(run).done() for run in slots
                                            if not retained_http or slots[run]['label'] != 'fails-first'):
                    raise RuntimeError('Fixture completed before both dispatch ACKs')
                failed = next(value for value in slots.values() if value['label'] == 'fails-first')
                peer = next(value for value in slots.values() if value['label'] == 'succeeds-second')
                async with httpx.AsyncClient(base_url='http://api:8000', timeout=10) as api:
                    async with asyncio.timeout(45):
                        for slot in ([peer] if source_change else slots.values()):
                            while True:
                                read = await api.get(f"/api/tables/{slot['table']}/documents/proof",
                                    headers={'Authorization': 'Bearer ' + slot['token']}, params={'scope': str(slot['org'].id)})
                                if read.status_code == 200 and read.json()['data']['value'] == slot['value']:
                                    break
                                if read.status_code != 404:
                                    raise RuntimeError('Independent shared SDK effect read failed')
                                await asyncio.sleep(0.25)
                    if not source_change and any(mux.collection(run).done() for run in slots
                                                if not retained_http or slots[run]['label'] != 'fails-first'):
                        raise RuntimeError('Both SDK writes must be held before release')
                    report['both_real_sdk_effects_held_concurrently'] = not source_change
                    handles = {slot['invocation'].runtime_handle for slot in slots.values()}
                    if retained_http:
                        if fault is None or fault.reader is None or fault.task is None or fault.task.done():
                            raise RuntimeError('Actual retained invocation must still be running')
                        first = adapter.attempt(str(failed['execution'].id))
                        for receipt in (first.receipt, first.delivery):
                            try:
                                await receipt
                            except httpx.ReadError:
                                pass
                            else:
                                raise RuntimeError('Original pre-ACK receipt did not fail')
                        if first.invocation.runtime_handle is not None or first.accepted_monotonic is not None:
                            raise RuntimeError('Original collector received a fabricated ACK')
                        handles = {fault.reader.runtime_handle, peer['invocation'].runtime_handle}
                    if None in handles or len(handles) != 2:
                        raise RuntimeError('Independent attempts shared a runtime handle')

                    async def release(slot):
                        response = await api.post('/api/sdk/config/set', headers={'Authorization': 'Bearer ' + slot['token']},
                            json={'key': slot['gate'], 'value': True, 'is_secret': False, 'scope': str(slot['org'].id)})
                        if response.status_code != 204:
                            raise RuntimeError('Authorized synthetic fixture release failed')

                    if late_result:
                        failure_consume_gate.set()
                        first = adapter.attempt(str(failed['execution'].id))
                        cutoff = first.info.started_at + timedelta(seconds=first.info.timeout_seconds)
                        await release(peer)
                        await adapter.attempt(str(peer['execution'].id)).delivery
                        if native._build_heartbeat()['busy_count'] != 1:
                            raise RuntimeError('Normal peer did not independently settle')
                        report['native_busy_counts'].append(1)
                        liveness.update(active_runs=1, runs_handled=1,
                                        memory_reserved_bytes=requests[str(failed['execution'].id)])
                        await beat()
                        old = ref.lease
                        ref.lease = await connect(2)
                        await beat()
                        if deadline_ack_loss:
                            try:
                                await first.deadline
                            except ConnectionError as exc:
                                if str(exc) != 'Injected shared failure ACK loss':
                                    raise
                            else:
                                raise RuntimeError('Deadline ACK loss was not observed')
                            await adapter.reconcile_deadline(str(failed['execution'].id))
                            report['explicit_deadline_receipt_recovery_verified'] = True
                        elif not (await first.deadline)['expired']:
                            raise RuntimeError('Finite workflow completed before original deadline')
                        if (first.info.started_at + timedelta(seconds=first.info.timeout_seconds) != cutoff
                            or mux.collection(str(failed['execution'].id)).done()
                            or native._build_heartbeat()['busy_count'] != 1):
                            raise RuntimeError('Logical timeout reset cutoff or released physical work')
                        report['native_busy_counts'].append(1)

                        async def timeout_snapshot():
                            async with get_db_context() as db:
                                row = await db.get(Execution, failed['execution'].id)
                                ids = sorted(str(value) for value in await db.scalars(select(ExecutionLog.id).where(
                                    ExecutionLog.execution_id == failed['execution'].id)))
                                return row.status, row.result, row.error_message, row.completed_at, ids

                        before = await timeout_snapshot()
                        if before[0] != ExecutionStatus.TIMEOUT or before[1] is not None or before[3] is None:
                            raise RuntimeError('Canonical Timeout did not precede physical settlement')
                        await beat()
                        async with get_db_context() as db:
                            row = await db.get(ExecutionWorker, worker_id)
                            if row.active_runs != 1 or row.memory_reserved_bytes != requests[str(failed['execution'].id)]:
                                raise RuntimeError('Timeout released unsettled worker accounting')
                        revoked = await api.post('/api/sdk/config/get',
                            headers={'Authorization': 'Bearer ' + failed['token']}, json={'key': failed['key']})
                        if revoked.status_code != 401:
                            raise RuntimeError('Runtime capability remained usable after Timeout')
                        from attempt_deadline import report_expired_workflow
                        try:
                            await report_expired_workflow(old, consumer, first.info)
                        except CredentialRouteChanged:
                            report['stale_deadline_owner_rejected'] = True
                        else:
                            raise RuntimeError('Stale deadline owner consumed timeout')
                        late = await first.receipt
                        await first.delivery
                        if deadline_ack_loss:
                            try:
                                await first.deadline
                            except ConnectionError as exc:
                                if str(exc) != 'Injected shared failure ACK loss':
                                    raise
                                report['original_deadline_receipt_failure_preserved'] = True
                            else:
                                raise RuntimeError('Original deadline failure was rewritten')
                        if not late['success'] or late['result']['value'] != failed['value']:
                            raise RuntimeError('Actual finite invocation did not return late success')
                        if await timeout_snapshot() != before or native.processes:
                            raise RuntimeError('Late success changed canonical Timeout or retained settled capacity')
                        calls = first.evidence['api_calls']
                        if not any(call['status'] == 410 and '/documents/upsert' in call['path'] for call in calls):
                            raise RuntimeError('No actual denied post-deadline SDK write observed')
                        async with get_db_context() as db:
                            table_id = await db.scalar(select(Table.id).where(Table.name == failed['table']))
                            if table_id is None or await db.get(Document, (table_id, 'late-denied')) is not None:
                                raise RuntimeError('Denied late SDK write left a document')
                        report['native_busy_counts'].append(0)
                        liveness.update(active_runs=0, runs_handled=2, memory_reserved_bytes=0)
                        await beat()
                        report.update(integrated_late_result_verified=True, original_cutoff_preserved=True,
                            timeout_committed_before_physical_settlement=True, late_success_preserved_timeout=True,
                            post_deadline_sdk_write_denied=True, denied_document_absent=True,
                            post_deadline_sdk_write_status=410,
                            access_revoked_before_late_result_status=revoked.status_code,
                            automatic_terminal_delivery_verified=True)
                    else:
                        if not source_change:
                            await release(failed)
                        retained_evidence = await retained_fault().wait() if retained_http else None
                        result = retained_evidence['result'] if retained_http else await failed['receipt']
                        if source_change:
                            failed_evidence = adapter.attempt(str(failed['execution'].id)).evidence
                            absent = await api.get(f"/api/tables/{failed['table']}/documents/proof",
                                headers={'Authorization': 'Bearer ' + failed['token']}, params={'scope': str(failed['org'].id)})
                            if (result['success'] or result['error_type'] == 'ValueError' or absent.status_code != 404
                                or any(not call['path'].startswith('/api/sdk/modules/')
                                       and call['path'] not in ('/api/sdk/modules-resolve', '/api/sdk/runtime/logs')
                                       for call in failed_evidence['api_calls'])):
                                raise RuntimeError('Changed dependency did not fail before workflow SDK effects')
                            report.update(source_change_probe=True, failed_document_status_before_terminal=absent.status_code,
                                changed_dependency_rejected_before_workflow_effects=True,
                                source_rejection_error_type=result['error_type'],
                                source_integrity_diagnostic_seen='Workspace module digest mismatch' in json.dumps(failed_evidence))
                        elif result['success'] or result['error_type'] != ('ExecutorBodyTooLargeError' if oversized_body else 'ExecutorFrameTooLargeError' if oversized_result else 'ValueError'):
                            raise RuntimeError('Expected first workflow error missing')
                        if oversized_body:
                            outcome = adapter.attempt(str(failed['execution'].id)).evidence.get('provider_outcome', {})
                            if (outcome.get('response_eof') is not True
                                or outcome.get('engine_outcome_delivered') is not False
                                or not 1048576 < outcome.get('response_bytes', 0) <= 4194304):
                                raise RuntimeError('Oversized body EOF evidence invalid')
                            report['oversized_body_failure_verified'] = True
                        if oversized_result:
                            evidence = adapter.attempt(str(failed['execution'].id)).evidence
                            outcome = evidence.get('provider_outcome', {})
                            if (outcome.get('execute_returned') is not True
                                or outcome.get('engine_outcome_delivered') is not False
                                or outcome.get('frame_limit_bytes') != 262144
                                or len(json.dumps(evidence).encode()) >= 2048):
                                raise RuntimeError('Oversized terminal delivery failure evidence invalid')
                            report['oversized_terminal_failure_verified'] = True
                        failure_consume_gate.set()
                        try:
                            if retained_http:
                                await adapter.reconcile_retained(str(failed['execution'].id), retained_evidence)
                            else:
                                await adapter.attempt(str(failed['execution'].id)).delivery
                        except ConnectionError as exc:
                            if str(exc) != 'Injected shared failure ACK loss':
                                raise
                        else:
                            raise RuntimeError('Lost ACK was not observed')

                        async def failed_snapshot():
                            async with get_db_context() as db:
                                row = await db.get(Execution, failed['execution'].id)
                                log_ids = sorted(str(value) for value in await db.scalars(select(ExecutionLog.id).where(
                                    ExecutionLog.execution_id == failed['execution'].id)))
                                return row.status, row.result, row.error_message, row.completed_at, log_ids

                        first_commit = await failed_snapshot()
                        if source_change:
                            peer_attempt = adapter.attempt(str(peer['execution'].id))
                            peer_evidence = peer_attempt.evidence
                            peer_result = None if peer_evidence is None else peer_evidence['result']
                            cutoff = peer['info'].started_at + timedelta(seconds=peer['info'].timeout_seconds)
                            diagnostic = {'source_change_peer_diagnostic': True,
                                'native_busy_count': native._build_heartbeat()['busy_count'],
                                'peer_collection_done': mux.collection(str(peer['execution'].id)).done(),
                                'peer_success': None if peer_result is None else peer_result['success'],
                                'peer_error_type': None if peer_result is None else peer_result.get('error_type'),
                                'peer_last_calls': [] if peer_evidence is None else peer_evidence['api_calls'][-5:],
                                'peer_remaining_deadline_ms': (cutoff - datetime.now(timezone.utc)).total_seconds() * 1000,
                                'source_error_type': result['error_type']}
                            encoded_diagnostic = json.dumps(diagnostic)
                            if any(secret and secret in encoded_diagnostic for secret in secrets):
                                raise RuntimeError('Credential exposure: source-change diagnostic withheld')
                            print(encoded_diagnostic, flush=True)
                        if native._build_heartbeat()['busy_count'] != 2 or mux.collection(str(peer['execution'].id)).done():
                            raise RuntimeError('Failure ACK loss disturbed live peer')
                        report['native_busy_counts'].append(native._build_heartbeat()['busy_count'])
                        old = ref.lease
                        ref.lease = await connect(2)
                        await beat()
                        try:
                            if retained_http:
                                await adapter.reconcile_retained(str(failed['execution'].id), retained_evidence, lease=old)
                            else:
                                await adapter.reconcile(str(failed['execution'].id), lease=old)
                        except CredentialRouteChanged:
                            report['stale_terminal_owner_rejected'] = True
                        else:
                            raise RuntimeError('Stale terminal owner accepted')
                        acknowledged = (await adapter.reconcile_retained(str(failed['execution'].id), retained_evidence)
                            if retained_http else await adapter.reconcile(str(failed['execution'].id)))
                        if retained_http:
                            retained = retained_fault()
                            task = retained.task
                            if task is None:
                                raise RuntimeError('Retained collection missing')
                            report.update(retained_http_preack_loss_verified=True, parent_accepted_observed=False,
                                original_collection_error_type='ReadError', retained_posts=retained.posts,
                                retained_collector_eof_complete=task.done() and task.exception() is None)
                        if not acknowledged or native._build_heartbeat()['busy_count'] != 1 or peer['route'] not in native.processes:
                            raise RuntimeError('Failure reconciliation did not preserve peer obligation')
                        report['native_busy_counts'].append(native._build_heartbeat()['busy_count'])
                        liveness.update(active_runs=1, runs_handled=1,
                                        memory_reserved_bytes=requests[str(peer['execution'].id)])
                        await beat()
                        async with get_db_context() as db:
                            worker_row = await db.get(ExecutionWorker, worker_id)
                            if (worker_row.active_runs != 1 or worker_row.runs_handled != 1
                                or worker_row.memory_reserved_bytes != requests[str(peer['execution'].id)]):
                                raise RuntimeError('Canonical worker accounting did not retain just the peer')
                        report['canonical_peer_accounting_verified'] = True
                        if await failed_snapshot() != first_commit:
                            raise RuntimeError('Failure ACK replay duplicated canonical state/logs')
                        report['failed_replay_unchanged_verified'] = True

                        await adapter.route_execution(execution_id=str(peer['execution'].id), context=peer['original_context'],
                            active_execution=peer['original_active'], credential_route=adapter.credential_route())
                        if adapter.attempt(str(peer['execution'].id)).receipt is not peer['receipt']:
                            raise RuntimeError('Peer duplicate did not reuse original receipt')
                        peer_access = await api.post('/api/sdk/config/get', headers={'Authorization': 'Bearer ' + peer['token']},
                                                     json={'key': peer['key']})
                        failed_access = await api.post('/api/sdk/config/get', headers={'Authorization': 'Bearer ' + failed['token']},
                                                       json={'key': failed['key']})
                        if peer_access.status_code != 200 or failed_access.status_code != 401:
                            raise RuntimeError('Terminal credential state leaked between attempts')
                        report.update(peer_live_after_failure_reconcile=True, failed_access_status=401, peer_access_status=200,
                                      duplicate_dispatch_reused_peer=True, lost_ack_reconnect_verified=True)
                        await release(peer)
                        success = await peer['receipt']
                        if not success['success'] or success['result']['value'] != peer['value']:
                            raise RuntimeError('Independent successful peer result mismatch')
                        if numeric_result and success['result'].get('exact_integer') != 9007199254740993:
                            raise RuntimeError('Integer transport fidelity lost')
                        if numeric_result:
                            report['exact_integer_roundtrip_verified'] = True
                        delivered = await adapter.attempt(str(peer['execution'].id)).delivery
                        if not delivered['acknowledged'] or native.processes or native._build_heartbeat()['busy_count'] != 0:
                            raise RuntimeError('Final settled peer obligation remained')
                        report['automatic_terminal_delivery_verified'] = True
                        report['native_busy_counts'].append(native._build_heartbeat()['busy_count'])
                        liveness.update(active_runs=0, runs_handled=2, memory_reserved_bytes=0)
                        await beat()
                        async with get_db_context() as db:
                            worker_row = await db.get(ExecutionWorker, worker_id)
                            if worker_row.active_runs != 0 or worker_row.runs_handled != 2 or worker_row.memory_reserved_bytes != 0:
                                raise RuntimeError('Canonical settled worker accounting was not released')
                        report['canonical_settled_accounting_verified'] = True
                    for run, slot in slots.items():
                        attempt = adapter.attempt(run)
                        slot['evidence'], slot['terminal_monotonic'] = attempt.evidence, attempt.terminal_monotonic
                        report['launches'][run] = attempt.launches
                        status = (ExecutionStatus.TIMEOUT if late_result else ExecutionStatus.FAILED) if slot is failed else ExecutionStatus.SUCCESS
                        async with get_db_context() as db:
                            row = await db.get(Execution, slot['execution'].id)
                            logs = list(await db.scalars(select(ExecutionLog.message).where(ExecutionLog.execution_id == slot['execution'].id)))
                            if row.status != status:
                                raise RuntimeError('Canonical independent terminal status mismatch')
                            if not logs:
                                if (not source_change or slot is not failed or not row.error_message
                                    or attempt.evidence['result']['error_type'] != 'WorkflowLoadError'
                                    or attempt.evidence['result']['logs'] != []):
                                    raise RuntimeError('Canonical independent terminal logs missing')
                                report.update(upstream_load_error_empty_logs_verified=True,
                                              failed_engine_log_count=0, failed_persisted_log_count=0)
                            persisted_result = row.result
                            error = row.error_message
                        revoked = await api.post('/api/sdk/config/get', headers={'Authorization': 'Bearer ' + slot['token']}, json={'key': slot['key']})
                        if revoked.status_code != 401:
                            raise RuntimeError('Completed shared credential remained usable')
                        if refresh_redaction and slot is failed:
                            marker = 'refresh_probe original=[REDACTED] replacement=[REDACTED] refresh=[REDACTED]'
                            if not error or marker not in error or not any(marker in message for message in logs):
                                raise RuntimeError('Refreshed credential redaction missing from canonical output')
                            report['run_refresh_generation_redaction_verified'] = True
                        if memory_pressure and slot is failed:
                            observed = [message for message in logs if 'bounded_memory_probe=' in message]
                            outcomes = [value for value in ('allocated_160mib', 'python_memory_error')
                                if error and error.endswith('bounded_memory_probe=' + value)]
                            if len(outcomes) != 1:
                                raise RuntimeError('Bounded memory observation missing')
                            report['bounded_memory_matching_log_count'] = len(observed)
                            report['bounded_memory_outcome'] = outcomes[0]
                            report['bounded_memory_probe_bytes'] = 160 * 1024 * 1024
                        report['attempts'][run] = {'label': slot['label'], 'canonical_status': status.value,
                            'canonical_result': persisted_result, 'canonical_error': error, 'persisted_logs': logs,
                            'runtime_handle': slot['evidence']['runtime_handle'], 'route': slot['route'],
                            'accepted_monotonic': slot['accepted_monotonic'], 'terminal_monotonic': slot['terminal_monotonic'],
                            'evidence': slot['evidence'], 'access_after_terminal_status': revoked.status_code,
                            'preparation': slot['preparation']}
                    if runtime_cache is not None:
                        await native._unregister_worker()
                        if await runtime_cache[1].exists(runtime_cache[2]):
                            raise RuntimeError('Runtime cache cleanup unconfirmed')
                        runtime_cache = None
                        report['native_runtime_cache_removed'] = True
                    await adapter.retire()
                    if native._redis is not None:
                        raise RuntimeError('Owned native Redis client remains after retirement')
                    if mux.retained_count != 0:
                        raise RuntimeError('Retired request collector retained credentials')
                    report.update(single_native_pool_verified=True, terminal_obligations_released=True,
                                  canonical_terminal_reconciliation_verified=True, mux_retired_after_canonical_ack=True,
                                  reusable_private_adapter_verified=True, api_preparation_verified=True)
                if heartbeat.done():
                    heartbeat.result()
            finally:
                await cancel_and_join(heartbeat)
                heartbeat = None
                await release_remote_owner(ref.lease)
                ref = None
                async with get_db_context() as db:
                    ids = [slot['execution'].id for slot in slots.values()]
                    await db.execute(delete(ExecutionLog).where(ExecutionLog.execution_id.in_(ids)))
                    await db.execute(delete(WorkDispatch).where(WorkDispatch.subject_id.in_(ids)))
        async with get_db_context() as db:
            if await db.scalar(select(ExecutionWorkerClaim).where(ExecutionWorkerClaim.worker_id == worker_id)) is not None:
                raise RuntimeError('Fixture worker claims survived cleanup')
        report['fixture_worker_cascade_cleanup_verified'] = True
        encoded = json.dumps(report)
        if (any(secret and secret in encoded for secret in secrets)
            or re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', encoded)):
            raise RuntimeError('Credential exposure: shared evidence withheld')
        print(encoded)
    finally:
        if runtime_cache is not None:
            owner, cache, key = runtime_cache
            await owner._unregister_worker()
            if await cache.exists(key):
                raise RuntimeError('Failed fixture runtime cache cleanup unconfirmed')
        if patcher is not None:
            patcher.stop()
        if fault is not None:
            await fault.close()
        if heartbeat is not None:
            await cancel_and_join(heartbeat)
        if ref is not None:
            await release_remote_owner(ref.lease)
        for path in sources:
            await invalidate_module(path)
            await storage.delete(path)
        await close_db()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--source-change', action='store_true')
    parser.add_argument('--late-result', action='store_true')
    parser.add_argument('--retained-http', action='store_true')
    parser.add_argument('--deadline-ack-loss', action='store_true')
    parser.add_argument('--refresh-redaction', action='store_true')
    parser.add_argument('--memory-pressure', action='store_true')
    parser.add_argument('--oversized-body', action='store_true')
    parser.add_argument('--numeric-result', action='store_true')
    parser.add_argument('--oversized-result', action='store_true')
    parser.add_argument('--startup-inventory', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main(source_change=args.source_change, late_result=args.late_result, retained_http=args.retained_http,
                     deadline_ack_loss=args.deadline_ack_loss, startup_inventory=args.startup_inventory, oversized_result=args.oversized_result, numeric_result=args.numeric_result, oversized_body=args.oversized_body, memory_pressure=args.memory_pressure, refresh_redaction=args.refresh_redaction))
