"""Bounded native opaque-handle adapter qualification; no provider admission."""
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
PACKET = ROOT / 'inbox/physical-handle-v1'
RUN = ROOT / 'runs/physical-handle-v1'
PROJECT = 'bifrost-test-5dc8d94e'


def output(command):
    return subprocess.check_output(command, cwd=CHECKOUT, text=True, timeout=30).strip()


def main():
    if (output(['hostname']) != 'bifrost-pyodide-test-01'
        or output(['git', 'rev-parse', 'HEAD']) != '1d33150eb376b71f8a81b427920eda1b0447f829'
        or shutil.disk_usage(ROOT).free < 8 * 1024**3
        or output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT])):
        raise RuntimeError('Assigned guest/source/disk/stopped-project guard failed')
    shared = {'api/src/worker/remote/protocol.py': '7830129a7f152790de94ae425fb53b7dc603ecf1',
              'api/src/worker/remote/remote_pool.py': '70b36293d8da90f7486fb5eaba1ae577c2228170'}
    for path, blob in shared.items():
        if output(['git', 'hash-object', path]) != blob:
            raise RuntimeError('Unexpected source overlay: ' + path)
    if hashlib.sha256((CHECKOUT / 'docker-compose.test.yml').read_bytes()).hexdigest() != '33e78480090f97ea3f04be404c5a3d312ea8486ec6d55a60c67361a8ef0bdce9':
        raise RuntimeError('Resource-bounded Compose changed')
    if output(['docker', 'image', 'inspect', 'bifrost-test-api-dev:bifrost-test-7ddfac36', '--format', '{{.Id}}']) != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Pinned dependency image changed')
    test_path = CHECKOUT / 'api/tests/unit/dev/test_pyodide_physical_handle.py'
    if test_path.exists():
        raise RuntimeError('Reconcile existing test before overlay')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    test_path.write_bytes((PACKET / test_path.name).read_bytes())
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1', 'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', PROJECT, '-f', 'docker-compose.test.yml']
    runner = [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', 'test-runner']
    report = {'api_source': '1d33150eb376b71f8a81b427920eda1b0447f829',
              'source_base': 'eca8fdc99af47916a6549ee10888e967a0109d00', 'cloud_invocations': 0,
              'execution_admission_qualified': False, 'physical_termination_verified': False,
              'baseline_blobs': shared, 'commands': [], 'packet_sha256': {
                  p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in PACKET.iterdir()
                  if p.is_file() and p.suffix != '.tar'}}
    started = time.monotonic()

    def run(command, phase, expected=0):
        with (RUN / (phase + '.private.log')).open('x') as log:
            result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=max(1, 400 - (time.monotonic() - started)))
        report['commands'].append({'argv': command, 'exit_code': result.returncode})
        if result.returncode != expected:
            raise RuntimeError('Physical-handle qualification failed: ' + phase)

    def junit(phase):
        path = Path('/tmp') / ('bifrost-' + PROJECT) / 'test-results.xml'
        (RUN / (phase + '.xml')).write_bytes(path.read_bytes())
        tree = ET.parse(path)
        return {k: len(tree.findall('.//' + tag)) for k, tag in
                [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}

    try:
        run(['./test.sh', 'stack', 'up'], 'boot')
        run(['./test.sh', 'tests/unit/dev/test_pyodide_physical_handle.py::test_opaque_ack_is_counted_without_inventing_a_pid_or_measurements', '-q'], 'before', 1)
        report['before'] = junit('before')
        if report['before'] != {'cases': 1, 'failures': 1, 'errors': 0, 'skipped': 0}:
            raise RuntimeError('Expected absent-profile feature was not reproduced')
        for path in shared:
            (CHECKOUT / path).write_bytes((PACKET / Path(path).name).read_bytes())
        run(['./test.sh', 'tests/unit/dev/test_pyodide_physical_handle.py',
             'tests/unit/dev/test_pyodide_remote_ack_boundary.py', 'tests/unit/dev/test_thin_remote_pool.py',
             'tests/unit/worker/remote/test_pool_inventory.py', 'tests/unit/worker/remote/test_protocol.py',
             'tests/unit/worker/remote/test_runtime_profile.py', '-q'], 'after')
        report['after'] = junit('after')
        if any(report['after'][key] for key in ('failures', 'errors', 'skipped')):
            raise RuntimeError('Selected native contracts did not all pass')
        run([*runner, 'ruff', 'check', 'src/worker/remote/protocol.py', 'src/worker/remote/remote_pool.py',
             'tests/unit/dev/test_pyodide_physical_handle.py'], 'ruff')
        run([*runner, 'pyright', 'src/worker/remote/protocol.py', 'src/worker/remote/remote_pool.py'], 'pyright')
        report['qualification_passed'] = True
    finally:
        subprocess.run([*compose, '--profile', 'test', '--profile', 'e2e', 'stop'],
                       cwd=CHECKOUT, env=env, check=True, timeout=60)
        report['running_after_cleanup'] = output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT]).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
