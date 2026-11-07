"""Export only bounded non-secret acceptance fields from owned local receipts."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
revision = int(sys.argv[1])
if revision not in (2, 3, 4):
    raise ValueError('Fixed owned run required')
raw = (ROOT / f'runs/staged-runtime-v{revision}/receipt.json').read_bytes()
receipt = json.loads(raw)
result = {key: receipt.get(key) for key in ('api_source', 'runtime_engine_source', 'cloud_invocations',
    'physical_termination_verified', 'qualification_passed', 'running_after_cleanup', 'elapsed_seconds')}
result['raw_receipt_sha256'] = hashlib.sha256(raw).hexdigest()
result['command_exit_codes'] = [item['exit_code'] for item in receipt.get('commands', [])]
probe = receipt.get('shared_pool_result', {})
result['contract_observations'] = {key: value for key, value in probe.items() if isinstance(value, bool)}
result['launch_counts'] = list(probe.get('launches', {}).values())
result['attempts'] = [{'label': item['label'], 'canonical_status': item['canonical_status'],
    'access_after_terminal_status': item['access_after_terminal_status'],
    'sdk_close_returned': item.get('evidence', {}).get('runtime_cleanup', {}).get('sdk_close_returned')}
    for item in probe.get('attempts', {}).values()]
result['provider_oom'] = receipt.get('provider_state_before_cleanup', {}).get('OOMKilled')
print(json.dumps(result, indent=2))
