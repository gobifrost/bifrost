"""Real artifact pin controls only; no Python Worker or platform request."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest import TestCase

from staged_runtime_input import validate_staged_runtime


def main(source):
    names = ('generated-runtime.mjs', 'runtime-code.mjs', 'sdk-cleanup-code.mjs', 'runtime-provenance.json')
    with tempfile.TemporaryDirectory(prefix='staged-preflight-', dir='/evidence') as directory:
        packet = Path(directory)
        target = packet / 'project/backend'
        target.mkdir(parents=True)
        for name in names:
            shutil.copyfile(source / name, target / name)
        provenance = validate_staged_runtime(packet)
        refused = []
        for name in names:
            path = target / name
            original = path.read_bytes()
            try:
                if name == 'runtime-provenance.json':
                    changed = json.loads(original)
                    changed['sdk_source'] = 'unqualified'
                    path.write_text(json.dumps(changed))
                else:
                    path.write_bytes(original + b'\n')
                with TestCase().assertRaises(RuntimeError):
                    validate_staged_runtime(packet)
                refused.append(name)
            finally:
                path.write_bytes(original)
        assert validate_staged_runtime(packet) == provenance
        report = {'scope': 'actual artifact positive pin and four altered-input refusals before runtime creation',
                  'bundle_sha256': provenance['bundle_sha256'], 'refused': refused, 'cloud_invocations': 0}
        Path('/evidence/preflight.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
