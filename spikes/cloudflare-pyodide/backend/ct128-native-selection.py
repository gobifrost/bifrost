"""Observe pinned workerd bundle selection without external network access."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SPIKE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'
RUN = ROOT / 'runs/native-bundle-selection-resume'
NAME = 'pyodide-ddf316c1-native-selection'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Wrong guest')
    if subprocess.check_output(['docker', 'ps', '-q'], text=True).strip():
        raise RuntimeError('Reconcile active containers first')
    RUN.mkdir()
    config = '''using Workerd = import "/workerd/workerd.capnp";
const config :Workerd.Config = (
 services = [(name = "main", worker = .mainWorker)],
 sockets = [(name = "http", address = "127.0.0.1:8812", http = (), service = "main")],
);
const mainWorker :Workerd.Worker = (
 modules = [(name = "main.py", pythonModule = "import sys\\n")],
 compatibilityDate = "2026-10-30", compatibilityFlags = ["python_workers"],
);
'''
    (RUN / 'selection.capnp').write_text(config)
    args = ['docker', 'run', '--name', NAME, '--network=none', '--cpus=0.5',
            '--memory=512m', '--memory-swap=512m', '--pids-limit=64',
            '--mount', f'type=bind,src={SPIKE},dst=/spike,readonly',
            '--mount', f'type=bind,src={RUN},dst=/selection',
            '--entrypoint', '/spike/node_modules/@cloudflare/workerd-linux-64/bin/workerd',
            'bifrost-test-api-dev:bifrost-test-7ddfac36', 'serve', '--verbose',
            '-I', '/spike/node_modules', '/selection/selection.capnp']
    report = {'command': args, 'network': 'none', 'cloud_invocations': 0,
              'started_at_epoch': time.time(), 'physical_termination_verified': False}
    try:
        with (RUN / 'native.log').open('x') as log:
            process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
            try:
                report['exit_code'] = process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                report['observation_timeout'] = True
    finally:
        subprocess.run(['docker', 'stop', '-t', '3', NAME], capture_output=True)
        text = (RUN / 'native.log').read_text()
        report['selected_urls'] = re.findall(r'https://pyodide-capnp-bin\.edgeworker\.net/[^\s";]+', text)
        report['log_sha256'] = hashlib.sha256((RUN / 'native.log').read_bytes()).hexdigest()
        report['finished_at_epoch'] = time.time()
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
        print(text[-3000:])


if __name__ == '__main__':
    main()
