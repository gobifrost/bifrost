"""One credential-free, network-disabled local workerd ABI probe in assigned CT128."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = '785b560b9973020e1c13401383e1a73a87a54940'
SPIKE = ROOT / SOURCE / 'spikes/cloudflare-pyodide'
RUN = ROOT / 'runs/owner-run72-runtime-probe'
NAME = 'pyodide-ddf316c1-run72-runtime'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def out(args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def main():
    if out(['hostname']) != 'bifrost-pyodide-test-01' or out(['docker', 'ps', '-q']):
        raise RuntimeError('Wrong guest or existing active containers')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    result = {'source': SOURCE, 'guest': 128, 'network': 'none', 'cloud_invocations': 0,
              'physical_termination_verified': False, 'runtime_qualified': False,
              'started_at_epoch': time.time(), 'wall_limit_seconds': 180,
              'container_limits': {'cpus': 1.5, 'memory_mib': 3072, 'pids': 256}}
    started = time.monotonic()
    try:
        args = ['docker', 'run', '-d', '--name', NAME, '--network=none', '--cpus=1.5',
                '--memory=3g', '--memory-swap=3g', '--pids-limit=256',
                '--mount', f'type=bind,src={SPIKE},dst=/spike',
                '--mount', f'type=bind,src={RUN / "home"},dst=/isolated-home',
                '--workdir', '/spike', '--env', 'HOME=/isolated-home', '--env', 'CI=true',
                '--env', 'WRANGLER_SEND_METRICS=false', '--entrypoint', 'node', IMAGE,
                'node_modules/wrangler/bin/wrangler.js', 'dev', '--local',
                '--ip', '127.0.0.1', '--port', '8810', '--config', 'backend/wrangler.runtime-probe.jsonc']
        result['command'] = args
        result['container_id'] = out(args)
        ready = False
        while time.monotonic() - started < 90:
            logs = subprocess.run(['docker', 'logs', NAME], text=True, capture_output=True)
            if 'Ready on' in logs.stdout + logs.stderr:
                ready = True
                break
            state = json.loads(out(['docker', 'inspect', NAME]))[0]['State']
            if not state['Running']:
                break
            if shutil.disk_usage(ROOT).used > 24 * 1024**3:
                raise RuntimeError('Disk guard reached')
            time.sleep(1)
        result['server_ready'] = ready
        if not ready:
            raise RuntimeError('Local runtime server did not become ready')
        code = "fetch('http://127.0.0.1:8810',{method:'POST',signal:AbortSignal.timeout(60000)}).then(async r=>console.log(JSON.stringify({status:r.status,body:await r.text()}))).catch(e=>{console.log(JSON.stringify({error_type:e.name}));process.exitCode=1})"
        probe = ['docker', 'exec', NAME, 'node', '-e', code]
        result['probe_command'] = probe
        with (RUN / 'probe.log').open('x') as log:
            observed = subprocess.run(probe, stdout=log, stderr=subprocess.STDOUT, timeout=70)
        result['observer_exit'] = observed.returncode
        response = json.loads((RUN / 'probe.log').read_text().strip())
        result['response'] = response
        if response.get('status') == 200:
            body = json.loads(response['body'])
            result['abi'] = body
            expected = {'pyodide': '314.0.6', 'python': [3, 14, 2], 'cache_tag': 'cpython-314',
                        'soabi': 'cpython-314-wasm32-emscripten', 'platform': 'emscripten-5.0.3-wasm32'}
            result['runtime_qualified'] = (all(body.get(k) == v for k, v in expected.items())
                and body['pydantic_core'] == '2.50.2' and body['validated'] == {'values': [1, 2]}
                and body['invalid_rejected'] is True)
    finally:
        logs = subprocess.run(['docker', 'logs', NAME], text=True, capture_output=True)
        (RUN / 'runtime.log').write_text(logs.stdout + logs.stderr)
        state = subprocess.run(['docker', 'inspect', NAME], text=True, capture_output=True)
        if state.returncode == 0:
            result['state_before_stop'] = json.loads(state.stdout)[0]['State']
            subprocess.run(['docker', 'stop', '-t', '5', NAME], stdout=subprocess.DEVNULL, check=True)
            result['state_after_stop'] = json.loads(out(['docker', 'inspect', NAME]))[0]['State']
        result['elapsed_seconds'] = time.monotonic() - started
        result['artifacts'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in RUN.iterdir() if p.is_file()}
        (RUN / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    if not result['runtime_qualified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
