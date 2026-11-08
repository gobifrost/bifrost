"""Prepare a fresh explicit local SDK experiment; no accepted pin changes."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = ROOT / 'inbox/engine-context-sdk-source-v1'
SPIKE = SOURCE / 'spikes/cloudflare-pyodide'
PACKET = ROOT / 'inbox/engine-context-sdk-v1'
BASE = ROOT / 'inbox/staged-runtime-v4'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8*1024**3:
        raise RuntimeError('Assigned guest/disk guard')
    shutil.copytree(BASE, PACKET, ignore=shutil.ignore_patterns('candidate', 'node_modules', '.wrangler', '__pycache__', 'generated-runtime.mjs'))
    runtime = gzip.decompress((ROOT / 'inbox/staged-runtime-v1/project/backend/generated-runtime.mjs.gz').read_bytes())
    if hashlib.sha256(runtime).hexdigest() != '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731':
        raise RuntimeError('Qualified base changed')
    (PACKET / 'project/backend/generated-runtime.mjs').write_bytes(runtime)
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--cpus', '.5', '--memory', '256m',
        '--memory-swap', '256m', '--pids-limit', '64', '--entrypoint', 'node',
        '-v', f'{SPIKE}:/source:ro', '-v', f'{PACKET}:/packet', IMAGE,
        '/source/dev/stage-canonical-candidate.mjs', '/packet/project/backend/generated-runtime.mjs',
        '/packet/candidate'], check=True, timeout=30, stdout=subprocess.DEVNULL)
    for path in (PACKET / 'candidate').rglob('*'):
        if path.is_file():
            target = PACKET / 'project' / path.relative_to(PACKET / 'candidate')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    for name in ('canonical-local-entry.mjs', 'engine-context-local-entry.mjs'):
        shutil.copyfile(SPIKE / 'dev/executor' / name, PACKET / 'project/dev/executor' / name)
    from sys import path
    path.insert(0, str(SPIKE / 'backend'))
    from engine_context_input import SOURCE as SOURCE_COMMIT, EXPECTED, validate_engine_context_overlay
    files = {name: {'py': (SOURCE / 'api' / name).read_text()} for name in EXPECTED}
    (PACKET / 'project/dev/executor/engine-context-overlay.mjs').write_text(
        'export default '+json.dumps({'source': SOURCE_COMMIT, 'files': files})+';\n')
    config = PACKET / 'project/cloudflare.config.ts'
    before = "entrypoint: './dev/executor/canonical-local-entry.mjs'"
    value = config.read_text()
    if value.count(before) != 1:
        raise RuntimeError('Qualified local fixture config changed')
    config.write_text(value.replace(before, "entrypoint: './dev/executor/engine-context-local-entry.mjs'"))
    print(json.dumps(validate_engine_context_overlay(PACKET)))


if __name__ == '__main__':
    main()
