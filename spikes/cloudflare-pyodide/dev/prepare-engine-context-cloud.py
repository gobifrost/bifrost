"""Build a closed cloud candidate from the completed local SDK acceptance."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
PACKET = ROOT / 'inbox/engine-context-sdk-v1'
RUN = ROOT / 'runs/engine-context-cloud-build-v1'
CACHE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide/node_modules'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8*1024**3:
        raise RuntimeError('Assigned guest/disk guard')
    accepted = json.loads((ROOT / 'runs/engine-context-sdk-v1/receipt.json').read_text())
    if accepted.get('qualification_passed') is not True or accepted.get('running_after_cleanup') != []:
        raise RuntimeError('Completed local SDK qualification required')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    shutil.copytree(PACKET / 'candidate', RUN / 'project')
    target = RUN / 'project/dev/executor'
    shutil.copyfile(PACKET / 'project/dev/executor/engine-context-overlay.mjs', target / 'engine-context-overlay.mjs')
    shutil.copyfile(Path(__file__).with_name('engine-context-cloud-entry.mjs'), target / 'engine-context-cloud-entry.mjs')
    config = target / 'cloudflare.config.ts'
    text = config.read_text()
    before = "entrypoint: './canonical-entry.mjs'"
    if text.count(before) != 1:
        raise RuntimeError('Qualified deployment config changed')
    config.write_text(text.replace(before, "entrypoint: './engine-context-cloud-entry.mjs'"))
    command = ['docker', 'run', '--rm', '--network', 'none', '--cpus', '1', '--memory', '2g',
        '--memory-swap', '2g', '--pids-limit', '128', '--entrypoint', 'node',
        '-e', 'CI=true', '-e', 'WRANGLER_SEND_METRICS=false', '-e', 'HOME=/qualification/home',
        '-v', f'{RUN}:/qualification', '-v', f'{CACHE}:/qualification/project/node_modules:ro',
        '-v', f'{CACHE}:/qualification/project/dev/executor/node_modules:ro',
        '-w', '/qualification/project/dev/executor', IMAGE,
        '/qualification/project/node_modules/cf/bin/cf', 'deploy', '--dry-run']
    with (RUN / 'build.log').open('x') as log:
        subprocess.run(command, check=True, timeout=120, stdout=log, stderr=subprocess.STDOUT)
    report = {'built': True, 'cloud_invocations': 0,
        'accepted_local_receipt_sha256': hashlib.sha256((ROOT / 'runs/engine-context-sdk-v1/receipt.json').read_bytes()).hexdigest(),
        'execution_overlay': accepted['staged_runtime_provenance']['execution_overlay'],
        'files_sha256': {str(path.relative_to(RUN / 'project')): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (RUN / 'project').rglob('*') if path.is_file() and '.cloudflare' not in path.parts}}
    (RUN / 'receipt.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'built': True, 'cloud_invocations': 0}))


if __name__ == '__main__':
    main()
