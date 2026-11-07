"""Bounded closed-guard check; no credentials, SDK calls or Cloudflare access."""
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
PACKET = ROOT / 'inbox/canonical-build-v1'
RUN = ROOT / 'runs/canonical-guard-v2'
NAME = 'pyodide-ddf316c1-canonical-guard-v2'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'
CACHE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide/node_modules'


def command(args, timeout=20):
    return subprocess.check_output(args, text=True, timeout=timeout).strip()


def main():
    if command(['hostname']) != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError('Assigned guest/disk guard failed')
    if command(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']) != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Dependency image changed')
    if subprocess.run(['docker', 'inspect', NAME], capture_output=True).returncode == 0:
        raise RuntimeError('Reconcile existing exact guard container first')
    RUN.mkdir()
    report = {'scope': 'closed local canonical guard only', 'cloud_invocations': 0,
              'engine_sdk_execution_verified': False, 'physical_termination_verified': False, 'passed': False}
    started = time.monotonic()
    try:
        command(['docker', 'run', '-d', '--name', NAME, '--label', 'pyodide.owner=ddf316c1-canonical-guard',
                 '--network', 'none', '--cpus', '1', '--memory', '1g', '--memory-swap', '1g', '--pids-limit', '128',
                 '--entrypoint', 'node', '-e', 'CI=true', '-e', 'WRANGLER_SEND_METRICS=false',
                 '-e', 'HOME=/qualification/guard-home', '-e', 'DEV_EXECUTOR_OPEN=false', '-e', 'DEV_EXECUTOR_EXPIRES_AT=0',
                 '-v', f'{PACKET}:/qualification', '-v', f'{CACHE}:/qualification/project/node_modules:ro',
                 '-v', f'{CACHE}:/qualification/project/dev/executor/node_modules:ro',
                 '-w', '/qualification/project/dev/executor', IMAGE,
                 '/qualification/project/node_modules/cf/bin/cf', 'dev'])
        while time.monotonic()-started < 45:
            state = json.loads(command(['docker', 'inspect', NAME, '--format', '{{json .State}}']))
            if not state['Running']:
                raise RuntimeError('Local guard provider exited before readiness')
            ready = subprocess.run(['docker', 'exec', NAME, 'node', '-e',
                "fetch('http://127.0.0.1:8787/',{signal:AbortSignal.timeout(2000)}).then(r=>process.exit(r.status===403?0:1)).catch(()=>process.exit(1))"],
                capture_output=True, timeout=5)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Closed guard readiness bound exceeded')
        script = """const statuses=[];
for(const options of [{method:'POST',body:'not-json'},{method:'POST',headers:{'X-Dev-Auth':'synthetic'}},{method:'GET'}]) {
 const r=await fetch('http://127.0.0.1:8787/',{...options,signal:AbortSignal.timeout(3000)});
 statuses.push(r.status);await r.body?.cancel();
}console.log(JSON.stringify(statuses));if(statuses.some(s=>s!==403))process.exitCode=1;"""
        report['statuses'] = json.loads(command(['docker', 'exec', NAME, 'node', '-e', script], timeout=15))
        report['passed'] = report['statuses'] == [403, 403, 403]
    finally:
        state = subprocess.run(['docker', 'inspect', NAME, '--format', '{{json .}}'], capture_output=True, text=True)
        if state.returncode == 0:
            value = json.loads(state.stdout)
            if value['Config']['Labels'].get('pyodide.owner') != 'ddf316c1-canonical-guard':
                raise RuntimeError('Uncertain ownership; cleanup withheld')
            report['provider_state'] = value['State']
            logs = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True)
            (RUN / 'runtime.log').write_text(logs.stdout + logs.stderr)
            command(['docker', 'stop', '-t', '5', NAME])
            report['running_after_cleanup'] = command(['docker', 'inspect', NAME, '--format', '{{.State.Running}}'])
        report['elapsed_seconds'] = time.monotonic()-started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
