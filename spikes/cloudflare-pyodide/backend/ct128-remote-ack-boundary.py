"""Five focused ACK/callback characterization tests; no provider execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = '45bcb38dd6412d64aa1d308479178904cd2ceab6'
CHECKOUT = ROOT / ('ack-boundary-' + SOURCE[:12])
RUN = ROOT / 'runs/remote-ack-boundary-v1'
PACKET = ROOT / 'inbox/remote-ack-boundary-v1'
TEST = 'api/tests/unit/dev/test_pyodide_remote_ack_boundary.py'
DIGEST = '07154cf159a1d57e8abe5e0bfe5af4d82e7235d7924720e1a2dd20d53e57c87a'


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main():
    binding = json.loads((ROOT / 'owner-binding.json').read_text())
    if binding['owner_thread_id'] != '6e1d35f9-0847-466a-a570-f72fd16f9ed7' or binding['vmid'] != 128:
        raise RuntimeError('Wrong owner binding')
    if out(['hostname']) != 'bifrost-pyodide-test-01' or out(['docker', 'ps', '-q']):
        raise RuntimeError('Wrong guest or reconcile running containers')
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError('Insufficient scratch')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    report = {'source': SOURCE, 'uncommitted_fixture': {TEST: DIGEST}, 'commands': [],
              'cloud_invocations': 0, 'physical_invocations': 0, 'qualification_passed': False,
              'scope': 'unit characterization with transport/Redis doubles; not SDK compatibility',
              'started_at_epoch': time.time()}
    started = time.monotonic()
    project = None
    try:
        repo = ROOT / 'sources/bifrost-upstream.git'
        out(['git', '--git-dir', str(repo), 'fetch', 'https://github.com/MTG-Thomas/bifrost-upstream.git', SOURCE])
        if CHECKOUT.exists():
            raise RuntimeError('Existing source reservation; reconcile instead of replay')
        out(['git', '--git-dir', str(repo), 'worktree', 'add', '--detach', str(CHECKOUT), SOURCE])
        if out(['git', 'status', '--porcelain'], cwd=CHECKOUT):
            raise RuntimeError('New checkout not clean')
        data = (PACKET / Path(TEST).name).read_bytes()
        if hashlib.sha256(data).hexdigest() != DIGEST:
            raise RuntimeError('Fixture changed')
        with (CHECKOUT / TEST).open('xb') as stream:
            stream.write(data)
        report['source_status'] = out(['git', 'status', '--porcelain'], cwd=CHECKOUT)
        report['shared_source_sha256'] = {name:hashlib.sha256((CHECKOUT / name).read_bytes()).hexdigest() for name in
            ['api/src/worker/remote/remote_pool.py', 'api/src/worker/remote/launcher.py',
             'api/src/worker/remote/protocol.py', 'api/src/worker/remote/host.py']}
        project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
        report['project'] = project
        env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
               'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1'}
        compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
        commands = [['./test.sh', 'stack', 'up'], [*compose, 'stop', 'worker', 'scheduler'],
            ['./test.sh', TEST.removeprefix('api/'),
             'tests/unit/dev/test_thin_remote_pool.py::test_callback_failure_retains_route_until_durable_result_replay', '-q'],
            [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', 'test-runner',
             'ruff', 'check', TEST.removeprefix('api/')]]
        for index, args in enumerate(commands):
            entry = {'argv': args}
            report['commands'].append(entry)
            with (RUN / f'phase-{index}.log').open('x') as log:
                process = subprocess.Popen(args, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                while process.poll() is None:
                    if time.monotonic() - started > 900 or shutil.disk_usage(ROOT).used > 24 * 1024**3:
                        os.killpg(process.pid, signal.SIGTERM)
                        raise RuntimeError('Wall/disk guard')
                    time.sleep(1)
                entry['exit_code'] = process.returncode
            if process.returncode:
                raise RuntimeError('Focused command failed')
        junit = Path('/tmp') / ('bifrost-' + project) / 'test-results.xml'
        shutil.copyfile(junit, RUN / 'test-results.xml')
        tree = ET.parse(junit)
        report['junit'] = {key:len(tree.findall('.//' + value)) for key,value in
            [('cases','testcase'),('failures','failure'),('errors','error'),('skipped','skipped')]}
        if report['junit'] != {'cases':5,'failures':0,'errors':0,'skipped':0}:
            raise RuntimeError('Unexpected test result')
        report['qualification_passed'] = True
    finally:
        if project:
            ids = out(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
            if ids:
                subprocess.run(['docker', 'stop', '-t', '10', *ids], check=True, capture_output=True)
            report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
        report['volumes_deleted'] = False
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
