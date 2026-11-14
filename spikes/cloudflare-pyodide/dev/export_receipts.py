"""Export sanitized successful fixture JSON, never the private raw logs."""
import json
from pathlib import Path

root = Path('/opt/bifrost-tests/pyodide-ddf316c1/dev-deploy-2037ac0')
records = []
for path in sorted(root.glob('cloud-qualification*.private.log')):
    for line in path.read_text().splitlines():
        if not line.startswith('{'):
            continue
        value = json.loads(line)
        if value.get('ended_run_status') == 401 and value.get('independent_document_value') == 70:
            records.append({'guest_log': path.name, 'receipt': value})
output = root / 'cloud-sdk-success.json'
output.write_text(json.dumps(records, indent=2) + '\n')
print(json.dumps({'successful_runs': len(records), 'fresh_canary_runs': sum(bool(record['receipt'].get('fresh_run_canaries_clear')) for record in records),
                  'integration_runs': sum(bool(record['receipt'].get('integration_read_secret_redaction_peer_denial_verified')) for record in records)}))
