"""One signed Linux workflow/retirement with an isolated candidate API mount."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
BASE = ROOT / 'upstream-025f6c0'
CHECKOUT = ROOT / 'startup-linux-candidate-v1'
PACKET = ROOT / 'inbox/startup-linux-candidate-v1'
RUN = ROOT / 'runs/startup-linux-candidate-v1'
SOURCE = '1d33150eb376b71f8a81b427920eda1b0447f829'
IMAGE = 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b'


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=45, **kwargs).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if out(['hostname']) != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError('Assigned guest/free disk guard failed')
    if CHECKOUT.exists() or RUN.exists():
        raise RuntimeError('Reconcile existing exclusive source/run before execution')
    if out(['git', 'rev-parse', 'HEAD'], cwd=BASE) != SOURCE:
        raise RuntimeError('Qualified source changed')
    expected = {'docker-compose.test.yml': '33e78480090f97ea3f04be404c5a3d312ea8486ec6d55a60c67361a8ef0bdce9',
                'api/src/worker/remote/protocol.py': '91db42936f3235599243af681d24886fbb79180a7020633dfafe35887b3956d5',
                'api/src/worker/remote/host.py': '6ce5e6be94f195d2690f4fbb929fc78d9d068be1f4f6e391a14a0c371b2e3078'}
    if any(digest(BASE / path) != value for path, value in expected.items()):
        raise RuntimeError('Qualified bounded Compose/control overlays changed')
    if digest(PACKET / 'remote_pool.py.txt') != '90bf84fb5213856473d04a770bae6ac1d3dfd71eadd0a897b0ac291a6c3cddde':
        raise RuntimeError('Startup candidate changed')
    if out(['docker', 'image', 'inspect', 'bifrost-test-api-dev:bifrost-test-7ddfac36', '--format', '{{.Id}}']) != IMAGE:
        raise RuntimeError('Cached API image changed')
    out(['git', 'worktree', 'add', '--detach', str(CHECKOUT), SOURCE], cwd=BASE, stderr=subprocess.STDOUT)
    for path in expected:
        shutil.copyfile(BASE / path, CHECKOUT / path)
    compose_path = CHECKOUT / 'docker-compose.test.yml'
    text = compose_path.read_text()
    for service in ('api', 'test-runner'):
        start = text.index(f'\n  {service}:\n')
        volumes = text.index('    volumes:\n', start) + len('    volumes:\n')
        text = text[:volumes] + f'      - {PACKET}/remote_pool.py.txt:/app/src/worker/remote/remote_pool.py:ro\n' + text[volumes:]
    start = text.index('\n  test-runner:\n')
    runner = text[start:]
    if '    mem_limit: 768m\n' not in runner or '    memswap_limit: 768m\n' not in runner:
        raise RuntimeError('Qualified runner resource layout changed')
    runner = runner.replace('    mem_limit: 768m\n', '    mem_limit: 2g\n', 1).replace('    memswap_limit: 768m\n', '    memswap_limit: 2g\n', 1)
    runner = runner.replace('\n  test-runner:\n', '\n  test-runner:\n    pids_limit: 128\n', 1)
    text = text[:start] + runner
    compose_path.write_text(text)
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    if out(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]):
        raise RuntimeError('Unexpected existing containers in exclusive project')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    report = {'scope': 'isolated candidate overlay, actual signed Linux workflow and retirement', 'source': SOURCE,
              'project': project, 'cloud_invocations': 0, 'qualification_passed': False,
              'source_overlays': expected, 'compose_sha256': digest(compose_path),
              'packet_sha256': {str(path.relative_to(PACKET)): digest(path) for path in PACKET.rglob('*') if path.is_file()},
              'commands': [], 'volumes_deleted': False}
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
    commands = [['./test.sh', 'stack', 'up'], [*compose, 'stop', 'worker', 'scheduler'],
                [*compose, 'exec', '-T', 'api', 'python', '-c',
                 'import hashlib; print(hashlib.sha256(open("/app/src/worker/remote/remote_pool.py","rb").read()).hexdigest())'],
                [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', '--name', project + '-signed-startup',
                 '--volume', str(PACKET) + ':/qualification:ro', '--volume', str(PACKET / 'spike') + ':/spike:ro',
                 'test-runner', 'python', '/qualification/linux-startup-candidate.py']]
    started = time.monotonic()
    try:
        for index, args in enumerate(commands):
            report['commands'].append({'argv': args})
            with (RUN / f'phase-{index}.log').open('x') as log:
                process = subprocess.Popen(args, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                while process.poll() is None:
                    if time.monotonic() - started > 240 or shutil.disk_usage(ROOT).free < 8 * 1024**3:
                        os.killpg(process.pid, signal.SIGTERM)
                        raise RuntimeError('Qualification wall/disk bound exceeded')
                    time.sleep(1)
                report['commands'][-1]['exit_code'] = process.returncode
            if process.returncode:
                raise RuntimeError(f'Qualification phase {index} failed; preserve private log')
            if index == 2 and (RUN / 'phase-2.log').read_text().strip() != '90bf84fb5213856473d04a770bae6ac1d3dfd71eadd0a897b0ac291a6c3cddde':
                raise RuntimeError('Actual API candidate mount differs')
        observations = [json.loads(line) for line in (RUN / 'phase-3.log').read_text().splitlines()
                        if line.startswith('{"runtime": "actual-linux-signed-control-reference"')]
        result = observations[-1]
        for key in ('qualification_passed', 'real_linux_fork_verified', 'retained_terminal_acknowledged',
                    'native_templates_stopped', 'fixture_worker_cleanup_verified', 'signed_retirement_and_pool_cache_cleanup_verified'):
            if result.get(key) is not True:
                raise RuntimeError('Signed candidate acceptance missing: ' + key)
        report['observed'] = result
        report['qualification_passed'] = True
    finally:
        ids = out(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
        if ids:
            subprocess.run(['docker', 'stop', '-t', '10', *ids], check=True, capture_output=True, timeout=35)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'project': project, 'qualification_passed': report['qualification_passed'],
                      'elapsed_seconds': report['elapsed_seconds'], 'running_after_cleanup': report['running_after_cleanup']}))


if __name__ == '__main__':
    main()
