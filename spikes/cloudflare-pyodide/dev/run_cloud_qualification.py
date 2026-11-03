"""Run one fixed cloud qualification in the assigned isolated development guest."""
import json
import os
from pathlib import Path
import subprocess
import time
import argparse
import re

parser = argparse.ArgumentParser()
parser.add_argument('--integration-fresh', action='store_true')
parser.add_argument('--streams', action='store_true')
parser.add_argument('--benchmark', choices=['json_transform', 'http_orchestration', 'integration_read', 'table_update', 'multi_step'])
parser.add_argument('--private-directory', default='cloud-private')
args = parser.parse_args()
if not re.fullmatch(r'cloud-private(?:-[a-z0-9-]{1,48})?', args.private_directory):
    raise RuntimeError('Owned private-directory name required')

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
RUN = ROOT / 'dev-deploy-2037ac0'
CHECKOUT = ROOT / 'dev-2037ac0'
PRIVATE = RUN / args.private_directory
if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
    raise RuntimeError('Assigned guest required')
PRIVATE.mkdir(mode=0o700, exist_ok=True)
profile = json.loads((PRIVATE / 'profile.json').read_text())
if profile['expires_at_ms'] <= time.time() * 1000:
    raise RuntimeError('Expired cloud qualification')
raw = subprocess.check_output(['docker', 'inspect', 'bifrost-debug-074217d4-api-1', '--format', '{{json .Config.Env}}'], text=True)
environment = json.loads(raw)
envfile = PRIVATE / 'dev-api.env'
with envfile.open('w') as file:
    for value in environment:
        if '\n' in value or '\r' in value:
            raise RuntimeError('Multiline container environment rejected')
        file.write(value + '\n')
os.chmod(envfile, 0o600)
command = ['timeout', '180', 'docker', 'run', '--rm', '--name', 'bifrost-pyodide-dev-cloud-qualification',
           '--network', 'bifrost-debug-074217d4_default', '--cpus=1', '--memory=768m', '--memory-swap=768m', '--pids-limit=128',
           '--env-file', str(envfile), '--volumes-from', 'bifrost-debug-074217d4-api-1:ro',
           '--mount', f'type=bind,src={CHECKOUT}/api/tests,dst=/app/tests,readonly',
           '--mount', f'type=bind,src={RUN}/authorized-local.py,dst=/qualification/authorized-local.py,readonly',
           '--mount', f'type=bind,src={RUN}/benchmark-workloads.py,dst=/qualification/benchmark-workloads.py,readonly',
           '--mount', f'type=bind,src={PRIVATE},dst=/qualification/private',
           '--entrypoint', 'python', 'bifrost-test-api-dev:bifrost-test-7ddfac36', '-c',
           "import asyncio,runpy; asyncio.run(runpy.run_path('/qualification/authorized-local.py')['main'](dev_qualification=True, "
           f"integration_probe={bool(args.integration_fresh or args.benchmark == 'integration_read')!r}, "
           f"fresh_probe={args.integration_fresh!r}, stream_probe={args.streams!r}, benchmark={args.benchmark!r}))"]
logpath = RUN / f'cloud-qualification-{time.time_ns()}.private.log'
with logpath.open('x') as log:
    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=200)
print(json.dumps({'exit_code': result.returncode, 'guest': 'CT128', 'max_memory_mib': 768,
                  'source': '2037ac0d9ec95e94a77ce440001ae016d4215407', 'fixture_overlay': 'authorized-local.py',
                  'log': str(logpath)}))
raise SystemExit(result.returncode)
