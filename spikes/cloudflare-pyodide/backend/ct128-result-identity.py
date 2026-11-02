"""Bounded source-regression qualification; no cloud/provider workflow runs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = '1017cca1795540f75b9781d87137822a4a42d685'
CHECKOUT = ROOT / 'result-identity-1017cca'
RUN = ROOT / 'runs/result-identity-v1'
PACKET = ROOT / 'inbox/result-identity-v1'
TEST = 'api/tests/unit/execution/test_result_transport_identity.py'


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main():
    binding = json.loads((ROOT / 'owner-binding.json').read_text())
    if binding['owner_thread_id'] != '6e1d35f9-0847-466a-a570-f72fd16f9ed7' or binding['vmid'] != 128:
        raise RuntimeError('Assigned guest required')
    if out(['hostname']) != 'bifrost-pyodide-test-01' or shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError('Guest identity/free disk preflight failed')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    repo = ROOT / 'sources/bifrost-upstream.git'
    out(['git', '--git-dir', str(repo), 'fetch', str(PACKET / 'source.bundle'), SOURCE])
    out(['git', '--git-dir', str(repo), 'worktree', 'add', '--detach', str(CHECKOUT), SOURCE])
    fixture = (PACKET / Path(TEST).name).read_bytes()
    (CHECKOUT / TEST).write_bytes(fixture)
    compose_file = CHECKOUT / 'docker-compose.test.yml'
    original = compose_file.read_text()
    limits = {'init': '512m', 'api': '1g', 'worker': '512m', 'test-runner': '768m',
              'postgres': '512m', 'pgbouncer': '128m', 'redis': '128m', 'seaweedfs': '256m'}
    for service, memory in limits.items():
        original = re.sub(r'^  ' + re.escape(service) + r':$',
                          f'  {service}:\n    cpus: 1\n    mem_limit: {memory}\n    memswap_limit: {memory}', original, flags=re.M)
    compose_file.write_text(original)
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1',
           'BIFROST_TEST_USE_CLEAN_BOOT': '1', 'COMPOSE_PARALLEL_LIMIT': '1'}
    report = {'source': SOURCE, 'fixture_sha256': hashlib.sha256(fixture).hexdigest(),
              'compose_sha256': hashlib.sha256(compose_file.read_bytes()).hexdigest(),
              'project': project, 'commands': [], 'cloud_invocations': 0,
              'scope': 'real process-pool metadata/remote callback with transport doubles; no physical execution'}
    started = time.monotonic()
    try:
        for index, command in enumerate([['./test.sh', 'stack', 'up'],
                                         ['./test.sh', TEST.removeprefix('api/'), '-q']]):
            with (RUN / f'phase-{index}.log').open('x') as log:
                result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=max(1, 600 - (time.monotonic() - started)))
            report['commands'].append({'argv': command, 'exit_code': result.returncode})
            if result.returncode and index == 0:
                raise RuntimeError('Stack preflight failed')
        junit = Path('/tmp') / ('bifrost-' + project) / 'test-results.xml'
        shutil.copyfile(junit, RUN / 'before.xml')
        tree = ET.parse(junit)
        report['junit'] = {key: len(tree.findall('.//' + tag)) for key, tag in
                           [('cases', 'testcase'), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
        if report['junit'] != {'cases': 9, 'failures': 6, 'errors': 0, 'skipped': 0}:
            raise RuntimeError('Baseline no longer reproduces the expected identity gap')
        report['expected_baseline_failure_reproduced'] = True
    finally:
        # Stop only this newly-created test project; preserve its volumes and
        # every dev stack container. Never delete uncertain locks.
        subprocess.run(['docker', 'compose', '-p', project, '-f', str(compose_file), '--profile', 'e2e',
                        '--profile', 'test', 'stop'], cwd=CHECKOUT, env=env, timeout=60, check=True)
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
