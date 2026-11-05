"""Fresh local packet combining the guarded candidate and existing canonical tests."""
import gzip
import argparse
import hashlib
from pathlib import Path
import shutil
import re
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = ROOT / 'inbox/guarded-source-v1/spikes/cloudflare-pyodide'
PACKET = ROOT / 'inbox/staged-runtime-v2'
BASE = ROOT / 'inbox/staged-runtime-v1'


def main(*, source_packet='guarded-source-v1', revision=2):
    global SOURCE, PACKET
    if not re.fullmatch(r'guarded-source-v[1-9][0-9]*', source_packet) or not 2 <= revision <= 20:
        raise ValueError('Bounded owned qualification packet required')
    SOURCE = ROOT / 'inbox' / source_packet / 'spikes/cloudflare-pyodide'
    PACKET = ROOT / f'inbox/staged-runtime-v{revision}'
    if PACKET.exists():
        raise RuntimeError('Reconcile existing packet before retry')
    shutil.copytree(BASE, PACKET, ignore=shutil.ignore_patterns('node_modules', '.wrangler', '__pycache__', '*.gz'))
    runtime = gzip.decompress((BASE / 'project/backend/generated-runtime.mjs.gz').read_bytes())
    if hashlib.sha256(runtime).hexdigest() != '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731':
        raise RuntimeError('Runtime pin changed')
    (PACKET / 'project/backend/generated-runtime.mjs').write_bytes(runtime)
    candidate = PACKET / 'candidate'
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--cpus', '.5', '--memory', '256m',
        '--memory-swap', '256m', '--pids-limit', '64', '--entrypoint', 'node',
        '-v', f'{SOURCE}:/source:ro', '-v', f'{PACKET}:/packet',
        'bifrost-test-api-dev:bifrost-test-7ddfac36', '/source/dev/stage-canonical-candidate.mjs',
        '/packet/project/backend/generated-runtime.mjs', '/packet/candidate'], check=True, timeout=30)
    for path in candidate.rglob('*'):
        if path.is_file():
            target = PACKET / 'project' / path.relative_to(candidate)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    shutil.copyfile(SOURCE / 'dev/executor/canonical-local-entry.mjs', PACKET / 'project/dev/executor/canonical-local-entry.mjs')
    config = PACKET / 'project/cloudflare.config.ts'
    value = config.read_text()
    before = "entrypoint: './backend/authorized-local-entry.mjs'"
    if value.count(before) != 1:
        raise RuntimeError('Local fixture configuration changed')
    config.write_text(value.replace(before, "entrypoint: './dev/executor/canonical-local-entry.mjs'"))
    print('Prepared fresh guarded local packet; no runtime execution yet')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-packet', default='guarded-source-v1')
    parser.add_argument('--revision', type=int, default=2)
    args = parser.parse_args()
    main(source_packet=args.source_packet, revision=args.revision)
