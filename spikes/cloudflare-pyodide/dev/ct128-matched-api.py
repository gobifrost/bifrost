"""Bounded matched canonical API boot; no cloud or existing dev mutations."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'upstream-025f6c0'
RUN = ROOT / 'runs/matched-api-v1'
PROJECT = 'bifrost-test-5dc8d94e'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def command(args, *, env=None, timeout=30, data=None):
    return subprocess.check_output(args, cwd=CHECKOUT, env=env, input=data, timeout=timeout)


def main():
    if command(['hostname']).decode().strip() != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError('Guest/disk guard failed')
    source = command(['git', 'rev-parse', 'HEAD']).decode().strip()
    if source != '1d33150eb376b71f8a81b427920eda1b0447f829':
        raise RuntimeError('Canonical source changed')
    if hashlib.sha256((CHECKOUT / 'docker-compose.test.yml').read_bytes()).hexdigest() != '33e78480090f97ea3f04be404c5a3d312ea8486ec6d55a60c67361a8ef0bdce9':
        raise RuntimeError('Bounded Compose changed')
    if command(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']).decode().strip() != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Dependency image changed')
    if command(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT]).strip():
        raise RuntimeError('Reconcile existing owned test processes')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', PROJECT, '-f', 'docker-compose.test.yml']
    report = {'source': source, 'project': PROJECT, 'cloud_invocations': 0, 'passed': False}
    started = time.monotonic()
    try:
        # Repository lifecycle owns migrations, fixtures and readiness.
        log = command(['./test.sh', 'stack', 'up'], env=env, timeout=180)
        (RUN / 'startup.log').write_bytes(log)
        command([*compose, 'stop', 'worker', 'scheduler'], env=env, timeout=30)
        probe = '''import asyncio,json,urllib.request,urllib.error
from sqlalchemy import text
from src.core.database import get_db_context,close_db
async def main():
    try:
        async with get_db_context() as db:
            revision=await db.scalar(text('SELECT version_num FROM alembic_version'))
        statuses=[]
        for path in ['/health/ready','/api/workflows']:
            try:
                with urllib.request.urlopen('http://api:8000'+path,timeout=10) as response:
                    statuses.append(response.status)
            except urllib.error.HTTPError as exc:
                statuses.append(exc.code)
        if revision!='20261101_table_name_unique' or statuses!=[200,401]:
            raise RuntimeError('Matched API/schema/auth preflight failed')
        print('MATCHED_API_RECEIPT='+json.dumps({'revision':revision,'statuses':statuses}))
    finally:
        await close_db()
asyncio.run(main())
'''
        output = command([*compose, 'exec', '-T', 'api', 'python', '-'], env=env, data=probe.encode(), timeout=30).decode()
        lines = [line for line in output.splitlines() if line.startswith('MATCHED_API_RECEIPT=')]
        if len(lines) != 1:
            raise RuntimeError('Missing API receipt')
        report.update(json.loads(lines[0].split('=', 1)[1]))
        report['passed'] = True
    finally:
        command([*compose, 'stop'], env=env, timeout=45)
        report['owned_running_after_cleanup'] = command(['docker', 'ps', '-q', '--filter',
            'label=com.docker.compose.project=' + PROJECT]).decode().strip()
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
