"""Sanitized local SDK qualification evidence; no logs, tokens or result bodies."""
import hashlib
import json
from pathlib import Path

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
raw = (ROOT / 'runs/engine-context-sdk-v1/receipt.json').read_bytes()
receipt = json.loads(raw)
probe = receipt.get('shared_pool_result', {})
result = {key: receipt.get(key) for key in ('api_source', 'runtime_engine_source', 'cloud_invocations',
    'physical_termination_verified', 'qualification_passed', 'running_after_cleanup', 'elapsed_seconds')}
result.update(raw_receipt_sha256=hashlib.sha256(raw).hexdigest(),
    command_exit_codes=[item['exit_code'] for item in receipt.get('commands', [])],
    execution_overlay=receipt['staged_runtime_provenance']['execution_overlay'],
    packet_sha256=receipt['packet_sha256'],
    contract_observations={key: value for key, value in probe.items() if isinstance(value, bool)},
    launch_counts=list(probe.get('launches', {}).values()), native_busy_counts=probe['native_busy_counts'],
    provider_oom=receipt['provider_state_before_cleanup']['OOMKilled'])
result['attempts'] = [{'label': slot['label'], 'canonical_status': slot['canonical_status'],
    'access_after_terminal_status': slot['access_after_terminal_status'],
    'persisted_log_count': len(slot['persisted_logs']),
    'sdk_close_returned': slot['evidence']['runtime_cleanup']['sdk_close_returned'],
    'runtime_identity_context': {key: slot['evidence']['result']['execution_context'][key] for key in (
        'is_agent', 'execution_origin', 'agent_id', 'original_user_is_platform_admin', 'original_user_id')},
    'packages': slot['preparation']['packages']}
    for slot in probe['attempts'].values()]
print(json.dumps(result, indent=2))
