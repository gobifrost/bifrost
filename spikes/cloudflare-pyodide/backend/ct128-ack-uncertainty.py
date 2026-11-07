"""Bounded fail-first/after qualification in the existing owned test checkout."""
import argparse
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
PACKET = ROOT / 'inbox/ack-uncertainty-v1'
SOURCE = '1d33150eb376b71f8a81b427920eda1b0447f829'
FILES = ['api/src/worker/remote/remote_pool.py', 'api/tests/unit/dev/test_pyodide_remote_ack_boundary.py',
         'api/tests/unit/dev/test_pyodide_remote_boundary.py']


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=30, **kwargs).strip()


def main(after):
    binding = json.loads((ROOT / 'owner-binding.json').read_text())
    if (out(['hostname']) != 'bifrost-pyodide-test-01' or binding['vmid'] != 128
        or binding['owner_thread_id'] != '6e1d35f9-0847-466a-a570-f72fd16f9ed7'
        or shutil.disk_usage(ROOT).free < 8 * 1024**3):
        raise RuntimeError('Assigned ownership/capacity guard failed')
    if out(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE:
        raise RuntimeError('Test checkout source changed')
    allowed = {'docker-compose.test.yml', *(FILES[1:] if after else [])}
    if set(out(['git', 'diff', '--name-only'], cwd=CHECKOUT).splitlines()) != allowed:
        raise RuntimeError('Unexpected checkout changes; preserve before investigation')
    if hashlib.sha256((CHECKOUT / FILES[0]).read_bytes()).hexdigest() != hashlib.sha256((PACKET / 'remote_pool.before.py').read_bytes()).hexdigest():
        raise RuntimeError('Baseline remote pool differs')
    if hashlib.sha256((CHECKOUT / 'docker-compose.test.yml').read_bytes()).hexdigest() != '33e78480090f97ea3f04be404c5a3d312ea8486ec6d55a60c67361a8ef0bdce9':
        raise RuntimeError('Bounded Compose changed')
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    if out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]):
        raise RuntimeError('Owned test project already running')
    run = ROOT / ('runs/ack-uncertainty-' + ('after' if after else 'before'))
    run.mkdir()
    (run / 'home').mkdir()
    for path in FILES[1:]:
        if after:
            before = json.loads((ROOT / 'runs/ack-uncertainty-before/receipt.json').read_text())
            if hashlib.sha256((CHECKOUT / path).read_bytes()).hexdigest() != before['tested_sha256'][path]:
                raise RuntimeError('Unexpected change to owned fail-first tests')
        shutil.copyfile(PACKET / Path(path).name, CHECKOUT / path)
    if after:
        shutil.copyfile(PACKET / 'remote_pool.after.py', CHECKOUT / FILES[0])
    report = {'source_base': SOURCE, 'project': project, 'cloud_invocations': 0,
              'physical_termination_verified': False, 'phase': 'after' if after else 'before',
              'tested_sha256': {path: hashlib.sha256((CHECKOUT / path).read_bytes()).hexdigest() for path in FILES},
              'commands': []}
    env = {'PATH': os.environ['PATH'], 'HOME': str(run / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1', 'COMPOSE_PARALLEL_LIMIT': '1'}
    selectors = [path.removeprefix('api/') for path in FILES[1:]]
    if after:
        selectors += ['tests/unit/dev/test_thin_remote_pool.py', 'tests/unit/worker/remote/test_credential_protocol.py',
                      'tests/unit/dev/test_pyodide_attempt_journal.py']
    started = time.monotonic()
    try:
        for index, command in enumerate([['./test.sh', 'stack', 'up'], ['./test.sh', *selectors, '-q']]):
            with (run / f'phase-{index}.log').open('x') as log:
                result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            report['commands'].append({'argv': command, 'exit_code': result.returncode})
            if index == 0 and result.returncode:
                raise RuntimeError('Test stack boot failed')
        junit = Path('/tmp') / ('bifrost-' + project) / 'test-results.xml'
        shutil.copyfile(junit, run / 'tests.xml')
        tree = ET.parse(junit)
        report['junit'] = {name: len(tree.findall('.//' + tag)) for name, tag in
                           [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
        if report['junit']['errors'] or report['junit']['skipped'] or report['junit']['cases'] < 10:
            raise RuntimeError('Invalid targeted test receipt')
        if after and (report['commands'][-1]['exit_code'] or report['junit']['failures']):
            raise RuntimeError('ACK uncertainty correction failed')
        if not after and report['junit']['failures'] == 0:
            raise RuntimeError('Fail-first baseline did not reproduce')
    finally:
        subprocess.run(['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml',
                        '--profile', 'test', '--profile', 'e2e', 'stop'], cwd=CHECKOUT, env=env, check=True, timeout=45)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (run / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--after', action='store_true')
    main(parser.parse_args().after)
