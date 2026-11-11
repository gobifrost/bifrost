"""Five fixed serial experiments; no scheduling/retry or business-job ownership."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1/run-e39b83eae')
WORKLOADS = ('json_transform', 'http_orchestration', 'integration_read', 'table_update', 'multi_step')
PRIVATE = 'cloud-private-benchmark-v1'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Assigned guest required')
    output = ROOT / f'cloud-benchmark-{time.time_ns()}.jsonl'
    with output.open('x') as records:
        for workload in WORKLOADS:
            command = ['python3', str(ROOT / 'run_cloud_qualification.py'), '--benchmark', workload,
                       '--private-directory', PRIVATE]
            result = subprocess.run(command, capture_output=True, text=True, timeout=210)
            if result.returncode:
                # The private qualification log retains details; do not print
                # arbitrary exception/log text which could contain credentials.
                print(json.dumps({'workload': workload, 'exit_code': result.returncode,
                                  'completed_records': str(output), 'automatic_retry': False}))
                raise RuntimeError('Benchmark stopped; reconcile private log/ledger before another invocation')
            control = json.loads(result.stdout)
            lines = Path(control['log']).read_text().splitlines()
            receipt = json.loads(next(line for line in reversed(lines) if line.startswith('{')))
            if receipt.get('ended_run_status') != 401 or receipt.get('ended_refresh_status') != 401:
                raise RuntimeError('Incomplete credential retirement; benchmark stopped')
            record = {'workload': workload, 'control': control, 'receipt': receipt}
            records.write(json.dumps(record) + '\n')
            records.flush()
            os.fsync(records.fileno())
    print(json.dumps({'workloads': len(WORKLOADS), 'successful_records': str(output),
                      'fresh_python_children': len(WORKLOADS), 'automatic_retries': 0}))


if __name__ == '__main__':
    main()
