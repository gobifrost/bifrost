"""Stage the unchanged, lock-pinned pure wheel; no install or native retagging."""
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

overlay_path, wheel_path = map(Path, sys.argv[1:])
wheel = wheel_path.read_bytes()
digest = hashlib.sha256(wheel).hexdigest()
if digest != '3cb2210c8f88ba2318d29b0388d1023c8492ff72ecdde4ebdaddbb13a31b1c4a':
    raise RuntimeError('Exact public certifi wheel digest required')
source = overlay_path.read_text(encoding='utf-8-sig').strip()
overlay = json.loads(source.removeprefix('export default ').removesuffix(';'))
with zipfile.ZipFile(wheel_path) as archive:
    metadata = archive.read('certifi-2026.4.22.dist-info/WHEEL').decode()
    if 'Root-Is-Purelib: true' not in metadata or 'Tag: py3-none-any' not in metadata:
        raise RuntimeError('Expected pure Python wheel')
    for name in archive.namelist():
        if name.startswith('certifi/') and (name.endswith('.py') or name == 'certifi/cacert.pem'):
            content = archive.read(name)
            overlay['files'][name] = ({'py': content.decode()} if name.endswith('.py') else
                {'base64': base64.b64encode(content).decode()})
            overlay['sha256'][name] = hashlib.sha256(content).hexdigest()
overlay['dependency'] = {'name': 'certifi', 'version': '2026.4.22', 'wheel_sha256': digest,
    'abi': 'py3-none-any', 'patches': []}
overlay_path.write_text('export default ' + json.dumps(overlay) + ';\n')
