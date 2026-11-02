"""Credential-free supported disposal probe through cf CLI, assigned guest only."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SPIKE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'
CACHE = ROOT / 'runs/runtime-source-bundle-v1/bundle-cache'
PACKET = ROOT / 'inbox/disposal-probe-v1'
RUN = ROOT / 'runs/disposal-probe-v4'
NAME = 'pyodide-ddf316c1-disposal-probe-v4'


def out(args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def main(*, memory='1g'):
    if out(['hostname']) != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).used > 24 * 1024**3:
        raise RuntimeError('Assigned guest/disk guard failed')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    shutil.copytree(PACKET / 'packet', RUN / 'packet')
    bundle = CACHE / 'pyodide_314.0.6_2026-08-17_6.capnp.bin'
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260':
        raise RuntimeError('Pinned bundle changed')
    wrapper = ROOT / 'runs/runtime-source-bundle-v1/workerd-with-cache.sh'
    if hashlib.sha256(wrapper.read_bytes()).hexdigest() != '9f9ad4a47455ff2f29a5600471855260f88f0bc42a690917fa47a46ce34bffe7':
        raise RuntimeError('Cache wrapper changed')
    report = {'cloud_invocations': 0, 'physical_termination_verified': False,
              'bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
              'cache_wrapper_sha256': hashlib.sha256(wrapper.read_bytes()).hexdigest(),
              'cf_cli_version': json.loads((SPIKE / 'node_modules/cf/package.json').read_text())['version'],
              'wrangler_version': json.loads((SPIKE / 'node_modules/wrangler/package.json').read_text())['version'],
              'image_id': out(['docker', 'image', 'inspect', 'bifrost-test-api-dev:bifrost-test-7ddfac36', '--format', '{{.Id}}']),
              'packet_sha256': {str(p.relative_to(RUN / 'packet')): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (RUN / 'packet').rglob('*') if p.is_file()}}
    started = time.monotonic()
    try:
        command = ['docker', 'run', '-d', '--name', NAME, '--network=none', '--cpus=1', '--memory=' + memory,
                   '--memory-swap=' + memory, '--pids-limit=256', '--mount', f'type=bind,src={SPIKE},dst=/spike,readonly',
                   '--mount', f'type=bind,src={SPIKE}/node_modules,dst=/qualification/packet/node_modules,readonly',
                   '--mount', f'type=bind,src={RUN},dst=/qualification',
                   '--mount', f'type=bind,src={CACHE},dst=/bundle-cache,readonly',
                   '--mount', f'type=bind,src={wrapper},dst=/cache-wrapper.sh,readonly',
                   '--env', 'MINIFLARE_WORKERD_PATH=/cache-wrapper.sh', '--env', 'HOME=/qualification/home',
                   '--env', 'CI=true', '--env', 'WRANGLER_SEND_METRICS=false', '--workdir', '/qualification/packet',
                   '--entrypoint', 'node', 'bifrost-test-api-dev:bifrost-test-7ddfac36',
                   '/spike/node_modules/cf/bin/cf', 'dev']
        report['command'] = command
        out(command)
        while time.monotonic() - started < 45:
            if not json.loads(out(['docker', 'inspect', NAME]))[0]['State']['Running']:
                raise RuntimeError('cf local server stopped before readiness')
            readiness = "fetch('http://127.0.0.1:8813').then(r=>process.exit(r.status===403?0:1)).catch(()=>process.exit(1))"
            ready = subprocess.run(['docker', 'exec', NAME, 'node', '-e', readiness], capture_output=True, timeout=5)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('cf local readiness deadline')
        probe = "fetch('http://127.0.0.1:8813',{method:'POST',signal:AbortSignal.timeout(30000)}).then(async r=>console.log(JSON.stringify({status:r.status,body:await r.json()})))"
        result = subprocess.run(['docker', 'exec', NAME, 'node', '-e', probe], capture_output=True, text=True, timeout=35)
        report['observer_exit_code'] = result.returncode
        if result.returncode:
            (RUN / 'probe-error.log').write_text(result.stderr)
            raise RuntimeError('Supported-object probe failed')
        report['observation'] = json.loads(result.stdout)
        observation = report['observation']
        if observation['status'] != 200 or observation['body']['abi'] != {
            'python': [3, 14, 2], 'pyodide': '314.0.6', 'soabi': 'cpython-314-wasm32-emscripten'
        }:
            raise RuntimeError('Actual Python runtime did not pass the ABI positive control')
        report['runtime_probe_passed'] = True
    finally:
        logs = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True)
        (RUN / 'runtime.log').write_text(logs.stdout + logs.stderr)
        state = subprocess.run(['docker', 'inspect', NAME], capture_output=True, text=True)
        if state.returncode == 0:
            report['state_before_cleanup'] = json.loads(state.stdout)[0]['State']
            subprocess.run(['docker', 'stop', '-t', '5', NAME], capture_output=True, check=True)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter', 'name=^/' + NAME + '$']).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
