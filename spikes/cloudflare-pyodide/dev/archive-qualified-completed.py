"""Bounded archival of exact duplicate inputs from stopped completed fixtures."""
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')


def main():
    runs_only = sys.argv[1:] == ['--completed-runs']
    if sys.argv[1:] and not runs_only:
        raise RuntimeError('Unknown archival mode')
    receipt = ROOT / ('runs/archive-qualified-v2' if runs_only else 'runs/archive-qualified-v1')
    if receipt.exists():
        raise RuntimeError('Reconcile existing archival receipt before retry')
    helper = runpy.run_path(str(ROOT / 'inbox/archive-completed-runtime-v1.py'))
    stopped = set(subprocess.check_output(['docker', 'ps', '-a', '--filter', 'status=exited',
                                         '--format', '{{.Names}}'], text=True, timeout=20).splitlines())
    fixtures = []
    if runs_only:
        prior = json.loads((ROOT / 'runs/archive-qualified-v1/receipt.json').read_text())
        fixtures = [Path(item['archive']).relative_to(ROOT / 'inbox').parts[0] for item in prior['archives']]
        if any('pyodide-ddf316c1-' + fixture not in stopped for fixture in fixtures):
            raise RuntimeError('Completed provider not stopped')
    for path in sorted((ROOT / 'inbox').glob('*/project/backend/generated-runtime.mjs')):
        if runs_only:
            break
        fixture = path.relative_to(ROOT / 'inbox').parts[0]
        if ('pyodide-ddf316c1-' + fixture not in stopped
            or not (ROOT / 'runs' / fixture / 'receipt.json').is_file()
            or path.with_suffix('.mjs.gz').exists()):
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() == helper['EXPECTED']:
            fixtures.append(fixture)
        if len(fixtures) == 40:
            break
    if not fixtures:
        raise RuntimeError('No qualified duplicate inputs')
    receipt.mkdir()
    # Existing helper rechecks every exact path, hash and provider before writes.
    helper['main'].__globals__['FIXTURES'] = tuple(fixtures)
    output = io.StringIO()
    if not runs_only:
        with contextlib.redirect_stdout(output):
            helper['main']()
    result = {'archives': [], 'volumes_deleted': False, 'guard_changed': False} if runs_only else json.loads(output.getvalue())
    # Completed providers also retained an exact working copy under runs/.
    for fixture in fixtures:
        path = ROOT / 'runs' / fixture / 'project/backend/generated-runtime.mjs'
        archive = path.with_suffix('.mjs.gz')
        if not path.is_file() or archive.exists():
            continue
        if path.is_symlink() or path.resolve() != path:
            raise RuntimeError('Uncertain completed-run path')
        original = path.read_bytes()
        if hashlib.sha256(original).hexdigest() != helper['EXPECTED']:
            continue
        with archive.open('xb') as target:
            target.write(gzip.compress(original, mtime=0))
        if hashlib.sha256(gzip.decompress(archive.read_bytes())).hexdigest() != helper['EXPECTED']:
            raise RuntimeError('Archive verification failed; original retained')
        path.unlink()
        result['archives'].append({'archive': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                                   'original_sha256': helper['EXPECTED'], 'bytes': archive.stat().st_size})
    (receipt / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'archives': len(result['archives']), 'receipt': str(receipt / 'receipt.json')}))


if __name__ == '__main__':
    main()
