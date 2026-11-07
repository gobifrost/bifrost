"""Owned guest contract tests; no cloud or physical-termination claim."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'upstream-025f6c0'
PACKET = ROOT / 'inbox/retained-contract-v1'
RUN = ROOT / 'runs/retained-contract-v1'
PROJECT = 'bifrost-test-5dc8d94e'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def output(args):
    return subprocess.check_output(args, cwd=CHECKOUT, text=True, timeout=20).strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    if (output(['hostname']) != 'bifrost-pyodide-test-01'
        or output(['git', 'rev-parse', 'HEAD']) != '1d33150eb376b71f8a81b427920eda1b0447f829'
        or shutil.disk_usage(ROOT).free < 8 * 1024**3
        or output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT])
        or output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']) !=
        'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b'
        or digest((CHECKOUT / 'docker-compose.test.yml').read_bytes()) !=
        '33e78480090f97ea3f04be404c5a3d312ea8486ec6d55a60c67361a8ef0bdce9'):
        raise RuntimeError('Guest/source/capacity/project/image/resource guard failed')
    target = CHECKOUT / 'spikes/cloudflare-pyodide/backend/attempt_journal.py'
    before = target.read_bytes()
    if digest(before) != 'f8c05fc45b351e25d87f00f6a2a2cd536244400c92cd602c9f94f1bac3fbbf9c':
        raise RuntimeError('Unexpected prototype source; reconcile before overlay')
    test = CHECKOUT / 'api/tests/unit/dev/test_pyodide_attempt_journal.py'
    if digest(test.read_bytes()) != '371f15e138ce618104831f48cc4b9f3da6a66e63a12bd217b65a2c025b3d7039':
        raise RuntimeError('Pinned PostgreSQL test source changed')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    (RUN / 'prototype-before.txt').write_bytes(before)
    candidate = (PACKET / 'attempt_journal.py').read_bytes()
    compose = ['docker', 'compose', '-p', PROJECT, '-f', 'docker-compose.test.yml']
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1', 'COMPOSE_PARALLEL_LIMIT': '1'}
    docker = ['docker', 'run', '--rm', '--network=none', '--cpus=.5', '--memory=1g', '--memory-swap=1g',
              '--pids-limit=64', '-v', f'{PACKET}:/qualification:ro', '-v', f'{CHECKOUT}/api:/app:ro',
              '-w', '/qualification', '-e', 'PYTHONPATH=/app:/qualification', '-e', 'BIFROST_ENVIRONMENT=testing',
              '-e', 'BIFROST_SECRET_KEY=synthetic-unit-fixture-not-an-authenticator',
              '-e', 'NODE_OPTIONS=--max-old-space-size=768', IMAGE]
    commands = [docker + ['python', '-m', 'unittest', '-v', 'test_private_http_pool', 'test_attempt_delivery',
                          'test_workflow_request_mux', 'test_http_invocation', 'test_workspace_prepare',
                          'test_attempt_deadline', 'test_attempt_journal_owners'],
                docker + ['sh', '-c', 'ruff check --no-cache attempt_journal.py private_http_pool.py workflow_request_mux.py test_private_http_pool.py ct128-retained-contract.py && pyright --project pyright-guest-fixtures.json attempt_journal.py private_http_pool.py workflow_request_mux.py test_private_http_pool.py ct128-retained-contract.py'],
                ['./test.sh', 'stack', 'up'], ['./test.sh', 'tests/unit/dev/test_pyodide_attempt_journal.py', '-q']]
    report = {'commands': [], 'cloud_invocations': 0, 'physical_termination_verified': False,
              'qualification_passed': False, 'before_sha256': digest(before),
              'packet_sha256': {p.name: digest(p.read_bytes()) for p in PACKET.glob('*.py')},
              'postgres_test_sha256': digest(test.read_bytes())}
    started = time.monotonic()
    target.write_bytes(candidate)
    try:
        for index, command in enumerate(commands):
            with (RUN / f'phase-{index}.log').open('x') as log:
                result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log,
                    stderr=subprocess.STDOUT, timeout=max(1, 240 - (time.monotonic() - started)))
            report['commands'].append({'argv': command, 'exit_code': result.returncode})
            if result.returncode:
                raise RuntimeError('Retained contract qualification failed; preserve logs')
        junit = Path('/tmp') / ('bifrost-' + PROJECT) / 'test-results.xml'
        (RUN / 'tests.xml').write_bytes(junit.read_bytes())
        xml = ET.parse(junit)
        report['junit'] = {key: len(xml.findall('.//' + tag)) for key, tag in
            [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
        report['qualification_passed'] = True
    finally:
        subprocess.run([*compose, '--profile', 'test', '--profile', 'e2e', 'stop'],
                       cwd=CHECKOUT, env=env, check=True, timeout=45)
        report['running_after_cleanup'] = output(['docker', 'ps', '-q', '--filter',
                                                 'label=com.docker.compose.project=' + PROJECT]).splitlines()
        if target.read_bytes() == candidate:
            target.write_bytes(before)
        report['prototype_restored'] = target.read_bytes() == before
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
