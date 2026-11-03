"""Serial local signal qualification with unchanged deployed Worker settings."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SPIKE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'
PACKET = ROOT / 'inbox/request-signal-v1/packet'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def out(command):
    return subprocess.check_output(command, text=True, timeout=30).strip()


def qualify(mode):
    if out(['hostname']) != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 5 * 1024**3:
        raise RuntimeError('Assigned guest/disk guard failed')
    if out(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']) != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Pinned image changed')
    cache = ROOT / 'runs/runtime-source-bundle-v1/bundle-cache'
    bundle = cache / 'pyodide_314.0.6_2026-08-17_6.capnp.bin'
    wrapper = ROOT / 'runs/runtime-source-bundle-v1/workerd-with-cache.sh'
    for path, digest in [(bundle, '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260'),
                         (wrapper, '9f9ad4a47455ff2f29a5600471855260f88f0bc42a690917fa47a46ce34bffe7')]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Pinned runtime/cache wrapper changed')
    run = ROOT / ('runs/request-signal-v1-' + mode)
    run.mkdir()
    (run / 'home').mkdir()
    shutil.copytree(PACKET, run / 'packet')
    name = 'pyodide-ddf316c1-request-signal-' + mode
    report = {'mode': mode, 'cloud_invocations': 0, 'physical_termination_verified': False,
              'source_base': 'cceccf3abe81af72820991a07fd00f7263fd455a',
              'packet_sha256': {str(path.relative_to(PACKET)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in PACKET.rglob('*') if path.is_file()}}
    command = ['docker', 'run', '-d', '--name', name, '--network=none', '--cpus=1', '--memory=1g',
               '--memory-swap=1g', '--pids-limit=256', '--mount', f'type=bind,src={SPIKE},dst=/spike,readonly',
               '--mount', f'type=bind,src={SPIKE}/node_modules,dst=/qualification/packet/node_modules,readonly',
               '--mount', f'type=bind,src={run},dst=/qualification',
               '--mount', f'type=bind,src={cache},dst=/bundle-cache,readonly',
               '--mount', f'type=bind,src={wrapper},dst=/cache-wrapper.sh,readonly',
               '--env', 'MINIFLARE_WORKERD_PATH=/cache-wrapper.sh', '--env', 'HOME=/qualification/home',
               '--env', 'CI=true', '--env', 'WRANGLER_SEND_METRICS=false', '--env', 'REQUEST_SIGNAL_MODE=' + mode,
               '--workdir', '/qualification/packet', '--entrypoint', 'node', IMAGE, '/spike/node_modules/cf/bin/cf', 'dev']
    report['command'] = command
    started = time.monotonic()
    created = False
    try:
        out(command)
        created = True
        while time.monotonic() - started < 45:
            if not json.loads(out(['docker', 'inspect', name]))[0]['State']['Running']:
                raise RuntimeError('Local cf server stopped')
            check = subprocess.run(['docker', 'exec', name, 'node', '-e',
                "fetch('http://127.0.0.1:8816/').then(r=>process.exit(r.status===403?0:1)).catch(()=>process.exit(1))"],
                capture_output=True, timeout=5)
            if check.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Readiness deadline exceeded')
        result = subprocess.run(['docker', 'exec', name, 'node', 'request-signal-driver.mjs'],
                                 capture_output=True, text=True, timeout=90)
        (run / 'driver.log').write_text(result.stdout + result.stderr)
        report['driver_exit_code'] = result.returncode
        if result.returncode:
            raise RuntimeError('Signal diagnostic failed; preserve logs')
        report['observation'] = json.loads((run / 'observation.json').read_text())
    finally:
        if created:
            logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True, timeout=10)
            (run / 'runtime.log').write_text(logs.stdout + logs.stderr)
            report['state_before_cleanup'] = json.loads(out(['docker', 'inspect', name]))[0]['State']
            subprocess.run(['docker', 'stop', '-t', '5', name], capture_output=True, check=True, timeout=15)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter', 'name=^/' + name + '$']).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (run / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    for value in ('disabled', 'enabled'):
        qualify(value)
