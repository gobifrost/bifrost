"""Opt-in fixed cloud CPU diagnostic; synthetic run identity, no job dispatch."""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx

PRIVATE = Path('/qualification/private')
URL = 'https://bifrost-pyodide-spike-ddf316c1.midtown-technology-group.workers.dev'
CASES = ('completion', 'code_limit', 'entrypoint_limit')


def reserve(profile, case):
    if profile['expires_at_ms'] <= time.time() * 1000:
        raise RuntimeError('Probe window expired')
    with (PRIVATE / 'invocations.jsonl').open('a+') as ledger:
        fcntl.flock(ledger, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger.seek(0)
        entries = [json.loads(line) for line in ledger if line.strip()]
        if len(entries) >= 5 or any(entry['case'] == case for entry in entries):
            raise RuntimeError('Probe reservation exhausted or already attempted')
        ledger.write(json.dumps({'case': case, 'reservation': len(entries) + 1}) + '\n')
        ledger.flush()
        os.fsync(ledger.fileno())


async def main():
    database = urlsplit(os.environ.get('BIFROST_DATABASE_URL', ''))
    profile = json.loads((PRIVATE / 'profile.json').read_text())
    if (os.environ.get('BIFROST_ENVIRONMENT') != 'development' or database.hostname != 'pgbouncer'
        or database.path != '/bifrost' or profile.get('deployment') != 'pyodide-ddf316c1-cpu-placement-v1'
        or profile.get('maximum_invocations') != 5 or profile.get('maximum_fresh_children') != 3
        or profile.get('incremental_cost_cap_usd') != 0.10
        or profile['expires_at_ms'] <= time.time() * 1000):
        raise RuntimeError('Owned bounded development qualification required')
    from src.core.database import close_db, get_engine, init_db
    from src.core.cache import get_redis
    from src.core.cache.keys import execution_logs_stream_key
    from src.services.authorization.tokens import RuntimeSubject, issue_run_credentials
    from tests.unit.authorization.committed_runs import committed_world

    await init_db()
    report = {'runtime': 'deployed-cloudflare', 'canonical_dispatch_verified': False,
              'physical_termination_verified': False, 'cases': [], 'cleanup': {}}
    secrets = [profile['DEV_AUTH']]
    runs = []
    try:
        async with committed_world(get_engine()) as world:
            headers = {'X-Dev-Auth': profile['DEV_AUTH'], 'User-Agent': 'bifrost-pyodide-dev-qualification/1.0'}
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as provider, httpx.AsyncClient(
                base_url='http://api:8000', timeout=15, follow_redirects=False
            ) as api:
                reserve(profile, 'unsupported')
                rejected = await provider.post(URL, headers=headers, json={'mode': 'canonical'})
                if rejected.status_code != 422:
                    raise RuntimeError('Unsupported dispatch was not rejected')
                report['unsupported_dispatch_status'] = rejected.status_code
                reserve(profile, 'invalid_identity')
                denied = await provider.post(URL, headers=headers, json={'case_id': 'completion', 'run_token': 'synthetic-invalid-runtime-token'})
                if denied.status_code != 502 or denied.json() != {'phase': 'observer-auth', 'observer_status': 401}:
                    raise RuntimeError('Invalid run identity was not rejected before launch')
                report['invalid_identity'] = denied.json()
                try:
                    for case in CASES:
                        organization = await world.organization()
                        user = await world.person(organization)
                        workflow = await world.workflow(organization)
                        execution = await world.execution(run_user=user, workflow=workflow, organization=organization, credential_version=2)
                        await world.session.commit()
                        credentials = await issue_run_credentials(RuntimeSubject('workflow_execution', execution.id), protocol=2,
                                                                  legacy_lifetime_seconds=300)
                        secrets.extend([credentials.access_token, credentials.refresh_token])
                        runs.append((execution.id, credentials))
                        auth = {'Authorization': 'Bearer ' + credentials.access_token}
                        control = await api.post('/api/sdk/runtime/logs', headers=auth,
                                                 json={'level': 'INFO', 'message': 'cpu-placement:controller-control'})
                        if control.status_code != 200:
                            raise RuntimeError('Internal runtime log control failed')
                        reserve(profile, case)
                        started = time.perf_counter()
                        response = await provider.post(URL, headers=headers, json={'case_id': case, 'run_token': credentials.access_token})
                        outcome = response.json()
                        roundtrip = (time.perf_counter() - started) * 1000
                        # Keep authority live throughout the independent late-effect observation.
                        await asyncio.sleep(1)
                        logs = await api.get(f'/api/sdk/runtime/logs/{execution.id}', headers=auth)
                        if logs.status_code != 200:
                            raise RuntimeError('Independent runtime log read failed')
                        messages = [row['message'] for row in logs.json()]
                        record = {'case_id': case, 'http_status': response.status_code, 'outcome': outcome,
                                  'native_roundtrip_ms': roundtrip, 'independent_messages': messages,
                                  'execution_id': str(execution.id)}
                        if any(secret in json.dumps(record) for secret in secrets if secret):
                            raise RuntimeError('Credential exposure; diagnostic evidence withheld')
                        report['cases'].append(record)
                        if case == 'completion' and (response.status_code != 200 or
                            f'cpu-placement:{case}:after' not in messages):
                            raise RuntimeError('Deployed independent effect control failed')
                    if len(runs) > 1:
                        peer = await api.get(f'/api/sdk/runtime/logs/{runs[0][0]}',
                                            headers={'Authorization': 'Bearer ' + runs[1][1].access_token})
                        report['cross_tenant_log_read_status'] = peer.status_code
                        if peer.status_code != 403:
                            raise RuntimeError('Cross-attempt log isolation failed')
                    report['completed'] = True
                finally:
                    async with get_redis() as redis:
                        for execution_id, _ in runs:
                            await redis.delete(execution_logs_stream_key(str(execution_id)))
                    report['cleanup']['owned_log_streams_deleted'] = len(runs)
        async with httpx.AsyncClient(base_url='http://api:8000', timeout=15, follow_redirects=False) as api:
            report['cleanup']['ended_access_statuses'] = []
            report['cleanup']['ended_refresh_statuses'] = []
            for _, credentials in runs:
                ended = await api.post('/api/sdk/runtime/logs', headers={'Authorization': 'Bearer ' + credentials.access_token},
                                       json={'level': 'INFO', 'message': 'cpu-placement:ended'})
                refresh = await api.post('/auth/refresh', json={'refresh_token': credentials.refresh_token})
                report['cleanup']['ended_access_statuses'].append(ended.status_code)
                report['cleanup']['ended_refresh_statuses'].append(refresh.status_code)
                if ended.status_code != 401 or refresh.status_code != 401:
                    raise RuntimeError('Synthetic run authority not retired')
    finally:
        await close_db()
        encoded = json.dumps(report, indent=2)
        if any(secret in encoded for secret in secrets if secret):
            raise RuntimeError('Credential exposure; diagnostic evidence withheld')
        with (PRIVATE / 'report.json').open('x') as output:
            output.write(encoded + '\n')
        print(json.dumps({'completed': report.get('completed', False), 'cases': len(report['cases']),
                          'physical_termination_verified': False}))


if __name__ == '__main__':
    asyncio.run(main())
