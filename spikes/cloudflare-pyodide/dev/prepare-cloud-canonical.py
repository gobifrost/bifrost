"""Offline pinned build for one bounded cloud qualification; no credentials."""
import json
import gzip
from pathlib import Path
import shutil
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = ROOT / 'inbox/cloud-source-v2/spikes/cloudflare-pyodide'
RUN = ROOT / 'runs/cloud-canonical-build-v2'
CACHE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide/node_modules'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8*1024**3:
        raise RuntimeError('Guest/disk guard')
    if subprocess.check_output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip() != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Pinned dependency image changed')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    previous = RUN / 'runtime.mjs'
    previous.write_bytes(gzip.decompress((ROOT / 'inbox/staged-runtime-v1/project/backend/generated-runtime.mjs.gz').read_bytes()))
    command = ['docker', 'run', '--rm', '--network', 'none', '--cpus', '.5', '--memory', '256m',
               '--memory-swap', '256m', '--pids-limit', '64', '--entrypoint', 'node',
               '-v', f'{SOURCE}:/source:ro', '-v', f'{ROOT}:/owned', IMAGE,
               '/source/dev/stage-canonical-candidate.mjs', str(Path('/owned') / previous.relative_to(ROOT)),
               '/owned/runs/cloud-canonical-build-v2/project']
    subprocess.run(command, check=True, timeout=30, stdout=subprocess.DEVNULL)
    command = ['docker', 'run', '--rm', '--network', 'none', '--cpus', '1', '--memory', '2g',
        '--memory-swap', '2g', '--pids-limit', '128', '--entrypoint', 'node',
        '-e', 'CI=true', '-e', 'WRANGLER_SEND_METRICS=false', '-e', 'HOME=/qualification/home',
        '-v', f'{RUN}:/qualification', '-v', f'{CACHE}:/qualification/project/node_modules:ro',
        '-v', f'{CACHE}:/qualification/project/dev/executor/node_modules:ro',
        '-w', '/qualification/project/dev/executor', IMAGE,
        '/qualification/project/node_modules/cf/bin/cf', 'deploy', '--dry-run']
    with (RUN / 'build.log').open('x') as log:
        subprocess.run(command, check=True, timeout=120, stdout=log, stderr=subprocess.STDOUT)
    print(json.dumps({'built': True, 'cloud_invocations': 0}))


if __name__ == '__main__':
    main()
