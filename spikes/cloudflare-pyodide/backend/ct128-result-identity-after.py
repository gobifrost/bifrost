"""Resume the owned identity-regression stack with the bounded metadata fix."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'result-identity-1017cca'
PACKET = ROOT / 'inbox/result-identity-v1'
RUN = ROOT / 'runs/result-identity-after-v1'
SOURCE = '1017cca1795540f75b9781d87137822a4a42d685'
FILES = {'process_pool.py': 'api/src/services/execution/process_pool.py',
         'test_result_transport_identity.py': 'api/tests/unit/execution/test_result_transport_identity.py',
         'test_pyodide_attempt_journal.py': 'api/tests/unit/dev/test_pyodide_attempt_journal.py'}


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main(canonical=False):
    run = RUN.with_name('result-identity-canonical-v1') if canonical else RUN
    if out(['hostname']) != 'bifrost-pyodide-test-01' or out(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE:
        raise RuntimeError('Owned guest/source preflight failed')
    run.mkdir()
    (run / 'home').mkdir()
    baseline = out(['git', 'show', SOURCE + ':api/src/services/execution/process_pool.py'], cwd=CHECKOUT)
    expected = (PACKET / 'process_pool.py').read_text().strip() if canonical else baseline
    if (CHECKOUT / FILES['process_pool.py']).read_text().strip() != expected:
        raise RuntimeError('Unexpected source edit; reconcile before applying')
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    if out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]):
        raise RuntimeError('Owned test process still running; reconcile before resuming')
    hashes = {}
    for name, destination in FILES.items():
        payload = (PACKET / name).read_bytes()
        (CHECKOUT / destination).write_bytes(payload)
        hashes[destination] = hashlib.sha256(payload).hexdigest()
    env = {'PATH': os.environ['PATH'], 'HOME': str(run / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1',
           'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
    selectors = ['tests/unit/execution/test_result_transport_identity.py',
                 'tests/unit/execution/test_process_pool.py::TestProcessPoolManagerResultHandling',
                 'tests/unit/execution/test_process_pool.py::TestResultTransportMetadata',
                 'tests/unit/dev/test_thin_remote_pool.py', 'tests/unit/dev/test_pyodide_attempt_journal.py']
    if canonical:
        selectors = ['tests/unit/dev/test_pyodide_attempt_journal.py::test_remote_result_cannot_complete_another_run_claimed_by_the_same_worker']
    commands = [['./test.sh', 'stack', 'up'], ['./test.sh', *selectors, '-q'],
                [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', 'test-runner', 'ruff', 'check',
                 'src/services/execution/process_pool.py', 'tests/unit/execution/test_result_transport_identity.py',
                 'tests/unit/dev/test_pyodide_attempt_journal.py'],
                [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', 'test-runner', 'pyright',
                 'src/services/execution/process_pool.py', 'tests/unit/execution/test_result_transport_identity.py',
                 'tests/unit/dev/test_pyodide_attempt_journal.py']]
    report = {'source': SOURCE, 'source_overlay_sha256': hashes, 'project': project,
              'cloud_invocations': 0, 'commands': [], 'qualification_passed': False}
    started = time.monotonic()
    try:
        for index, command in enumerate(commands):
            with (run / f'phase-{index}.log').open('x') as log:
                result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=max(1, 600 - (time.monotonic() - started)))
            report['commands'].append({'argv': command, 'exit_code': result.returncode})
            if result.returncode:
                raise RuntimeError('Focused qualification failed; inspect preserved phase log')
        junit = Path('/tmp') / ('bifrost-' + project) / 'test-results.xml'
        shutil.copyfile(junit, run / 'after.xml')
        tree = ET.parse(junit)
        report['junit'] = {key: len(tree.findall('.//' + tag)) for key, tag in
                           [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
        if report['junit']['cases'] < (1 if canonical else 9) or any(report['junit'][key] for key in ('failures', 'errors', 'skipped')):
            raise RuntimeError('Unexpected JUnit outcome')
        report['qualification_passed'] = True
    finally:
        subprocess.run([*compose, '--profile', 'e2e', '--profile', 'test', 'stop'],
                       cwd=CHECKOUT, env=env, timeout=60, check=True)
        (run / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--canonical', action='store_true')
    main(canonical=parser.parse_args().canonical)
