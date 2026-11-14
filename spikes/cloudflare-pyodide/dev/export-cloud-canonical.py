"""Export selected assertions and digests, never raw runtime logs or credentials."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')


def main():
    reports = []
    for revision in (1, 2, 3, 4):
        run = ROOT / f'runs/cloud-canonical-v{revision}'
        log = (run / 'qualification.private.log').read_bytes()
        relay = run / 'private/relay-receipt.json'
        report = {'revision': revision, 'log_sha256': hashlib.sha256(log).hexdigest(),
                  'child_requests': json.loads(relay.read_text())['child_requests'] if relay.exists() else 0,
                  'physical_termination_verified': False, 'responses': []}
        for path in sorted((run / 'private').glob('response-*.json')):
            metadata = json.loads(path.read_text())
            raw = path.with_suffix('.bin').read_bytes()
            records = [json.loads(line) for line in raw.splitlines()]
            evidence = records[-1].get('evidence', {})
            result = evidence.get('result', {})
            report['responses'].append({**metadata, 'sha256': hashlib.sha256(raw).hexdigest(),
                'success': result.get('success'), 'error_type': result.get('error_type'),
                'sdk_close_returned': evidence.get('runtime_cleanup', {}).get('sdk_close_returned')})
        records = [json.loads(line) for line in log.decode().splitlines() if line.startswith('{')]
        probes = [row for row in records if row.get('shared_native_pool_probe')]
        report['passed'] = len(probes) == 1 and probes[0].get('fixture_worker_cascade_cleanup_verified') is True
        if report['passed']:
            probe = probes[0]
            report['contract_observations'] = {key: value for key, value in probe.items() if isinstance(value, bool)}
            report['launch_counts'] = list(probe['launches'].values())
            report['native_busy_counts'] = probe['native_busy_counts']
            report['attempts'] = [{'label': slot['label'], 'canonical_status': slot['canonical_status'],
                'access_after_terminal_status': slot['access_after_terminal_status'],
                'persisted_log_count': len(slot['persisted_logs']),
                'fresh': slot.get('canonical_result', {}).get('fresh') if isinstance(slot.get('canonical_result'), dict) else None,
                'sdk_close_returned': slot['evidence']['runtime_cleanup']['sdk_close_returned']}
                for slot in probe['attempts'].values()]
        reports.append(report)
    owned = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=bifrost-test-5dc8d94e'], text=True).strip()
    relays = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'name=pyodide-cloud-relay-'], text=True).strip()
    inventory = json.loads((ROOT / 'runs/cloud-canonical-v4/private/response-1.bin').read_text())
    provenance = json.loads((ROOT / 'runs/cloud-canonical-build-v2/project/candidate-provenance.json').read_text())
    print(json.dumps({'runs': reports, 'total_child_requests': sum(row['child_requests'] for row in reports),
        'runtime_inventory': {key: inventory[key] for key in ('runtime', 'packages', 'package_environment')},
        'candidate_files_sha256': provenance['files_sha256'],
        'owned_test_running_after_cleanup': owned, 'relay_running_after_cleanup': relays,
        'api_source': '1d33150eb376b71f8a81b427920eda1b0447f829',
        'engine_source': 'f49d4ba99cebd24aa497f1244833b940e640885f',
        'sdk_source': '139596497d57b219ddcc3616aa66ec58e1b3cf3c',
        'fixture_counter_note': 'Reused native fixture reports cloud_invocations=0; actual cloud request counts are supplied by the relay above.'}, indent=2))


if __name__ == '__main__':
    main()
