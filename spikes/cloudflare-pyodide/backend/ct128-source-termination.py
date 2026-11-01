"""Bounded credential-free workerd termination observations; never physical-stop acceptance."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
RUN = ROOT / 'runs/source-termination-v1'
PACKET = ROOT / 'inbox/source-termination-v1'
SPIKE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'
CACHE = ROOT / 'runs/runtime-source-bundle-v1/bundle-cache'
NAME = 'pyodide-ddf316c1-source-termination-v1'


def out(args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def main():
    if out(['hostname']) != 'bifrost-pyodide-test-01' or out(['docker', 'ps', '-q']):
        raise RuntimeError('Wrong guest or reconcile active containers')
    if shutil.disk_usage(ROOT).used > 24 * 1024**3:
        raise RuntimeError('Disk guard')
    RUN.mkdir()
    shutil.copytree(PACKET / 'packet', RUN / 'packet')
    (RUN / 'packet/results').mkdir()
    (RUN / 'home').mkdir()
    report = {'runtime': 'actual-local-workerd', 'cloud_invocations': 0,
              'physical_termination_verified': False, 'execution_admission_qualified': False,
              'started_at_epoch': time.time()}
    started = time.monotonic()
    try:
        bundle = CACHE / 'pyodide_314.0.6_2026-08-17_6.capnp.bin'
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        if digest != '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260':
            raise RuntimeError('Bundle changed')
        report['bundle_sha256'] = digest
        report['packet_sha256'] = {str(p.relative_to(RUN / 'packet')):hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (RUN / 'packet').rglob('*') if p.is_file()}
        wrapper = ROOT / 'runs/runtime-source-bundle-v1/workerd-with-cache.sh'
        if hashlib.sha256(wrapper.read_bytes()).hexdigest() != '9f9ad4a47455ff2f29a5600471855260f88f0bc42a690917fa47a46ce34bffe7':
            raise RuntimeError('Cache wrapper changed')
        args = ['docker', 'run', '-d', '--name', NAME, '--network=none', '--cpus=1', '--memory=1g',
                '--memory-swap=1g', '--pids-limit=256', '--mount', f'type=bind,src={SPIKE},dst=/spike,readonly',
                '--mount', f'type=bind,src={RUN},dst=/qualification',
                '--mount', f'type=bind,src={CACHE},dst=/bundle-cache,readonly',
                '--mount', f'type=bind,src={wrapper},dst=/cache-wrapper.sh,readonly',
                '--env', 'MINIFLARE_WORKERD_PATH=/cache-wrapper.sh', '--env', 'HOME=/qualification/home',
                '--env', 'CI=true', '--env', 'WRANGLER_SEND_METRICS=false', '--workdir', '/qualification/packet',
                '--entrypoint', 'node', 'bifrost-test-api-dev:bifrost-test-7ddfac36',
                '/spike/node_modules/wrangler/bin/wrangler.js', 'dev', '--local', '--ip', '127.0.0.1',
                '--port', '8811', '--config', 'backend/wrangler.guest-termination.jsonc']
        report['command'] = args
        out(args)
        while time.monotonic() - started < 45:
            logs = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True)
            if 'Ready on' in logs.stdout + logs.stderr:
                break
            if not json.loads(out(['docker', 'inspect', NAME]))[0]['State']['Running']:
                raise RuntimeError('Runtime stopped before readiness')
            time.sleep(1)
        else:
            raise RuntimeError('Readiness deadline')
        probe = ['docker', 'exec', NAME, 'node', 'backend/guest-termination-probe.mjs', '--run']
        report['probe_command'] = probe
        with (RUN / 'probe.log').open('x') as log:
            completed = subprocess.run(probe, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        report['observer_exit_code'] = completed.returncode
        observed = RUN / 'packet/results/guest-termination-timed.json'
        if observed.exists():
            report['observations'] = json.loads(observed.read_text())
    finally:
        logs = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True)
        (RUN / 'runtime.log').write_text(logs.stdout + logs.stderr)
        state = subprocess.run(['docker', 'inspect', NAME], capture_output=True, text=True)
        if state.returncode == 0:
            report['state_before_cleanup'] = json.loads(state.stdout)[0]['State']
            subprocess.run(['docker', 'stop', '-t', '5', NAME], check=True, capture_output=True)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter', 'name=^/' + NAME + '$']).splitlines()
        report['volumes_deleted'] = False
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
