"""Build the exact locally accepted canonical candidate with cached cf CLI."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = ROOT / 'inbox/staged-runtime-v4/candidate'
RUN = ROOT / 'runs/cloud-canonical-build-v1'
CACHE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide/node_modules'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Wrong assigned guest')
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError('Disk guard failed')
    accepted = json.loads((ROOT / 'runs/staged-runtime-v4/receipt.json').read_text())
    if accepted.get('qualification_passed') is not True or accepted.get('running_after_cleanup') != []:
        raise RuntimeError('Local canonical acceptance/cleanup missing')
    manifest = json.loads((SOURCE / 'candidate-provenance.json').read_text())
    for name, expected in manifest['files_sha256'].items():
        if hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Accepted candidate source changed')
    RUN.mkdir()
    shutil.copytree(SOURCE, RUN / 'project')
    (RUN / 'home').mkdir()
    command = ['docker', 'run', '--rm', '--network', 'none', '--cpus', '1', '--memory', '2g',
        '--memory-swap', '2g', '--pids-limit', '128', '--entrypoint', 'node',
        '-e', 'CI=true', '-e', 'WRANGLER_SEND_METRICS=false', '-e', 'HOME=/qualification/home',
        '-e', 'DEV_EXECUTOR_OPEN=false', '-e', 'DEV_EXECUTOR_EXPIRES_AT=0',
        '-v', f'{RUN}:/qualification', '-v', f'{CACHE}:/qualification/project/node_modules:ro',
        '-v', f'{CACHE}:/qualification/project/dev/executor/node_modules:ro',
        '-w', '/qualification/project/dev/executor', IMAGE,
        '/qualification/project/node_modules/cf/bin/cf', 'deploy', '--dry-run']
    started = time.monotonic()
    with (RUN / 'build.log').open('x') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    report = {'exit_code': result.returncode, 'elapsed_seconds': time.monotonic()-started,
              'candidate_files_sha256': manifest['files_sha256'], 'network': 'none',
              'deployment_performed': False, 'cloud_invocations': 0}
    (RUN / 'receipt.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'exit_code': result.returncode, 'receipt': str(RUN / 'receipt.json')}))
    if result.returncode:
        raise RuntimeError('cf build failed; preserve log before retry')


if __name__ == '__main__':
    main()
