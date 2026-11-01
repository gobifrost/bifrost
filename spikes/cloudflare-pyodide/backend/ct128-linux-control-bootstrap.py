"""One bounded Linux signed-control/template prerequisite in the existing guest."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = 'f417d03f5c8d5e3a182e68f69e2bf601a3b60125'
CHECKOUT = ROOT / SOURCE
RUN = ROOT / 'runs/linux-control-bootstrap-v1'
PACKET = ROOT / 'inbox/linux-control-bootstrap-v1'


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main():
    if out(['hostname']) != 'bifrost-pyodide-test-01' or out(['docker', 'ps', '-q']):
        raise RuntimeError('Wrong guest or active containers')
    if shutil.disk_usage(ROOT).used > 24 * 1024**3:
        raise RuntimeError('Disk guard')
    if out(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE or out(['git', 'status', '--porcelain'], cwd=CHECKOUT):
        raise RuntimeError('Source identity or cleanliness changed')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    report = {'source': SOURCE, 'scope': 'Linux signed control and native template startup only',
              'cloud_invocations': 0, 'workflow_invocations': 0, 'qualification_passed': False,
              'started_at_epoch': time.time(), 'commands': []}
    started = time.monotonic()
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    report['project'] = project
    fixture = PACKET / 'linux-control-bootstrap.py'
    report['fixture_sha256'] = hashlib.sha256(fixture.read_bytes()).hexdigest()
    # Bound this test process inside the existing 2 CPU / 4 GiB guest.
    limits = RUN / 'limits.yml'
    limits.write_text('services:\n  test-runner:\n    cpus: 1\n    mem_limit: 2g\n    memswap_limit: 2g\n    pids_limit: 128\n')
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36'}
    compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
    commands = [['./test.sh', 'stack', 'up'], [*compose, 'stop', 'worker', 'scheduler'],
        [*compose, '-f', str(limits), '--profile', 'test', 'run', '--rm', '--no-deps',
         '--name', project + '-linux-control-bootstrap', '--volume', str(PACKET) + ':/qualification:ro',
         'test-runner', 'python', '/qualification/linux-control-bootstrap.py']]
    try:
        for index, args in enumerate(commands):
            entry = {'argv': args}
            report['commands'].append(entry)
            with (RUN / f'phase-{index}.log').open('x') as log:
                process = subprocess.Popen(args, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                while process.poll() is None:
                    if time.monotonic() - started > 240 or shutil.disk_usage(ROOT).used > 24 * 1024**3:
                        os.killpg(process.pid, signal.SIGTERM)
                        raise RuntimeError('Wall/disk guard')
                    time.sleep(1)
                entry['exit_code'] = process.returncode
            if process.returncode:
                raise RuntimeError('Bootstrap command failed')
        reports = [json.loads(line) for line in (RUN / 'phase-2.log').read_text().splitlines()
                   if line.startswith('{"runtime": "actual-linux-launcher-bootstrap"')]
        observed = reports[-1]
        for key in ['qualification_passed','signed_owner_and_unadmitted_bootstrap_verified',
                    'native_templates_stopped','fixture_worker_cleanup_verified']:
            if observed.get(key) is not True:
                raise RuntimeError('Bootstrap acceptance failed: ' + key)
        report['observed'] = observed
        report['qualification_passed'] = True
    finally:
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
