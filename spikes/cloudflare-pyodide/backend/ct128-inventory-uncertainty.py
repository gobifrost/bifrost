"""Bounded fail-first native inventory boundary qualification; no cloud work."""
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
CHECKOUT = ROOT / 'upstream-025f6c0'
PACKET = ROOT / 'inbox/inventory-uncertainty-v1'
RUN = ROOT / 'runs/inventory-uncertainty-v1'
PROJECT = 'bifrost-test-5dc8d94e'
SHARED = 'api/src/worker/remote/remote_pool.py'
TEST = 'tests/unit/dev/test_pyodide_inventory_uncertainty.py'


def output(args):
    return subprocess.check_output(args, cwd=CHECKOUT, text=True, timeout=30).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(revision=1):
    global PACKET, RUN
    PACKET = ROOT / f'inbox/inventory-uncertainty-v{revision}'
    RUN = ROOT / f'runs/inventory-uncertainty-v{revision}'
    if (output(['hostname']) != 'bifrost-pyodide-test-01'
        or output(['git', 'rev-parse', 'HEAD']) != '1d33150eb376b71f8a81b427920eda1b0447f829'
        or shutil.disk_usage(ROOT).free < 8 * 1024**3
        or output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT])):
        raise RuntimeError('Assigned guest/source/capacity/stopped-project guard failed')
    baseline = {1: 'abb9a48eeaa2475f2cf42367073c64e8b50f24b2d1f7479493d5b0c433ba429a',
                2: '3f02b968ecdd33f85e09561f3b77bccbdf6dc9c631d96fecb9aa0b95a6ddabdf',
                3: '17d9eea075c3d7da5ba10031721f116d3c5a049a1563fc430944d51e698e373e'}[revision]
    if digest(CHECKOUT / SHARED) != baseline:
        raise RuntimeError('Reconcile changed shared pool before overlay')
    if digest(CHECKOUT / 'api/src/worker/remote/protocol.py') != '91db42936f3235599243af681d24886fbb79180a7020633dfafe35887b3956d5':
        raise RuntimeError('Runtime protocol overlay changed')
    if digest(CHECKOUT / 'docker-compose.test.yml') != '33e78480090f97ea3f04be404c5a3d312ea8486ec6d55a60c67361a8ef0bdce9':
        raise RuntimeError('Resource-bounded Compose changed')
    if output(['docker', 'image', 'inspect', 'bifrost-test-api-dev:bifrost-test-7ddfac36', '--format', '{{.Id}}']) != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Pinned dependency image changed')
    destination = CHECKOUT / 'api' / TEST
    previous_test = {2: 'aea74f405d93e097f6b3369b951657156ec776d9019c0bdf396527b461614058',
                     3: 'e83f69d9a86f9c83ce396025111641158ef0befb736cf01a3f77b6c857af7137'}
    if destination.exists() and (revision == 1 or digest(destination) != previous_test[revision]):
        raise RuntimeError('Reconcile existing regression file before overlay')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    (RUN / 'before-remote_pool.py').write_bytes((CHECKOUT / SHARED).read_bytes())
    destination.write_bytes((PACKET / destination.name).read_bytes())
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1', 'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', PROJECT, '-f', 'docker-compose.test.yml']
    runner = [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', 'test-runner']
    report = {'api_source': '1d33150eb376b71f8a81b427920eda1b0447f829', 'project': PROJECT,
              'cloud_invocations': 0, 'execution_admission_qualified': False, 'physical_termination_verified': False,
              'before_pool_sha256': digest(RUN / 'before-remote_pool.py'), 'qualification_passed': False,
              'packet_sha256': {p.name: digest(p) for p in PACKET.iterdir() if p.is_file()}, 'commands': []}
    started = time.monotonic()

    def run(command, phase, expected=0):
        with (RUN / (phase + '.private.log')).open('x') as log:
            result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=max(1, 400 - (time.monotonic() - started)))
        report['commands'].append({'argv': command, 'exit_code': result.returncode})
        if result.returncode != expected:
            raise RuntimeError('Qualification phase failed: ' + phase)

    def junit(phase):
        data = (Path('/tmp') / ('bifrost-' + PROJECT) / 'test-results.xml').read_bytes()
        (RUN / (phase + '.xml')).write_bytes(data)
        tree = ET.fromstring(data)
        return {name: len(tree.findall('.//' + tag)) for name, tag in
                [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}

    try:
        run(['./test.sh', 'stack', 'up'], 'boot')
        run(['./test.sh', TEST, '-q'], 'before', 1)
        report['before'] = junit('before')
        expected_before = {'cases': revision + 2, 'failures': 2 if revision == 1 else 1,
                           'errors': 0, 'skipped': 0}
        if report['before'] != expected_before:
            raise RuntimeError('Expected two opaque defects and unchanged Linux control were not reproduced')
        (CHECKOUT / SHARED).write_bytes((PACKET / 'remote_pool.py').read_bytes())
        run(['./test.sh', TEST, 'tests/unit/dev/test_pyodide_physical_handle.py',
             'tests/unit/dev/test_pyodide_remote_ack_boundary.py', 'tests/unit/dev/test_thin_remote_pool.py',
             'tests/unit/worker/remote/test_pool_inventory.py', '-q'], 'after')
        report['after'] = junit('after')
        if any(report['after'][key] for key in ('failures', 'errors', 'skipped')):
            raise RuntimeError('Selected native contracts failed')
        run([*runner, 'ruff', 'check', 'src/worker/remote/remote_pool.py', TEST], 'ruff')
        run([*runner, 'pyright', 'src/worker/remote/remote_pool.py', TEST], 'pyright')
        report['qualification_passed'] = True
    finally:
        subprocess.run([*compose, '--profile', 'test', '--profile', 'e2e', 'stop'],
                       cwd=CHECKOUT, env=env, timeout=60, check=True)
        report['running_after_cleanup'] = output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT]).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        report['after_pool_sha256'] = digest(CHECKOUT / SHARED)
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision', type=int, choices=(1, 2, 3), default=1)
    main(parser.parse_args().revision)
