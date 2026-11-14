"""Sanitized deployed qualification summary; retain private raw receipts on guest."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
RUN = ROOT / 'runs/engine-context-cloud-v1'
raw = (RUN / 'qualification.private.log').read_bytes()
records = [json.loads(line) for line in raw.decode().splitlines() if line.startswith('{')]
probe = next(row for row in records if row.get('shared_native_pool_probe'))
inventory = json.loads((RUN / 'private/response-1.bin').read_text())
build = json.loads((ROOT / 'runs/engine-context-cloud-build-v1/receipt.json').read_text())
result = {'passed': probe['fixture_worker_cascade_cleanup_verified'],
    'raw_log_sha256': hashlib.sha256(raw).hexdigest(),
    'cloud_child_requests': json.loads((RUN / 'private/relay-receipt.json').read_text())['child_requests'],
    'physical_termination_verified': False, 'execution_overlay': build['execution_overlay'],
    'candidate_files_sha256': build['files_sha256'],
    'runtime_inventory': {key: inventory[key] for key in ('runtime', 'packages', 'package_environment')},
    'contract_observations': {key: value for key, value in probe.items() if isinstance(value, bool)},
    'launch_counts': list(probe['launches'].values()), 'native_busy_counts': probe['native_busy_counts'],
    'attempts': [{'label': slot['label'], 'canonical_status': slot['canonical_status'],
        'access_after_terminal_status': slot['access_after_terminal_status'],
        'persisted_log_count': len(slot['persisted_logs']),
        'sdk_close_returned': slot['evidence']['runtime_cleanup']['sdk_close_returned'],
        'runtime_identity_context': {key: slot['evidence']['result']['execution_context'][key] for key in (
            'is_agent', 'execution_origin', 'agent_id', 'original_user_is_platform_admin', 'original_user_id')}}
        for slot in probe['attempts'].values()],
    'fixture_counter_note': 'Original fixture cloud_invocations=0 is local instrumentation; actual count comes from cloud relay.',
    'responses': []}
for path in sorted((RUN / 'private').glob('response-*.json')):
    result['responses'].append({**json.loads(path.read_text()),
        'sha256': hashlib.sha256(path.with_suffix('.bin').read_bytes()).hexdigest()})
result['owned_test_running_after_cleanup'] = subprocess.check_output(['docker', 'ps', '-q', '--filter',
    'label=com.docker.compose.project=bifrost-test-5dc8d94e'], text=True).strip()
result['relay_running_after_cleanup'] = subprocess.check_output(['docker', 'ps', '-q', '--filter',
    'name=pyodide-engine-cloud-relay-v1'], text=True).strip()
print(json.dumps(result, indent=2))
