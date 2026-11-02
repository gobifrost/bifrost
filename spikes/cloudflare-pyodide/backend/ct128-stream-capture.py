"""Reproduce and qualify shared engine stream capture in the owned test stack."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'result-identity-1017cca'
PACKET = ROOT / 'inbox/stream-capture-v1'
SOURCE = '1017cca1795540f75b9781d87137822a4a42d685'


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=30, **kwargs).strip()


def main(after):
    if (out(['hostname']) != 'bifrost-pyodide-test-01'
        or out(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE
        or shutil.disk_usage(ROOT).free < 8 * 1024**3):
        raise RuntimeError('Owned guest/source/disk guard failed')
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    if out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]):
        raise RuntimeError('Owned test project still running')
    run = ROOT / ('runs/stream-capture-after-v2' if after else 'runs/stream-capture-before-v2')
    run.mkdir()
    (run / 'home').mkdir()
    files = {'test_stream_capture.py': 'api/tests/unit/engine/test_stream_capture.py'}
    if after:
        files['engine.py'] = 'api/src/services/execution/engine.py'
    for name, relative in files.items():
        destination = CHECKOUT / relative
        if destination.exists():
            (run / ('previous-' + name)).write_bytes(destination.read_bytes())
        destination.write_bytes((PACKET / name).read_bytes())
    hashes = {relative: hashlib.sha256((CHECKOUT / relative).read_bytes()).hexdigest()
              for relative in [*files.values(), 'api/src/services/execution/engine.py']}
    env = {'PATH': os.environ['PATH'], 'HOME': str(run / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1',
           'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
    selectors = ['tests/unit/engine/test_stream_capture.py']
    if after:
        selectors += ['tests/unit/engine/test_engine_token_redaction.py',
                      'tests/unit/engine/test_execution_contract.py', 'tests/unit/engine/test_runtime_log_lock.py']
    commands = [['./test.sh', 'stack', 'up'], ['./test.sh', *selectors, '-q']]
    if after:
        for checker in ('ruff', 'pyright'):
            commands.append([*compose, '--profile', 'test', 'run', '--rm', '--no-deps', 'test-runner',
                             checker, *(['check'] if checker == 'ruff' else []),
                             'src/services/execution/engine.py', 'tests/unit/engine/test_stream_capture.py'])
    report = {'source': SOURCE, 'overlay_sha256': hashes, 'project': project, 'commands': [],
              'phase': 'after' if after else 'before', 'cloud_invocations': 0, 'qualification_passed': False}
    started = time.monotonic()
    try:
        for index, command in enumerate(commands):
            with (run / f'phase-{index}.log').open('x') as log:
                result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=max(1, 600 - (time.monotonic() - started)))
            report['commands'].append({'argv': command, 'exit_code': result.returncode})
            if result.returncode and (after or index != 1):
                raise RuntimeError('Focused qualification failed; inspect retained log')
        junit = Path('/tmp') / ('bifrost-' + project) / 'test-results.xml'
        shutil.copyfile(junit, run / 'tests.xml')
        tree = ET.parse(junit)
        report['junit'] = {key: len(tree.findall('.//' + tag)) for key, tag in
                           [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
        expected = (report['junit']['cases'] >= 5 and report['junit']['failures'] == 0) if after else (
            report['junit']['cases'] == 5 and report['junit']['failures'] == 4)
        if not expected or report['junit']['errors'] or report['junit']['skipped']:
            raise RuntimeError('Unexpected JUnit outcome')
        report['qualification_passed'] = after
        report['expected_baseline_reproduced'] = not after
    finally:
        subprocess.run([*compose, '--profile', 'e2e', '--profile', 'test', 'stop'],
                       cwd=CHECKOUT, env=env, timeout=60, check=True)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter',
                                               'label=com.docker.compose.project=' + project]).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (run / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


def pyodide():
    if out(['hostname']) != 'bifrost-pyodide-test-01':
        raise RuntimeError('Owned guest required')
    run = ROOT / 'runs/stream-capture-pyodide-v1'
    run.mkdir()
    core = ROOT / 'runs/pyodide-package-qualification-resume/core/pyodide'
    spike = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'
    name = 'pyodide-ddf316c1-stream-capture-v1'
    command = ['docker', 'run', '--name', name, '--network=none', '--cpus=1', '--memory=1g',
               '--memory-swap=1g', '--pids-limit=128', '--mount', f'type=bind,src={run},dst=/qualification',
               '--mount', f'type=bind,src={core},dst=/core,readonly',
               '--mount', f'type=bind,src={spike},dst=/spike,readonly',
               '--mount', f'type=bind,src={PACKET},dst=/patch,readonly', '--workdir', '/qualification',
               '--entrypoint', 'node', 'bifrost-test-api-dev:bifrost-test-7ddfac36',
               '/patch/stream-capture-pyodide.mjs']
    report = {'command': command, 'cloud_invocations': 0, 'sdk_qualified': False,
              'engine_sha256': hashlib.sha256((PACKET / 'engine.py').read_bytes()).hexdigest(),
              'driver_sha256': hashlib.sha256((PACKET / 'stream-capture-pyodide.mjs').read_bytes()).hexdigest()}
    try:
        with (run / 'runtime.log').open('x') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        report['exit_code'] = result.returncode
        if result.returncode:
            raise RuntimeError('Actual Pyodide stream qualification failed')
        report['observed'] = json.loads((run / 'pyodide.json').read_text())
    finally:
        subprocess.run(['docker', 'stop', '-t', '3', name], capture_output=True, timeout=15, check=True)
        report['state_after_cleanup'] = json.loads(out(['docker', 'inspect', name, '--format', '{{json .State}}']))
        (run / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'exit_code': report['exit_code'], 'abi': report['observed']['abi']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--after', action='store_true')
    parser.add_argument('--pyodide', action='store_true')
    args = parser.parse_args()
    if args.pyodide:
        pyodide()
    else:
        main(args.after)
