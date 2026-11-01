"""Canonical test-stack claim -> actual SDK/Pyodide -> upstream terminal consumer."""
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
RUN = ROOT / 'runs/node-memory-qualification-v1'
PACKET = ROOT / 'inbox/node-memory-v1'
SPIKE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'
CORE = ROOT / 'runs/pyodide-package-qualification-resume/core/pyodide'
NAME = 'pyodide-ddf316c1-node-memory-v1'


def output(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main():
    if output(['hostname']) != 'bifrost-pyodide-test-01' or output(['docker', 'ps', '-q']):
        raise RuntimeError('Wrong guest or reconcile active containers first')
    if shutil.disk_usage(ROOT).free < 20 * 1024**3:
        raise RuntimeError('Insufficient scratch')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    report = {'api_source': SOURCE, 'runtime_source': '785b560b9973020e1c13401383e1a73a87a54940',
              'runtime': 'official-pyodide-on-node', 'cloudflare_runtime_qualified': False,
              'physical_termination_verified': False, 'cloud_invocations': 0,
              'started_at_epoch': time.time(), 'commands': [], 'qualification_passed': False}
    project = None
    started = time.monotonic()
    try:
        expected = {'node-memory-provider.mjs': 'c899c3d24ccc9925c2836f10add5015ca187ad4d486359dd6ef99d41420fa054',
                    'canonical-local.py': '5ebf9c12d5727807922921f31767e09d8c5be0e71a6b064cf4a9b89604a0c9cf'}
        for name, digest in expected.items():
            if hashlib.sha256((PACKET / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError('Source packet hash mismatch')
        report['packet_sha256'] = expected
        repo = ROOT / 'sources/bifrost-upstream.git'
        output(['git', '--git-dir', str(repo), 'fetch', 'https://github.com/MTG-Thomas/bifrost-upstream.git', SOURCE])
        if not CHECKOUT.exists():
            output(['git', '--git-dir', str(repo), 'worktree', 'add', '--detach', str(CHECKOUT), SOURCE])
        if output(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE or output(['git', 'status', '--porcelain'], cwd=CHECKOUT):
            raise RuntimeError('Existing source checkout identity or cleanliness mismatch')
        changes = output(['git', '--git-dir', str(repo), 'diff', '--name-only',
                          '0c3241789a15e418d02ca8eb6fbd8fac8016edef', SOURCE]).splitlines()
        if not all(p.startswith(('spikes/cloudflare-pyodide/', 'api/tests/')) for p in changes):
            raise RuntimeError('Existing image build inputs changed')
        project = output(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
        report['project'] = project
        env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'),
               'BIFROST_SKIP_BUILD': '1', 'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36'}
        compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
        commands = [['./test.sh', 'stack', 'up'], [*compose, 'stop', 'worker', 'scheduler'],
            ['docker', 'run', '-d', '--name', NAME, '--network', project + '_default',
             '--network-alias', 'bifrost-pyodide-ddf316c1', '--cpus=1', '--memory=1g',
             '--memory-swap=1g', '--pids-limit=128', '--label', 'com.docker.compose.project=' + project,
             '--mount', f'type=bind,src={SPIKE},dst=/spike,readonly',
             '--mount', f'type=bind,src={CORE},dst=/core,readonly',
             '--mount', f'type=bind,src={PACKET},dst=/qualification,readonly',
             '--entrypoint', 'node', 'bifrost-test-api-dev:bifrost-test-7ddfac36', '/qualification/node-memory-provider.mjs'],
            [*compose, '--profile', 'test', 'run', '--rm', '--no-deps', '--name', project + '-sdk-fixture',
             '--volume', str(SPIKE) + ':/spike:ro', '--volume', str(PACKET) + ':/qualification:ro',
             'test-runner', 'python', '/qualification/canonical-local.py', '--node-pyodide', '--integration-probe', '--integration-secret-probe']]
        for index, args in enumerate(commands):
            if index == 3:
                # Readiness probes invoke no workflow and carry no credential.
                for _ in range(30):
                    state = json.loads(output(['docker', 'inspect', NAME]))[0]['State']
                    if not state['Running']:
                        raise RuntimeError('Provider startup failed')
                    healthy = subprocess.run(['docker', 'exec', NAME, 'node', '-e',
                        "fetch('http://127.0.0.1:8799/health',{signal:AbortSignal.timeout(1000)}).then(r=>process.exitCode=r.ok?0:1).catch(()=>process.exitCode=1)"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                    if healthy.returncode == 0:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError('Provider readiness deadline')
            entry = {'argv': args, 'started_at_epoch': time.time()}
            report['commands'].append(entry)
            with (RUN / f'phase-{index}.log').open('x') as log:
                process = subprocess.Popen(args, cwd=CHECKOUT, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                while process.poll() is None:
                    if time.monotonic() - started > 900 or shutil.disk_usage(ROOT).used > 24 * 1024**3:
                        os.killpg(process.pid, signal.SIGTERM)
                        raise RuntimeError('Wall/disk guard reached')
                    time.sleep(1)
                entry['exit_code'] = process.returncode
            entry['finished_at_epoch'] = time.time()
            if process.returncode:
                raise RuntimeError('Qualification command failed')
        lines = (RUN / 'phase-3.log').read_text().splitlines()
        reports = [json.loads(line) for line in lines if line.startswith('{"runtime":')]
        result = reports[-1]
        for key in ('canonical_claim_verified', 'canonical_terminal_consumption_verified', 'lost_ack_reconnect_verified',
                    'fixture_worker_cascade_cleanup_verified', 'integration_read_verified', 'integration_secret_decryption_and_redaction_verified'):
            if result.get(key) is not True:
                raise RuntimeError('Canonical acceptance failed: ' + key)
        if result['result']['runtime'] != 'official-pyodide-on-node' or result['launches'] != 1:
            raise RuntimeError('Wrong physical runtime or duplicate launch')
        report['canonical_result'] = result
        report['qualification_passed'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        provider = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True)
        (RUN / 'provider.log').write_text(provider.stdout + provider.stderr)
        if project:
            ids = output(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
            report['owned_container_ids'] = ids
            if ids:
                subprocess.run(['docker', 'stop', '-t', '10', *ids], stdout=subprocess.DEVNULL, check=True)
            report['running_after_cleanup'] = output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
            report['volumes_deleted'] = False
        report['elapsed_seconds'] = time.monotonic() - started
        report['finished_at_epoch'] = time.time()
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({k:v for k,v in report.items() if k!='canonical_result'}))


if __name__ == '__main__':
    main()
