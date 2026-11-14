"""Focused static checks after the owned guest contract run has stopped."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'upstream-025f6c0'
RUN = ROOT / 'runs/ack-uncertainty-quality'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Assigned guest required')
    prior = json.loads((ROOT / 'runs/ack-uncertainty-after/receipt.json').read_text())
    project = prior['project']
    if prior['junit']['failures'] or prior['running_after_cleanup']:
        raise RuntimeError('Passing stopped contract run required')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'),
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml', '--profile', 'test',
               'run', '--rm', '--no-deps', 'test-runner']
    files = ['src/worker/remote/remote_pool.py', 'tests/unit/dev/test_pyodide_remote_ack_boundary.py',
             'tests/unit/dev/test_pyodide_remote_boundary.py']
    report = {'cloud_invocations': 0, 'commands': []}
    for index, command in enumerate([[*compose, 'ruff', 'check', *files], [*compose, 'pyright', *files]]):
        with (RUN / f'phase-{index}.log').open('x') as log:
            result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        report['commands'].append({'argv': command, 'exit_code': result.returncode})
    (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    if any(item['exit_code'] for item in report['commands']):
        raise RuntimeError('Focused static checks failed; inspect retained logs')


if __name__ == '__main__':
    main()
