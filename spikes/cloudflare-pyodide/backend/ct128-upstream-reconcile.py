"""Qualify current upstream merge without changing the existing dev deployment."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = '1d33150eb376b71f8a81b427920eda1b0447f829'
UPSTREAM = '025f6c019928d8be78bfe47f1cb62702b4ad2226'
CHECKOUT = ROOT / 'upstream-025f6c0'
RUN = ROOT / 'runs/upstream-025f6c0-v2'
PACKET = ROOT / 'inbox/upstream-025f6c0-v1'


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main(resume):
    binding = json.loads((ROOT / 'owner-binding.json').read_text())
    if (out(['hostname']) != 'bifrost-pyodide-test-01' or binding['vmid'] != 128
        or binding['owner_thread_id'] != '6e1d35f9-0847-466a-a570-f72fd16f9ed7'
        or shutil.disk_usage(ROOT).free < 8 * 1024**3):
        raise RuntimeError('Assigned guest/disk preflight failed')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    repo = ROOT / 'sources/bifrost-upstream.git'
    if resume:
        if (out(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE
            or out(['git', 'diff', '--name-only'], cwd=CHECKOUT) != 'docker-compose.test.yml'):
            raise RuntimeError('Unexpected prior source state')
    else:
        out(['git', '--git-dir', str(repo), 'fetch', str(PACKET / 'source.bundle'), SOURCE])
        out(['git', '--git-dir', str(repo), 'worktree', 'add', '--detach', str(CHECKOUT), SOURCE])
    # The cached image remains valid only when its Python dependency inputs match.
    for path in ('pyproject.toml', 'requirements.lock'):
        original = out(['git', 'show', '1017cca1795540f75b9781d87137822a4a42d685:' + path], cwd=CHECKOUT)
        if (CHECKOUT / path).read_text().strip() != original:
            raise RuntimeError('Cached image dependency input changed')
    compose_file = CHECKOUT / 'docker-compose.test.yml'
    compose = out(['git', 'show', SOURCE + ':docker-compose.test.yml'], cwd=CHECKOUT) + '\n'
    limits = {'init': '512m', 'api': '1g', 'worker': '512m', 'scheduler': '384m',
              'scheduler-fixtures': '384m', 'test-runner': '768m', 'postgres': '512m',
              'pgbouncer': '128m', 'redis': '128m', 'seaweedfs': '256m'}
    blocks = re.split(r'(?m)(?=^  [a-zA-Z0-9_-]+:$)', compose)
    for index, block in enumerate(blocks):
        header = re.match(r'^  ([a-zA-Z0-9_-]+):\n', block)
        if header and header[1] in limits:
            memory = limits[header[1]]
            block = re.sub(r'^    (?:cpus|mem_limit|memswap_limit):[^\n]*\n', '', block, flags=re.M)
            blocks[index] = block.replace(header[0], header[0] +
                f'    cpus: 1\n    mem_limit: {memory}\n    memswap_limit: {memory}\n', 1)
    compose = ''.join(blocks)
    compose_file.write_text(compose)
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    if out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]):
        raise RuntimeError('Owned project has a live process')
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1',
           'BIFROST_TEST_USE_CLEAN_BOOT': '1', 'COMPOSE_PARALLEL_LIMIT': '1'}
    selectors = ['tests/unit/dev/test_pyodide_attempt_journal.py', 'tests/unit/dev/test_thin_remote_pool.py',
                 'tests/unit/worker/remote/test_protocol.py', 'tests/unit/worker/remote/test_credential_protocol.py',
                 'tests/unit/services/worker_pool/test_external_capacity.py',
                 'tests/unit/services/worker_pool/test_external_policy.py',
                 'tests/unit/execution/test_result_transport_identity.py',
                 'tests/unit/engine/test_stream_capture.py', 'tests/unit/engine/test_engine_token_redaction.py']
    report = {'source': SOURCE, 'upstream': UPSTREAM, 'project': project, 'cloud_invocations': 0,
              'compose_sha256': hashlib.sha256(compose_file.read_bytes()).hexdigest(),
              'commands': [], 'qualification_passed': False}
    started = time.monotonic()
    try:
        for index, command in enumerate([
            ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml', 'config', '--quiet'],
            ['./test.sh', 'stack', 'up'], ['./test.sh', *selectors, '-q']]):
            with (RUN / f'phase-{index}.log').open('x') as log:
                result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=max(1, 600 - (time.monotonic() - started)))
            report['commands'].append({'argv': command, 'exit_code': result.returncode})
            if result.returncode:
                raise RuntimeError('Merged-source qualification failed; inspect retained log')
        junit = Path('/tmp') / ('bifrost-' + project) / 'test-results.xml'
        shutil.copyfile(junit, RUN / 'tests.xml')
        tree = ET.parse(junit)
        report['junit'] = {key: len(tree.findall('.//' + tag)) for key, tag in
                           [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
        if report['junit']['cases'] < 80 or any(report['junit'][key] for key in ('failures', 'errors', 'skipped')):
            raise RuntimeError('Unexpected JUnit outcome')
        report['qualification_passed'] = True
    finally:
        subprocess.run(['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml',
                        '--profile', 'e2e', '--profile', 'test', 'stop'], cwd=CHECKOUT, env=env, timeout=60, check=True)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter',
                                               'label=com.docker.compose.project=' + project]).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume-preflight', action='store_true')
    main(parser.parse_args().resume_preflight)
