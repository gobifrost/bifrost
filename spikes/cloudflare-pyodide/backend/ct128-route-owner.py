"""Bounded journal ownership regression on the existing isolated CT128 test project."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'result-identity-1017cca'
PACKET = ROOT / 'inbox/route-owner-v1'
RUN = ROOT / 'runs/route-owner-v1'
SOURCE = '1017cca1795540f75b9781d87137822a4a42d685'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=30, **kwargs).strip()


def main():
    if (out(['hostname']) != 'bifrost-pyodide-test-01'
        or out(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE
        or shutil.disk_usage(ROOT).free < 8 * 1024**3):
        raise RuntimeError('Owned guest/source/disk guard failed')
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    if out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]):
        raise RuntimeError('Owned test project still running')
    (RUN / 'home').mkdir()
    files = {'attempt_journal.py': 'spikes/cloudflare-pyodide/backend/attempt_journal.py',
             'test_pyodide_attempt_journal.py': 'api/tests/unit/dev/test_pyodide_attempt_journal.py'}
    hashes = {}
    for name, relative in files.items():
        destination = CHECKOUT / relative
        (RUN / ('previous-' + name)).write_bytes(destination.read_bytes())
        payload = (PACKET / name).read_bytes()
        destination.write_bytes(payload)
        hashes[relative] = hashlib.sha256(payload).hexdigest()
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1',
           'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
    unit = ['timeout', '60', 'docker', 'run', '--rm', '--name', 'pyodide-ddf316c1-route-owner-v1',
            '--network', 'none', '--cpus=1', '--memory=512m', '--memory-swap=512m', '--pids-limit=64',
            '--volumes-from', 'bifrost-debug-074217d4-api-1:ro',
            '--mount', f'type=bind,src={PACKET},dst=/qualification,readonly', '--workdir', '/qualification',
            '--env', 'PYTHONPATH=/app', '--entrypoint', 'python', IMAGE,
            '-m', 'unittest', '-v', 'test_attempt_journal_owners']
    commands = [unit, ['./test.sh', 'stack', 'up'],
                ['./test.sh', 'tests/unit/dev/test_pyodide_attempt_journal.py', '-q'],
                [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', 'test-runner',
                 'ruff', 'check', '/app/pyodide-prototype/attempt_journal.py',
                 'tests/unit/dev/test_pyodide_attempt_journal.py']]
    report = {'source': SOURCE, 'overlay_sha256': hashes, 'project': project, 'commands': [],
              'cloud_invocations': 0, 'qualification_passed': False,
              'unit_fixture_sha256': hashlib.sha256((PACKET / 'test_attempt_journal_owners.py').read_bytes()).hexdigest(),
              'image_id': out(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'])}
    started = time.monotonic()
    try:
        for index, command in enumerate(commands):
            with (RUN / f'after-{index}.log').open('x') as log:
                result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=max(1, 600 - (time.monotonic() - started)))
            report['commands'].append({'argv': command, 'exit_code': result.returncode})
            if result.returncode:
                raise RuntimeError('Focused qualification failed; inspect retained log')
        junit = Path('/tmp') / ('bifrost-' + project) / 'test-results.xml'
        shutil.copyfile(junit, RUN / 'after.xml')
        tree = ET.parse(junit)
        report['junit'] = {key: len(tree.findall('.//' + tag)) for key, tag in
                           [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
        if report['junit']['cases'] < 25 or any(report['junit'][key] for key in ('failures', 'errors', 'skipped')):
            raise RuntimeError('Unexpected JUnit outcome')
        report['qualification_passed'] = True
    finally:
        subprocess.run([*compose, '--profile', 'e2e', '--profile', 'test', 'stop'],
                       cwd=CHECKOUT, env=env, timeout=60, check=True)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter',
                                               'label=com.docker.compose.project=' + project]).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
