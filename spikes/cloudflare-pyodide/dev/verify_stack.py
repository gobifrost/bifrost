"""Verify dev identity/health without emitting credentials."""
import json
from pathlib import Path
import subprocess
import urllib.request

root = Path('/opt/bifrost-tests/pyodide-ddf316c1/dev-deploy-2037ac0')
expected = json.loads((root / 'dev-access.json').read_text())
raw = subprocess.check_output(['docker', 'inspect', 'bifrost-debug-074217d4-api-1', '--format', '{{json .Config.Env}}'], text=True)
env = dict(value.split('=', 1) for value in json.loads(raw))
password = env.get('BIFROST_DEFAULT_USER_PASSWORD', '')
strong = len(password) > 32 and password == expected['password'] and password != 'password'
if not strong:
    raise RuntimeError('Dev credential preflight failed; public exposure refused')
with urllib.request.urlopen('http://127.0.0.1:32767/health/ready', timeout=10) as response:
    status = response.status
print(json.dumps({'strong_dev_password': strong, 'api_ready_http_status': status, 'source': '2037ac0'}))
