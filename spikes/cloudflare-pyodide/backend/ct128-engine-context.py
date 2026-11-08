"""Pinned local Python Worker probe; no cloud or existing stack mutation."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import argparse

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
PACKET = ROOT / 'inbox/engine-context-v1'
RUN = ROOT / 'runs/engine-context-v1'
NAME = 'pyodide-engine-context-v1'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'
CACHE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide/node_modules'


def command(args, timeout=30):
    return subprocess.check_output(args, text=True, timeout=timeout, stderr=subprocess.STDOUT).strip()


def main(revision=1):
    global RUN, NAME
    if revision not in (1, 2, 3):
        raise ValueError('Bounded probe revision required')
    RUN = ROOT / f'runs/engine-context-v{revision}'
    NAME = f'pyodide-engine-context-v{revision}'
    if command(['hostname']) != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8*1024**3:
        raise RuntimeError('Assigned guest/disk guard')
    if command(['docker', 'ps', '-aq', '--filter', 'name=^/'+NAME+'$']):
        raise RuntimeError('Reconcile existing owned probe')
    if command(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']) != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Pinned native harness image changed')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    project = RUN / 'project'
    project.mkdir()
    baseline = gzip.decompress((ROOT / 'inbox/staged-runtime-v1/project/backend/generated-runtime.mjs.gz').read_bytes())
    if hashlib.sha256(baseline).hexdigest() != '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731':
        raise RuntimeError('Qualified baseline bundle changed')
    (project / 'baseline.mjs').write_bytes(baseline)
    names = ('src/services/execution/engine.py', 'src/services/execution/worker.py', 'bifrost/_execution_context.py')
    files = {name: {'py': (PACKET / 'api' / name).read_text()} for name in names}
    overlay = {'source': '1796c6fcf68964aa2ff36421ccc7be697d6322f7', 'files': files}
    (project / 'overlay.mjs').write_text('export default '+json.dumps(overlay)+';\n')
    spike = PACKET / 'spikes/cloudflare-pyodide/backend'
    shutil.copyfile(spike / 'engine-context-entry.mjs', project / 'entry.mjs')
    (project / 'probe-source.mjs').write_text('export default '+json.dumps((spike / 'engine-context-probe.py').read_text())+';\n')
    (project / 'cloudflare.config.ts').write_text("import {defineConfig} from 'cf/config';export default defineConfig({worker:{name:'local-engine-context',entrypoint:'./entry.mjs',compatibilityDate:'2026-10-30',workersDev:false,previewUrls:false,env:{LOADER:{type:'worker-loader'}}}});")
    (project / 'wrangler.config.ts').write_text("import {defineWranglerConfig} from 'wrangler/experimental-config';export default defineWranglerConfig({dev:{ip:'127.0.0.1',port:8813}});")
    (project / 'package.json').write_text(json.dumps({'private': True, 'type': 'module',
        'devDependencies': {'cf': '1.0.0-beta.26', 'wrangler': '4.156.0'}}))
    report = {'cloud_invocations': 0, 'physical_termination_verified': False,
              'sdk_operations_verified': False, 'source': overlay['source'],
              'overlay_sha256': {name: hashlib.sha256(value['py'].encode()).hexdigest() for name, value in files.items()},
              'cases': [], 'passed': False}
    started = time.monotonic()
    wrapper = ROOT / 'runs/runtime-source-bundle-v1/workerd-with-cache.sh'
    bundle_cache = ROOT / 'runs/runtime-source-bundle-v1/bundle-cache'
    if (hashlib.sha256(wrapper.read_bytes()).hexdigest() != '9f9ad4a47455ff2f29a5600471855260f88f0bc42a690917fa47a46ce34bffe7'
        or hashlib.sha256((bundle_cache / 'pyodide_314.0.6_2026-08-17_6.capnp.bin').read_bytes()).hexdigest()
        != '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260'):
        raise RuntimeError('Qualified runtime cache/wrapper changed')
    try:
        command(['docker', 'run', '-d', '--name', NAME, '--network', 'none', '--cpus', '1',
            '--memory', '2g', '--memory-swap', '2g', '--pids-limit', '128', '--entrypoint', 'node',
            '-e', 'CI=true', '-e', 'WRANGLER_SEND_METRICS=false', '-e', 'HOME=/qualification/home',
            '-e', 'MINIFLARE_WORKERD_PATH=/cache-wrapper.sh', '-v', f'{RUN}:/qualification',
            '-v', f'{CACHE}:/qualification/project/node_modules:ro',
            '-v', f'{CACHE}:/spike/node_modules:ro',
            '-v', f'{wrapper}:/cache-wrapper.sh:ro', '-v', f'{bundle_cache}:/bundle-cache:ro',
            '-w', '/qualification/project', IMAGE, '/qualification/project/node_modules/cf/bin/cf', 'dev'])
        while time.monotonic()-started < 45:
            if 'Ready on' in command(['docker', 'logs', NAME]):
                break
            if command(['docker', 'inspect', NAME, '--format', '{{.State.Running}}']) != 'true':
                raise RuntimeError('Owned runtime exited before readiness')
            time.sleep(1)
        else:
            raise RuntimeError('Local readiness bound')
        js = """const results=[];for(const mode of ['baseline','engine-only','engine-context']) {
const r=await fetch('http://127.0.0.1:8813',{method:'POST',body:JSON.stringify({mode}),signal:AbortSignal.timeout(30000)});
results.push({mode,status:r.status,body:await r.json()});}console.log(JSON.stringify(results));"""
        report['cases'] = json.loads(command(['docker', 'exec', NAME, 'node', '-e', js], timeout=100))
        base, negative, current = report['cases']
        expected = {'value': 28, 'public_url': 'https://isolated.example.invalid', 'is_agent': True,
                    'execution_origin': 'agent', 'agent_id': 'synthetic-agent',
                    'original_user_is_platform_admin': True, 'original_user_id': 'synthetic-original-user'}
        report['passed'] = (base['body'].get('status') == 'Success'
            and negative['body'].get('error_type') == 'TypeError'
            and current['body'].get('status') == 'Success' and current['body'].get('result') == expected)
    finally:
        logs = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True)
        (RUN / 'runtime.log').write_text(logs.stdout+logs.stderr)
        command(['docker', 'stop', '-t', '5', NAME])
        report['owned_running_after_cleanup'] = command(['docker', 'ps', '-q', '--filter', 'name=^/'+NAME+'$'])
        report['elapsed_seconds'] = time.monotonic()-started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    if not report['passed']:
        raise RuntimeError('Engine/context qualification failed; preserve diagnostic')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision', type=int, choices=(1, 2, 3), default=1)
    main(parser.parse_args().revision)
