"""One real Linux workflow over upstream signed control; isolated test data only."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import runpy
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import websockets


async def cancel_control_receiver(task):
    """Join the receiver before launcher cleanup; retain non-cancellation errors."""
    task.cancel()
    outcome, = await asyncio.gather(task, return_exceptions=True)
    if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
        raise outcome


async def main(*, benchmark_probe=False):
    target = urlsplit(os.environ.get('BIFROST_DATABASE_URL', ''))
    if (os.environ.get('BIFROST_ENVIRONMENT') != 'testing' or os.environ.get('TEST_API_URL') != 'http://api:8000'
            or target.hostname != 'pgbouncer' or target.path != '/bifrost_test'):
        raise RuntimeError('Own disposable stack required')
    from sqlalchemy import delete, select
    from src.core.database import init_db, close_db, get_engine, get_db_context
    from src.core.module_cache import set_module, invalidate_module
    from src.core.redis_client import pending_execution_record
    from src.core.security import encrypt_secret
    from src.models.enums import ConfigType, ExecutionStatus
    from src.models.orm import Execution, ExecutionLog
    from src.models.orm.config import Config
    from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
    from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
    from src.models.orm.tables import Table, Document
    from src.models.orm.work_dispatches import WorkDispatch
    from src.services.execution.workflow_dispatch import stage_workflow_run
    from src.services.repo_storage import RepoStorage
    from src.services.worker_pool.claims import register_static_worker
    from src.worker.remote.identity import LauncherIdentity
    from src.worker.remote.launcher import Launcher, _receive_control_commands
    from tests.unit.authorization.committed_runs import committed_world
    from shared.version import get_version

    await init_db()
    helpers = runpy.run_path('/spike/backend/authorized-local.py')
    storage = RepoStorage()
    suffix = uuid4().hex
    path, helper = f'workflows/linux_reference_{suffix}.py', f'linux_helper_{suffix}'
    key, table, name = f'linux-config-{suffix}', f'linux-table-{suffix}', f'linux-integration-{suffix}'
    synthetic = 'synthetic-linux-reference-' + uuid4().hex
    secrets = [synthetic, os.environ['BIFROST_SECRET_KEY']]
    benchmark_code = (f"    from {helper} import measure\n"
                      f"    benchmark = await measure({key!r}, {name!r}, {table!r})\n") if benchmark_probe else "    benchmark = {}\n"
    source = f'''from bifrost import workflow, config, tables, integrations
from {helper} import fold
import hashlib, httpx, logging, os
@workflow
async def qualified(n: int = 7):
    value = await config.get({key!r})
    integration = await integrations.get({name!r})
    if integration is None or integration.config.get('qualification_value') != 42:
        raise RuntimeError('Integration configuration mismatch')
    secret = integration.config.get('qualification_secret')
    if hashlib.sha256(secret.encode()).hexdigest() != {hashlib.sha256(synthetic.encode()).hexdigest()!r}:
        raise RuntimeError('Synthetic secret decryption mismatch')
    logging.getLogger(__name__).warning('integration secret=%s', secret)
    doc = await tables.upsert({table!r}, id='proof', data={{'value':value + fold(n)}})
{benchmark_code.rstrip()}
    logging.getLogger(__name__).warning('Linux control reference')
    return {{'value':doc.data['value'],'integration_secret_probe':secret,
             'credential_redaction_probe':os.environ['BIFROST_ACCESS_TOKEN'],
             'parent_secret_present':'BIFROST_SECRET_KEY' in os.environ, **benchmark}}
'''
    sources = {path:source, helper + '.py':'def fold(n):\n    return sum(range(1,n+1))\n'}
    if benchmark_probe:
        sources[helper + '.py'] += Path(__file__).with_name('benchmark-workloads.py').read_text()
    report = {'runtime':'actual-linux-signed-control-reference','cloud_invocations':0,
              'pyodide_physical_termination_verified':False,'qualification_passed':False,
              'workspace_sha256':{p:hashlib.sha256(c.encode()).hexdigest() for p,c in sources.items()}}
    launcher = socket = receive = None
    try:
        async with committed_world(get_engine()) as world, helpers['cleanup_fixture_audit'](world, get_engine()):
            org = await world.organization()
            user = await world.person(org)
            role = await world.role('configs.read','tables.read','tables.readwrite','integrations.read','secrets.read')
            await world.assign(user, role.id, ('organization',org.id))
            integration = await world._add(Integration(name=name))
            await world._add(IntegrationMapping(integration_id=integration.id, organization_id=org.id, entity_id='synthetic'))
            for schema_key, schema_type, raw in [('qualification_value','int',42),('qualification_secret','secret',encrypt_secret(synthetic))]:
                schema = await world._add(IntegrationConfigSchema(integration_id=integration.id,key=schema_key,type=schema_type))
                await world._add(Config(key=schema_key,value={'value':raw},config_type=ConfigType.INT if schema_type=='int' else ConfigType.SECRET,
                    organization_id=org.id,integration_id=integration.id,config_schema_id=schema.id,updated_by=str(user.id)))
            await world._add(Config(key=key,value={'value':42},config_type=ConfigType.INT,organization_id=org.id,updated_by=str(user.id)))
            workflow = await world.workflow(org)
            workflow.path, workflow.function_name, workflow.timeout_seconds = path, 'qualified', 30
            execution = await world.execution(run_user=user,workflow=workflow,organization=org,
                status=ExecutionStatus.PENDING,credential_version=2)
            pending = pending_execution_record(execution_id=str(execution.id),workflow_id=str(workflow.id),parameters={'n':7},
                org_id=str(org.id),user_id=str(user.id),user_name=user.name,user_email=user.email,sync=False,lineage=None)
            staged = await stage_workflow_run(world.session,pending,
                {'execution_id':str(execution.id),'workflow_id':str(workflow.id),'sync':False},record_exists=True)
            world._created.append((WorkDispatch,staged.dispatch_id))
            await world.session.commit()
            for filename, code in sources.items():
                await storage.write(filename,code.encode())
                await set_module(filename,code,report['workspace_sha256'][filename])
            worker_id = await register_static_worker(hostname='pyodide-linux-control-reference',incarnation_id=uuid4(),
                supported_handlers=['workflow.execute'],version=get_version())
            world._created.append((ExecutionWorker,worker_id))
            worker = await world.session.get(ExecutionWorker,worker_id)
            worker.reserved_for_dispatch_id = staged.dispatch_id
            await world.session.commit()
            events = {'workflow_commands':0,'pid_acks':[],'result_events':0}

            class RecordingSocket:
                def __init__(self, actual):
                    self.actual = actual

                async def send(self, raw):
                    frame = json.loads(raw)
                    payload = frame.get('payload',{})
                    result = payload.get('result',{})
                    if isinstance(result.get('pid'),int):
                        events['pid_acks'].append(result['pid'])
                    if payload.get('event') == 'result':
                        events['result_events'] += 1
                    await self.actual.send(raw)

                async def recv(self):
                    raw = await self.actual.recv()
                    payload = json.loads(raw).get('payload',{})
                    if payload.get('action') == 'workflow':
                        events['workflow_commands'] += 1
                        context = payload['payload']['context']
                        secrets.extend([context.get('engine_token'),context.get('engine_refresh_token')])
                    return raw

                async def close(self, **kwargs):
                    await self.actual.close(**kwargs)

            try:
                socket = await websockets.connect('ws://api:8000/api/worker-control',open_timeout=10,close_timeout=10)
                ready = asyncio.Event()
                identity = LauncherIdentity(uuid4(),worker_id,None,None,'pyodide-linux-control-reference','static')
                launcher = Launcher(RecordingSocket(socket),identity=identity,secret=os.environ['BIFROST_SECRET_KEY'],on_ready=ready.set)
                await launcher.hello({})
                await asyncio.wait_for(launcher.start(),timeout=45)
                receive = asyncio.create_task(_receive_control_commands(launcher,launcher.socket,asyncio.Event()))
                await launcher.send({'event':'inventory','inventory':launcher.inventory(),'requirements':launcher.requirements})
                await asyncio.wait_for(ready.wait(),timeout=30)
                async with asyncio.timeout(60):
                    while True:
                        async with get_db_context() as db:
                            row = await db.get(Execution,execution.id)
                            if row.status in (ExecutionStatus.SUCCESS,ExecutionStatus.FAILED):
                                if row.status != ExecutionStatus.SUCCESS:
                                    report['workflow_error'] = row.error_message
                                assert row.status == ExecutionStatus.SUCCESS
                                assert row.result['value'] == 70
                                assert row.result['integration_secret_probe'] == '[REDACTED]'
                                assert row.result['credential_redaction_probe'] == '[REDACTED]'
                                assert row.result['parent_secret_present'] is False
                                doc = await db.scalar(select(Document).join(Table,Table.id==Document.table_id).where(
                                    Table.name==table,Table.organization_id==org.id,Document.id=='proof'))
                                assert doc.data['value'] == 70
                                assert await db.scalar(select(ExecutionWorkerClaim).where(
                                    ExecutionWorkerClaim.worker_id==worker_id,ExecutionWorkerClaim.subject_id==execution.id))
                                report['terminal_status'],report['independent_document_value'] = row.status.value,doc.data['value']
                                if benchmark_probe:
                                    report['benchmark'] = row.result['benchmark']
                                    documents = list(await db.scalars(select(Document).join(Table,Table.id==Document.table_id).where(
                                        Table.name==table,Table.organization_id==org.id,Document.id.in_(['benchmark-0','benchmark-1','benchmark-2']))))
                                    if len(documents) != 3 or any(document.data['value'] != 70 for document in documents):
                                        raise RuntimeError('Independent benchmark document readback failed')
                                    report['independent_benchmark_documents'] = len(documents)
                                    report['retained_log_messages'] = list(await db.scalars(select(ExecutionLog.message).where(
                                        ExecutionLog.execution_id==execution.id)))
                                break
                        await asyncio.sleep(0.1)
                async with asyncio.timeout(10):
                    while launcher.children or launcher.pending_terminal:
                        await asyncio.sleep(0.1)
                assert events['workflow_commands']==1 and len(events['pid_acks'])==1 and events['result_events']==1
                assert events['pid_acks'][0]>0
                async with httpx.AsyncClient(base_url='http://api:8000') as api:
                    revoked = await api.post('/api/sdk/config/get',headers={'Authorization':'Bearer '+secrets[-2]},json={'key':key})
                    assert revoked.status_code==401
                report.update(qualification_passed=True,wire_events=events,signed_session_generation=launcher.session_generation,
                    runtime_access_after_terminal_status=401,retained_terminal_acknowledged=True,real_linux_fork_verified=True)
            finally:
                if receive is not None:
                    await cancel_control_receiver(receive)
                if launcher is not None:
                    await launcher.stop()
                    report['native_templates_stopped'] = not launcher.template.is_alive() and not launcher.agent_template.is_alive()
                if socket is not None:
                    await socket.close()
                async with get_db_context() as db:
                    await db.execute(delete(ExecutionLog).where(ExecutionLog.execution_id==execution.id))
        async with get_db_context() as db:
            assert await db.get(ExecutionWorker,worker_id) is None
        report['fixture_worker_cleanup_verified'] = True
    finally:
        for filename in sources:
            await invalidate_module(filename)
            await storage.delete(filename)
        await close_db()
        encoded = json.dumps(report)
        if any(secret and secret in encoded for secret in secrets):
            raise RuntimeError('Diagnostic credential exposure refused')
        print(encoded)


if __name__ == '__main__':
    try:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--benchmark-probe', action='store_true')
        asyncio.run(main(benchmark_probe=parser.parse_args().benchmark_probe))
    except Exception as error:
        print(json.dumps({'runtime':'linux-control-reference-error','error_type':type(error).__name__}))
        raise SystemExit(1)
