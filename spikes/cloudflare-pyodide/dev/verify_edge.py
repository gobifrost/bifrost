"""Guest-only authenticated readback; never emit credentials or tokens."""
import json
import hashlib
from pathlib import Path
import urllib.parse
import urllib.request
import urllib.error

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1/dev-deploy-2037ac0')
EDGE = 'https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev'
USER_AGENT = 'bifrost-pyodide-dev-qualification/1.0'
password = json.loads((ROOT / 'dev-access.json').read_text())['password']
body = urllib.parse.urlencode({'username': 'dev@gobifrost.com', 'password': password}).encode()
request = urllib.request.Request(EDGE + '/auth/login', data=body, headers={'User-Agent': USER_AGENT})
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        login = json.load(response)
except urllib.error.HTTPError as error:
    body = error.read(4096)
    print(json.dumps({'login_status': error.code, 'server': error.headers.get('Server'),
                      'cloudflare_error_1010': b'1010' in body,
                      'cloudflare_error_1020': b'1020' in body,
                      'body_sha256': hashlib.sha256(body).hexdigest(),
                      'content_type': error.headers.get('Content-Type')}))
    raise SystemExit(1)
token = login.get('access_token')
if not token:
    raise RuntimeError('Dev login did not issue an access token')
reads = {}
for path in ['/api/workflows', '/api/tables', '/api/forms', '/api/agents', '/api/integrations']:
    request = urllib.request.Request(EDGE + path, headers={'Authorization': 'Bearer ' + token, 'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
        reads[path] = {'status': response.status, 'response_type': type(payload).__name__}
receipt = {'source': '2037ac0d9ec95e94a77ce440001ae016d4215407',
           'edge': EDGE, 'authenticated_reads': reads,
           'user_agent': USER_AGENT,
           'scope': 'Cloudflare static UI and API proxy; upstream API executes on CT128'}
(ROOT / 'edge-auth-readback.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
