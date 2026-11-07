"""Qualify explicit custom-receipt awaits in the existing owned guest stack."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'upstream-025f6c0'
PACKET = ROOT / 'inbox/journal-await-v1'
RUN = ROOT / 'runs/journal-await-v1'
PROJECT = 'bifrost-test-5dc8d94e'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Assigned guest required')
    running = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT], text=True)
    if running.strip():
        raise RuntimeError('Owned project must be stopped')
    path = 'api/tests/unit/dev/test_pyodide_attempt_journal.py'
    baseline = subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=CHECKOUT)
    if (CHECKOUT / path).read_bytes() != baseline:
        raise RuntimeError('Unexpected journal-test changes')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    (CHECKOUT / path).write_bytes((PACKET / 'test_pyodide_attempt_journal.py').read_bytes())
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1', 'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', PROJECT, '-f', 'docker-compose.test.yml']
    unit = ['docker', 'run', '--rm', '--network=none', '--cpus=1', '--memory=512m', '--memory-swap=512m',
            '--pids-limit=64', '--volumes-from', 'bifrost-debug-074217d4-api-1:ro',
            '--mount', f'type=bind,src={PACKET},dst=/qualification,readonly', '--workdir', '/qualification',
            '--env', 'PYTHONPATH=/app', '--entrypoint', 'python', 'bifrost-test-api-dev:bifrost-test-7ddfac36',
            '-m', 'unittest', '-v', 'test_attempt_journal_owners']
    report = {'cloud_invocations': 0, 'commands': [], 'overlay_sha256': {
        name: hashlib.sha256((PACKET / name).read_bytes()).hexdigest()
        for name in ['test_pyodide_attempt_journal.py', 'test_attempt_journal_owners.py', 'attempt_journal.py']}}
    started = time.monotonic()
    try:
        commands = [unit, ['./test.sh', 'stack', 'up'], ['./test.sh', 'tests/unit/dev/test_pyodide_attempt_journal.py', '-q'],
                    [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', 'test-runner',
                     'ruff', 'check', 'tests/unit/dev/test_pyodide_attempt_journal.py']]
        for index, command in enumerate(commands):
            with (RUN / f'phase-{index}.log').open('x') as log:
                result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            report['commands'].append({'argv': command, 'exit_code': result.returncode})
            if result.returncode:
                raise RuntimeError('Journal await qualification failed; preserve log')
        junit = Path('/tmp') / ('bifrost-' + PROJECT) / 'test-results.xml'
        (RUN / 'tests.xml').write_bytes(junit.read_bytes())
        xml = ET.parse(junit)
        report['junit'] = {key: len(xml.findall('.//' + tag)) for key, tag in
                           [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
    finally:
        subprocess.run([*compose, '--profile', 'test', '--profile', 'e2e', 'stop'], cwd=CHECKOUT, env=env, check=True, timeout=45)
        report['running_after_cleanup'] = subprocess.check_output(['docker', 'ps', '-q', '--filter',
            'label=com.docker.compose.project=' + PROJECT], text=True).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
