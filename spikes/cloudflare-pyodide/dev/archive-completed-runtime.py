"""Preserve exact duplicate runtime inputs from four completed owned fixtures."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
EXPECTED = '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731'
FIXTURES = ('canonical-cancel-v4', 'canonical-cancel-v7', 'canonical-https-v6', 'canonical-https-v7')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Wrong assigned guest')
    records = []
    # Validate the entire bounded set before altering any file.
    for fixture in FIXTURES:
        path = ROOT / 'inbox' / fixture / 'project/backend/generated-runtime.mjs'
        if path.is_symlink() or path.resolve() != path or digest(path.read_bytes()) != EXPECTED:
            raise RuntimeError('Unexpected runtime input')
        state = json.loads(subprocess.check_output(
            ['docker', 'inspect', 'pyodide-ddf316c1-' + fixture], text=True, timeout=10))[0]
        if state['State']['Running'] or not (ROOT / 'runs' / fixture / 'receipt.json').is_file():
            raise RuntimeError('Completed fixture evidence missing or provider running')
        archive = path.with_suffix('.mjs.gz')
        if archive.exists():
            raise RuntimeError('Reconcile existing archive before retry')
        records.append((path, archive))
    receipt = []
    for path, archive in records:
        original = path.read_bytes()
        with archive.open('xb') as target:
            target.write(gzip.compress(original, mtime=0))
        if digest(gzip.decompress(archive.read_bytes())) != EXPECTED:
            raise RuntimeError('Archive round trip failed; original preserved')
        path.unlink()
        receipt.append({'archive': str(archive), 'sha256': digest(archive.read_bytes()),
                        'original_sha256': EXPECTED, 'bytes': archive.stat().st_size})
    print(json.dumps({'archives': receipt, 'volumes_deleted': False, 'guard_changed': False}))


if __name__ == '__main__':
    main()
