"""One real Linux signed-control run of the shared five-workload benchmark."""
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
RUN = ROOT / 'runs/linux-benchmark-v1'
PACKET = ROOT / 'inbox/linux-benchmark-v1'


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
    report = {'source': SOURCE, 'scope': 'Linux signed control with one real native SDK workflow',
              'cloud_invocations': 0, 'planned_workflows': 1, 'qualification_passed': False,
              'started_at_epoch': time.time(), 'commands': []}
    started = time.monotonic()
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    report['project'] = project
    fixture = PACKET / 'linux-control-reference.py'
    report['fixture_sha256'] = hashlib.sha256(fixture.read_bytes()).hexdigest()
    if report['fixture_sha256'] != 'bb4d228fb773f1fedcae79f5593ab80e5699645bb4954be9c0deac09eb3ec734':
        raise RuntimeError('Fixture source changed')
    report['benchmark_source_sha256'] = hashlib.sha256((PACKET / 'benchmark-workloads.py').read_bytes()).hexdigest()
    if report['benchmark_source_sha256'] != '77e16fbd6adc0272bdd94c9843c81d1a2de04894d64c1267ef97178334cec3f2':
        raise RuntimeError('Benchmark source changed')
    # Bound this test process inside the existing 2 CPU / 4 GiB guest.
    limits = RUN / 'limits.yml'
    limits.write_text('services:\n  test-runner:\n    cpus: 1\n    mem_limit: 2g\n    memswap_limit: 2g\n    pids_limit: 128\n')
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36'}
    compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
    commands = [['./test.sh', 'stack', 'up'], [*compose, 'stop', 'worker', 'scheduler'],
        [*compose, '-f', str(limits), '--profile', 'test', 'run', '--rm', '--no-deps',
         '--name', project + '-linux-control-reference', '--volume', str(PACKET) + ':/qualification:ro', '--volume', str(CHECKOUT / 'spikes/cloudflare-pyodide') + ':/spike:ro',
         'test-runner', 'python', '/qualification/linux-control-reference.py', '--benchmark-probe']]
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
                   if line.startswith('{"runtime": "actual-linux-signed-control-reference"')]
        observed = reports[-1]
        for key in ['qualification_passed','real_linux_fork_verified','retained_terminal_acknowledged',
                    'native_templates_stopped','fixture_worker_cleanup_verified']:
            if observed.get(key) is not True:
                raise RuntimeError('Bootstrap acceptance failed: ' + key)
        if observed['independent_benchmark_documents'] != 3:
            raise RuntimeError('Benchmark write readback failed')
        if [row['workload'] for row in observed['benchmark']] != ['json_transform', 'http_orchestration',
                                                                'integration_read', 'table_update', 'multi_step']:
            raise RuntimeError('Benchmark workload set mismatch')
        for row in observed['benchmark']:
            samples = row['samples_wall_ms']
            if row['correctness'] is not True or len(samples) != 3 or sorted(samples)[1] != row['median_wall_ms']:
                raise RuntimeError('Benchmark correctness or sample mismatch')
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
