"""Opt-in real-API/Pyodide qualification, restricted to the disposable test stack.

Fixture rows use the existing committed token-test world. They are not an
execution adapter writing business-job state. This does not claim dispatch,
ownership or terminal acceptance, which need separate canonical conformance.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4
from urllib.parse import urlsplit

import httpx


@asynccontextmanager
async def seeded_config(api, headers, key, scope):
    response = await api.post("/api/sdk/config/set", headers=headers,
                              json={"key": key, "value": 42, "is_secret": False, "scope": scope})
    try:
        assert response.status_code == 204, f"Canonical runtime credential seed status {response.status_code}"
        yield
    finally:
        if response.is_success:
            deleted = await api.post("/api/sdk/config/delete", headers=headers, json={"key": key, "scope": scope})
            assert deleted.status_code == 200, f"Fixture config cleanup status {deleted.status_code}"


@asynccontextmanager
async def cleanup_fixture_audit(world, engine):
    """Same exact-fixture audit teardown as upstream worker SDK HTTP tests."""
    from sqlalchemy import delete, or_
    from sqlalchemy.ext.asyncio import AsyncSession
    from src.models.orm import AuditLog, Execution, ExecutionLog, Organization, User

    try:
        yield
    finally:
        orgs = [row_id for model, row_id in world._created if model is Organization]
        users = [row_id for model, row_id in world._created if model is User]
        executions = [row_id for model, row_id in world._created if model is Execution]
        async with AsyncSession(engine) as session:
            await session.execute(delete(ExecutionLog).where(ExecutionLog.execution_id.in_(executions)))
            await session.execute(delete(AuditLog).where(or_(AuditLog.organization_id.in_(orgs),
                AuditLog.user_id.in_(users), AuditLog.execution_id.in_(executions))))
            await session.commit()


def reserve_development_invocation(execution_id):
    """Reserve once before sending; keep uncertain requests charged."""
    import fcntl
    from pathlib import Path

    ledger = Path('/qualification/private/invocations.jsonl')
    profile = json.loads(Path('/qualification/private/profile.json').read_text())
    limit = profile.get('maximum_invocations', 20)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise RuntimeError('Invalid bounded development invocation limit')
    with ledger.open('a+') as reservations:
        fcntl.flock(reservations, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reservations.seek(0)
        count = sum(1 for line in reservations if line.strip())
        if count >= limit:
            raise RuntimeError('Fresh development invocation budget exhausted')
        reservations.write(json.dumps({'reservation': count + 1, 'execution_id': str(execution_id)}) + '\n')
        reservations.flush()
        os.fsync(reservations.fileno())


async def main(*, dev_qualification=False, integration_probe=False, fresh_probe=False, stream_probe=False,
               benchmark=None) -> None:
    if benchmark is not None and benchmark not in (
        'json_transform', 'http_orchestration', 'integration_read', 'table_update', 'multi_step'
    ):
        raise ValueError('Unsupported fixed benchmark workload')
    database = urlsplit(os.environ.get("BIFROST_DATABASE_URL", ""))
    provider_url = "http://bifrost-pyodide-ddf316c1:8799"
    provider_headers = {}
    if dev_qualification:
        from pathlib import Path
        profile = json.loads(Path('/qualification/private/profile.json').read_text())
        if (profile.get('deployment') != 'pyodide-ddf316c1-dev-2037ac0'
            or os.environ.get('BIFROST_ENVIRONMENT') != 'development'
            or database.hostname != 'pgbouncer' or database.path != '/bifrost'
            or profile.get('expires_at_ms', 0) <= time.time() * 1000):
            raise RuntimeError('Owned isolated development qualification required')
        provider_url = 'https://bifrost-pyodide-spike-ddf316c1.midtown-technology-group.workers.dev'
        provider_headers = {'X-Dev-Auth': profile['DEV_AUTH'], 'User-Agent': 'bifrost-pyodide-dev-qualification/1.0'}
    elif (os.environ.get("BIFROST_ENVIRONMENT") != "testing"
          or os.environ.get("TEST_API_URL") != "http://api:8000"
          or database.hostname != "pgbouncer" or database.path != "/bifrost_test"):
        raise RuntimeError("Only the repository disposable Docker test stack is admitted")

    from src.core.database import close_db, get_engine, init_db
    from src.core.module_cache import invalidate_module, set_module
    from src.services.authorization.tokens import RuntimeSubject, issue_run_credentials
    from src.services.repo_storage import RepoStorage
    from tests.unit.authorization.committed_runs import committed_world

    await init_db()
    storage = RepoStorage()
    suffix = uuid4().hex
    path = f"workflows/pyodide_qualification_{suffix}.py"
    helper = f"pyodide_helper_{suffix}"
    helper_path = f"{helper}.py"
    config_key = f"pyodide-qualification-{suffix}"
    table = f"pyodide-qualification-{suffix}"
    integration_name = f'pyodide-integration-{suffix}'
    integration_secret = f'synthetic-{uuid4().hex}' if integration_probe else None
    fresh_code = f'''    import builtins, json
    from pathlib import Path
    marker = Path('/tmp/bifrost-cloud-qualification-canary')
    fresh = {{'builtins_before': getattr(builtins, '_bifrost_cloud_canary', None),
             'module_before': getattr(json, '_bifrost_cloud_canary', None),
             'environment_before': os.environ.get('BIFROST_CLOUD_CANARY'),
             'tmp_before': marker.read_text() if marker.exists() else None}}
    if any(value is not None for value in fresh.values()):
        raise RuntimeError('Cross-run contamination detected before SDK effects')
    builtins._bifrost_cloud_canary = {suffix!r}
    json._bifrost_cloud_canary = {suffix!r}
    os.environ['BIFROST_CLOUD_CANARY'] = {suffix!r}
    marker.write_text({suffix!r})
''' if fresh_probe else '    fresh = {}\n'
    integration_code = f'''    import hashlib, httpx
    from bifrost import integrations
    integration = await integrations.get({integration_name!r})
    if integration.config['qualification_value'] != 42:
        raise RuntimeError('Integration configuration mismatch')
    if hashlib.sha256(integration.config['qualification_secret'].encode()).hexdigest() != {hashlib.sha256(integration_secret.encode()).hexdigest() if integration_secret else None!r}:
        raise RuntimeError('Integration secret mismatch')
    try:
        await integrations.get({integration_name!r}, scope=forbidden_scope)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
    else:
        raise RuntimeError('Cross-tenant integration read allowed')
    logging.getLogger(__name__).warning('integration secret=%s', integration.config['qualification_secret'])
    fresh['integration_value'] = 42
    fresh['integration_secret_probe'] = integration.config['qualification_secret']
''' if integration_probe else ''
    stream_code = '''    import sys
    print('stream stdout credential=' + os.environ['BIFROST_ACCESS_TOKEN'])
    print('stream stderr credential=' + os.environ['BIFROST_ACCESS_TOKEN'], file=sys.stderr)
''' if stream_probe else ''
    source = f'''
import logging
import os
from bifrost import workflow, config, tables
from {helper} import fold

@workflow
async def qualified(n: int = 7, forbidden_scope: str | None = None):
{fresh_code.rstrip()}
    value = await config.get({config_key!r})
{integration_code.rstrip()}
    document = await tables.upsert({table!r}, id="proof", data={{"value": value + fold(n)}})
{stream_code.rstrip()}
    logging.getLogger(__name__).warning("authorized local Pyodide workflow")
    return {{"value": document.data["value"], "parent_secret_present": "BIFROST_SECRET_KEY" in os.environ,
             "transport_url": os.environ["BIFROST_API_URL"],
             "credential_redaction_probe": os.environ["BIFROST_ACCESS_TOKEN"], **fresh}}
'''
    sources = {path: source, helper_path: "def fold(n):\n    return sum(range(1, n + 1))\n"}
    if benchmark is not None:
        from pathlib import Path
        benchmark_source = Path('/qualification/benchmark-workloads.py').read_text()
        sources[helper_path] += '\n' + benchmark_source
        source = f'''import logging, os
from bifrost import workflow
from {helper} import measure
@workflow
async def qualified(n: int = 7, forbidden_scope: str | None = None):
    measured = await measure({config_key!r}, {integration_name!r}, {table!r}, selected={benchmark!r})
    logging.getLogger(__name__).warning('fixed cloud benchmark complete')
    return {{**measured, 'parent_secret_present': 'BIFROST_SECRET_KEY' in os.environ,
             'transport_url': os.environ['BIFROST_API_URL']}}
'''
        sources[path] = source
    manifest = {name: hashlib.sha256(code.encode()).hexdigest() for name, code in sources.items()}
    report = {"runtime": "cloudflare-real-upstream-worker" if dev_qualification else "local-workerd-real-upstream-worker", "canonical_claim_verified": False,
              "canonical_terminal_acceptance_verified": False, "workspace_sha256": manifest}
    access_token = None
    try:
        async with committed_world(get_engine()) as world, cleanup_fixture_audit(world, get_engine()):
            organization = await world.organization()
            other = await world.organization()
            user = await world.person(organization)
            role = await world.role("configs.read", "configs.readwrite", "tables.read", "tables.readwrite",
                                    *(("integrations.read", "secrets.read") if integration_probe else ()))
            if integration_probe:
                from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
                from src.models.orm.config import Config
                from src.models.enums import ConfigType
                from src.core.security import encrypt_secret
                integration = await world._add(Integration(name=integration_name))
                await world._add(IntegrationMapping(integration_id=integration.id, organization_id=organization.id,
                                                   entity_id='synthetic-cloud-qualification'))
                for key, kind, value in [('qualification_value', ConfigType.INT, 42),
                                         ('qualification_secret', ConfigType.SECRET, encrypt_secret(integration_secret))]:
                    schema = await world._add(IntegrationConfigSchema(integration_id=integration.id, key=key, type=kind.value))
                    await world._add(Config(key=key, value={'value': value}, config_type=kind,
                                           organization_id=organization.id, integration_id=integration.id,
                                           config_schema_id=schema.id, updated_by=str(user.id)))
            await world.assign(user, role.id, ("organization", organization.id))
            workflow = await world.workflow(organization)
            workflow.path = path
            workflow.function_name = "qualified"
            execution = await world.execution(run_user=user, workflow=workflow, organization=organization,
                                              credential_version=2)
            execution.workflow_name = workflow.name
            await world.session.commit()
            for name, code in sources.items():
                digest = await storage.write(name, code.encode())
                assert digest == manifest[name]
                await set_module(name, code, digest)

            credentials = await issue_run_credentials(RuntimeSubject("workflow_execution", execution.id),
                                                      protocol=2, legacy_lifetime_seconds=300)
            access_token = credentials.access_token
            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient(base_url="http://api:8000", timeout=60, follow_redirects=False) as api, seeded_config(
                api, headers, config_key, str(organization.id)
            ):
                denied = await api.post("/api/sdk/config/get", headers=headers,
                                       json={"key": config_key, "scope": str(other.id)})
                assert denied.status_code == 403, f"Cross-tenant status {denied.status_code}"
                module_preflight = await api.get('/api/sdk/modules/' + path, headers=headers)
                report['internal_module_preflight_status'] = module_preflight.status_code
                if module_preflight.status_code != 200:
                    print(json.dumps(report))
                    raise RuntimeError('Internal API module preflight failed before provider invocation')
                context = {"execution_id": str(execution.id), "workflow_id": str(workflow.id),
                           "name": workflow.name, "function_name": "qualified", "file_path": path,
                           "content_hash": manifest[path], "parameters": {"n": 7, "forbidden_scope": str(other.id)},
                           "caller": {"user_id": str(user.id), "email": user.email, "name": user.name},
                           "organization": {"id": str(organization.id), "name": organization.name, "is_active": True,
                                            "is_provider": False},
                           "engine_token": access_token, "engine_refresh_token": credentials.refresh_token,
                           "timeout_seconds": 30, "transient": True, "no_cache": True}
                async with httpx.AsyncClient(timeout=90, follow_redirects=False) as provider:
                    if dev_qualification:
                        payload = {'execution_id': str(execution.id), 'context': context, 'manifest': manifest,
                                   'api_url': 'https://attacker.invalid', 'public_url': 'https://attacker.invalid'}
                        # Explicitly reject canonical mode rather than pretending
                        # this physical-runtime fixture implements platform dispatch.
                        reserve_development_invocation(execution.id)
                        rejected = await provider.post(provider_url, headers=provider_headers,
                            json={**payload, 'mode': 'canonical', 'worker_id': str(uuid4())})
                        assert rejected.status_code == 422
                        reserve_development_invocation(execution.id)
                        invalid = await provider.post(provider_url, headers=provider_headers,
                            json={**payload, 'context': {**context, 'engine_token': 'synthetic-invalid-runtime-token'}})
                        invalid_result = invalid.json()
                        assert invalid.status_code == 502 and invalid_result['phase'] == 'module-preflight'
                        assert invalid_result['module_status'] == 401
                        report['unsupported_canonical_mode_status'] = rejected.status_code
                        report['invalid_runtime_credential_module_status'] = invalid_result['module_status']
                        reserve_development_invocation(execution.id)
                    provider_started = time.perf_counter()
                    response = await provider.post(provider_url, headers=provider_headers,
                                                   json={"execution_id": str(execution.id), "context": context,
                                                         "manifest": manifest,
                                                         **({'api_url': 'https://attacker.invalid', 'public_url': 'https://attacker.invalid'}
                                                            if dev_qualification else {})})
                    evidence = response.json()
                    provider_roundtrip_ms = (time.perf_counter() - provider_started) * 1000
                encoded = json.dumps(evidence)
                if any(secret and secret in encoded for secret in [access_token, credentials.refresh_token, integration_secret]):
                    raise RuntimeError("Runtime credential was exposed; evidence not emitted")
                if response.status_code != 200:
                    print(json.dumps({**report, 'provider_status': response.status_code, 'failure': evidence}))
                    raise RuntimeError('Cloud provider rejected qualification')
                result = evidence["result"]
                if result.get("status") != "Success":
                    report["failure"] = evidence
                    print(json.dumps(report))
                    raise RuntimeError("Actual upstream worker did not complete successfully")
                if benchmark is None:
                    assert result["result"]["value"] == 70
                else:
                    samples = result['result']['benchmark']
                    assert len(samples) == 1 and samples[0]['workload'] == benchmark
                    assert samples[0]['correctness'] is True and len(samples[0]['samples_wall_ms']) == 3
                    report['benchmark'] = samples
                    report['fresh_child_provider_roundtrip_ms'] = provider_roundtrip_ms
                    report['benchmark_scope'] = 'three operation samples within one fresh child; not warm-isolate measurements'
                assert result["result"]["parent_secret_present"] is False
                if dev_qualification:
                    expected_public = 'https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev'
                    assert result['execution_context']['public_url'] == expected_public
                    assert result['result']['transport_url'] == 'https://test-api.invalid'
                    report['trusted_public_url_verified'] = expected_public
                    report['virtual_sdk_transport_preserved'] = True
                if fresh_probe:
                    assert all(result['result'][key] is None for key in ('builtins_before', 'module_before', 'environment_before', 'tmp_before'))
                    report['fresh_run_canaries_clear'] = True
                if integration_probe and benchmark is None:
                    assert result['result']['integration_value'] == 42
                    assert result['result']['integration_secret_probe'] == '[REDACTED]'
                    report['integration_read_secret_redaction_peer_denial_verified'] = True
                assert result["metrics"]["code_hash"] == manifest[path]
                if stream_probe:
                    messages = [row['message'] for row in result['logs']]
                    for message in ('stream stdout credential=[REDACTED]', 'stream stderr credential=[REDACTED]'):
                        assert messages.count(message) == 1
                    report['printed_streams_returned_redacted'] = True
                if benchmark is None or benchmark == 'table_update':
                    ids = ['proof'] if benchmark is None else ['benchmark-0', 'benchmark-1', 'benchmark-2']
                    for document_id in ids:
                        readback = await api.get(f"/api/tables/{table}/documents/{document_id}", headers=headers,
                                                 params={"scope": str(organization.id)})
                        assert readback.status_code == 200
                        assert readback.json()["data"]["value"] == 70
                    report['independent_document_values'] = [70] * len(ids)
                    if benchmark is None:
                        report['independent_document_value'] = 70
                report.update({"result": evidence,
                               "cross_tenant_status": denied.status_code,
                               "credential_version": credentials.credential_version})
            # Vary only the fixture run's liveness while its person/roles remain.
            # This is an authorization test input, not canonical terminal reporting.
            from src.models.enums import ExecutionStatus
            async with httpx.AsyncClient(base_url="http://api:8000", timeout=15) as api:
                before = await api.post("/api/sdk/config/get", headers=headers, json={"key": config_key})
                assert before.status_code == 200
                execution.status = ExecutionStatus.SUCCESS
                await world.session.commit()
                if stream_probe:
                    from bifrost._logging import recover_terminal_execution_logs
                    from sqlalchemy import select
                    from src.models.orm.executions import ExecutionLog
                    from src.core.cache import get_redis
                    from src.core.cache.keys import execution_logs_stream_key
                    recovered = await recover_terminal_execution_logs([str(execution.id)], limit=1)
                    rows = (await world.session.scalars(select(ExecutionLog).where(
                        ExecutionLog.execution_id == execution.id))).all()
                    messages = [row.message for row in rows]
                    if any(secret and secret in json.dumps(messages) for secret in (
                        access_token, credentials.refresh_token, integration_secret)):
                        raise RuntimeError('Persisted logs exposed a runtime secret; evidence withheld')
                    for message in ('stream stdout credential=[REDACTED]', 'stream stderr credential=[REDACTED]'):
                        assert messages.count(message) == 1
                    report['persisted_printed_logs'] = [{'level': row.level, 'message': row.message} for row in rows]
                    report['terminal_log_recovery'] = recovered
                    repeated = await recover_terminal_execution_logs([str(execution.id)], limit=1)
                    assert repeated['flushed'] == 0
                    report['repeat_log_recovery'] = repeated
                    async with get_redis() as redis:
                        assert await redis.xlen(execution_logs_stream_key(str(execution.id))) == 0
                    report['runtime_log_stream_drained'] = True
                terminal = await api.post("/api/sdk/config/get", headers=headers, json={"key": config_key})
                assert terminal.status_code == 401
                report["live_then_fixture_terminal_status"] = [before.status_code, terminal.status_code]
        # Deleting the fixture execution removes its live-run authority, regardless
        # of the token expiry. No persistent caller credential is created.
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as api:
            ended = await api.post("http://api:8000/api/sdk/config/get",
                                   headers={"Authorization": f"Bearer {access_token}"},
                                   json={"key": config_key})
            assert ended.status_code == 401
            report["ended_run_status"] = ended.status_code
            if dev_qualification:
                revoked_refresh = await api.post('http://api:8000/auth/refresh', json={'refresh_token': credentials.refresh_token})
                assert revoked_refresh.status_code == 401
                report['ended_refresh_status'] = revoked_refresh.status_code
        print(json.dumps(report))
    finally:
        for name in sources:
            await invalidate_module(name)
            await storage.delete(name)
        await close_db()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
