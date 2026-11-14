"""Credential-free SDK drift probe; cached local cf dev, never deployment."""
import hashlib
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'
OLD = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'


def command(args, timeout=20):
    return subprocess.check_output(args, text=True, timeout=timeout).strip()


def main(revision, *, rpc_results=False, rpc_context=False, rpc_http_context=False, cpu_egress=False, approved_https=False):
    if sum((rpc_results, rpc_context, rpc_http_context, cpu_egress, approved_https)) > 1:
        raise ValueError('One lifecycle probe mode required')
    prefix = 'approved-https' if approved_https else 'cpu-egress' if cpu_egress else 'rpc-http-context' if rpc_http_context else 'rpc-context' if rpc_context else 'rpc-results' if rpc_results else 'sdk1217'
    packet = ROOT / f'inbox/{prefix}-v{revision}'
    run = ROOT / f'runs/{prefix}-v{revision}'
    name = f'pyodide-ddf316c1-{prefix}-v{revision}'
    if command(['hostname']) != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError('Guest/disk guard failed')
    if command(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']) != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Dependency image changed')
    existing = subprocess.run(['docker', 'inspect', name], capture_output=True)
    if existing.returncode == 0:
        raise RuntimeError('Reconcile existing exact probe container before execution')
    cache = ROOT / 'runs/runtime-source-bundle-v1/bundle-cache'
    wrapper = ROOT / 'runs/runtime-source-bundle-v1/workerd-with-cache.sh'
    if hashlib.sha256(wrapper.read_bytes()).hexdigest() != '9f9ad4a47455ff2f29a5600471855260f88f0bc42a690917fa47a46ce34bffe7':
        raise RuntimeError('Workerd wrapper changed')
    bundle = cache / 'pyodide_314.0.6_2026-08-17_6.capnp.bin'
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260':
        raise RuntimeError('Runtime bundle changed')
    run.mkdir()
    shutil.copytree(packet / 'project', run / 'project')
    (run / 'home').mkdir()
    config = run / 'project/cloudflare.config.ts'
    source = config.read_text()
    if source.count('./backend/authorized-local-entry.mjs') != 1:
        raise RuntimeError('Unexpected local CLI configuration')
    entrypoint = './backend/approved-https-entry.mjs' if approved_https else './backend/cpu-egress-entry.mjs' if cpu_egress else './backend/rpc-http-context-entry.mjs' if rpc_http_context else './backend/rpc-context-entry.mjs' if rpc_context else './backend/rpc-result-entry.mjs' if rpc_results else './backend/latest-sdk-probe-entry.mjs'
    config.write_text(source.replace('./backend/authorized-local-entry.mjs', entrypoint))
    scope = 'credential-free approved HTTPS policy only' if approved_https else 'credential-free CPU egress deadline only' if cpu_egress else 'credential-free HTTP context lifetime only' if rpc_http_context else 'credential-free RPC context lifetime only' if rpc_context else 'credential-free RPC result lifecycle only' if rpc_results else 'credential-free SDK import/constructor only'
    report = {'scope': scope, 'cloud_invocations': 0,
        'engine_sdk_execution_verified': False, 'sdk_import_constructor_passed': False,
        'packet_sha256': {str(path.relative_to(packet)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in packet.rglob('*') if path.is_file() and path.suffix != '.gz'}}
    started = time.monotonic()
    try:
        command(['docker', 'run', '-d', '--name', name, '--label', 'pyodide.owner=ddf316c1-sdk-probe',
            '--network', 'none', '--cpus=1', '--memory=2g', '--memory-swap=2g', '--pids-limit=128',
            '-v', f'{run}:/qualification', '-v', f'{OLD}:/spike:ro', '-v', f'{OLD}/node_modules:/qualification/project/node_modules:ro',
            '-v', f'{cache}:/bundle-cache:ro', '-v', f'{wrapper}:/cache-wrapper.sh:ro',
            '-e', 'MINIFLARE_WORKERD_PATH=/cache-wrapper.sh', '-e', 'HOME=/qualification/home',
            '-e', 'CI=true', '-e', 'WRANGLER_SEND_METRICS=false', '-w', '/qualification/project',
            '--entrypoint', 'node', IMAGE, '/qualification/project/node_modules/cf/bin/cf', 'dev'])
        for _ in range(30):
            ready = subprocess.run(['docker', 'exec', name, 'node', '-e',
                "fetch('http://127.0.0.1:8799/health').then(r=>process.exit(r.status===403?0:1)).catch(()=>process.exit(1))"],
                capture_output=True, timeout=5)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Local CLI readiness failed')
        probe = ['/qualification/project/backend/approved-https-probe.mjs'] if approved_https else ['/qualification/project/backend/http-context-probe.mjs'] if (rpc_http_context or cpu_egress) else ['-e',
            "fetch('http://127.0.0.1:8799',{method:'POST',signal:AbortSignal.timeout(45000)}).then(async r=>{const t=await r.text();if(t.length>1048576)throw Error('Bound');console.log(JSON.stringify({status:r.status,body:JSON.parse(t)}));}).catch(()=>{process.exitCode=1;})"]
        result = command(['docker', 'exec', name, 'node', *probe], timeout=55)
        report['observation'] = json.loads(result)
        body = report['observation']['body']
        report['sdk_import_constructor_passed'] = (not (rpc_results or rpc_context or rpc_http_context or cpu_egress or approved_https) and report['observation']['status'] == 200
            and all(value.get('imported') for value in body.get('probes', {}).values())
            and body.get('constructor', {}).get('created') is True)
    finally:
        state = subprocess.run(['docker', 'inspect', name, '--format', '{{json .}}'], capture_output=True, text=True)
        if state.returncode == 0:
            value = json.loads(state.stdout)
            if value['Config']['Labels'].get('pyodide.owner') != 'ddf316c1-sdk-probe':
                raise RuntimeError('Uncertain container ownership; cleanup withheld')
            report['provider_state_before_cleanup'] = value['State']
            command(['docker', 'stop', '-t', '5', name])
            report['running_after_cleanup'] = command(['docker', 'inspect', name, '--format', '{{.State.Running}}'])
        report['elapsed_seconds'] = time.monotonic() - started
        (run / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['observation']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision', type=int, choices=range(1, 100), default=1)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--rpc-results', action='store_true')
    mode.add_argument('--rpc-context', action='store_true')
    mode.add_argument('--rpc-http-context', action='store_true')
    mode.add_argument('--cpu-egress', action='store_true')
    mode.add_argument('--approved-https', action='store_true')
    args = parser.parse_args()
    main(args.revision, rpc_results=args.rpc_results, rpc_context=args.rpc_context, rpc_http_context=args.rpc_http_context, cpu_egress=args.cpu_egress, approved_https=args.approved_https)
