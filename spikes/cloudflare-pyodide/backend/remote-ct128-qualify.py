"""One bounded owner qualification, executed through existing pct exec 128.

No guest provisioning, credential transfer, cloud calls, or automatic retries.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = '4d493d5547a6864cc6337df9144cdf9366652915'
BASE = '0c3241789a15e418d02ca8eb6fbd8fac8016edef'
RUN = ROOT / 'runs' / ('owner-run68-v2-' + SOURCE[:12])
CHECKOUT = ROOT / SOURCE
REPO = ROOT / 'sources/bifrost-upstream.git'
OWNER = '6e1d35f9-0847-466a-a570-f72fd16f9ed7'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'
SELECTORS = ['tests/unit/dev/test_pyodide_attempt_journal.py',
             'tests/unit/worker/remote/test_launcher.py',
             'tests/unit/worker/remote/test_ownership.py',
             'tests/unit/worker/remote/test_fencing.py']


def command(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main():
    binding = json.loads((ROOT / 'owner-binding.json').read_text())
    assert binding['owner_thread_id'] == OWNER and binding['vmid'] == 128
    assert command(['hostname']) == 'bifrost-pyodide-test-01'
    assert not command(['docker', 'ps', '-q']), 'Guest has active containers; reconcile first'
    assert shutil.disk_usage(ROOT).free > 20 * 1024**3
    RUN.mkdir()  # Exclusive reservation: never replay an uncertain invocation.
    (RUN / 'home').mkdir()
    report = {'owner': OWNER, 'vmid': 128, 'source': SOURCE, 'commands': [],
              'cloud_invocations': 0, 'runtime_acceptance': False,
              'wall_limit_seconds': 900, 'disk_used_guard_bytes': 24 * 1024**3,
              'hard_guest_limits': {'cpus': 2, 'memory_mib': 4096, 'swap': 0},
              'started_at_epoch': time.time(), 'remote_test_executed': False}
    project = None
    started = time.monotonic()
    try:
        command(['git', '--git-dir', str(REPO), 'fetch',
                 'https://github.com/MTG-Thomas/bifrost-upstream.git', SOURCE])
        changes = command(['git', '--git-dir', str(REPO), 'diff', '--name-only', BASE, SOURCE]).splitlines()
        assert all(p.startswith(('spikes/cloudflare-pyodide/', 'api/tests/')) for p in changes), changes
        if not CHECKOUT.exists():
            command(['git', '--git-dir', str(REPO), 'worktree', 'add', '--detach', str(CHECKOUT), SOURCE])
        assert command(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) == SOURCE
        assert not command(['git', 'status', '--porcelain'], cwd=CHECKOUT)
        report['staged_patch_sha256'] = hashlib.sha256(b'').hexdigest()
        report['unstaged_patch_sha256'] = hashlib.sha256(b'').hexdigest()
        report['required_untracked_files'] = []
        report['tree'] = command(['git', 'rev-parse', 'HEAD^{tree}'], cwd=CHECKOUT)
        report['image'] = json.loads(command(['docker', 'image', 'inspect', IMAGE]))[0]['Id']
        pins = json.loads((CHECKOUT / 'spikes/cloudflare-pyodide/backend/remote-test-image-pins.json').read_text())
        report['sidecar_pin_manifest'] = pins
        # Verify every immutable sidecar digest is present; Compose aliases must
        # resolve to the same image, not simply to a similarly named tag.
        staged = json.loads((ROOT / 'receipts/pinned-sidecars-stage.json').read_text())
        for item in staged:
            actual = json.loads(command(['docker', 'image', 'inspect', item['tag']]))[0]
            assert actual['Id'] == item['image_id']
            assert item['pinned_digest'] in actual['RepoDigests']
        report['sidecars_verified'] = staged
        project = command(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
        report['project'] = project
        report['fixture_sha256'] = {p: hashlib.sha256((CHECKOUT / 'api' / p).read_bytes()).hexdigest()
                                    for p in SELECTORS}
        env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'),
               'BIFROST_SKIP_BUILD': '1', 'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36',
               'BIFROST_TEST_UNIT_ONLY': '1'}
        for index, args in enumerate([['./test.sh', 'stack', 'up'], ['./test.sh', *SELECTORS, '-q']]):
            entry = {'argv': args, 'started_at_epoch': time.time()}
            report['commands'].append(entry)
            if index == 1:
                report['remote_test_executed'] = True
            with (RUN / f'phase-{index}.log').open('x') as output:
                process = subprocess.Popen(args, cwd=CHECKOUT, env=env, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                while process.poll() is None:
                    with (RUN / 'memory-samples.jsonl').open('a') as samples:
                        samples.write(json.dumps({'at_epoch': time.time(),
                            'meminfo': Path('/proc/meminfo').read_text()}) + '\n')
                    if time.monotonic() - started > 900 or shutil.disk_usage(ROOT).used > 24 * 1024**3:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(10)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                        raise RuntimeError('Wall/disk guard stopped the owned command')
                    time.sleep(1)
                entry['exit_code'] = process.returncode
            entry['finished_at_epoch'] = time.time()
            assert entry['exit_code'] == 0, entry
        junit = Path('/tmp') / ('bifrost-' + project) / 'test-results.xml'
        shutil.copyfile(junit, RUN / 'test-results.xml')
        tree = ET.parse(junit)
        cases = tree.findall('.//testcase')
        report['junit'] = {'cases': len(cases), 'failures': len(tree.findall('.//failure')),
                           'errors': len(tree.findall('.//error')), 'skipped': len(tree.findall('.//skipped'))}
        assert len(cases) == 40 and not any(report['junit'][k] for k in ('failures', 'errors', 'skipped'))
        report['qualification_passed'] = True
    except Exception as error:
        report['qualification_passed'] = False
        report['error_type'] = type(error).__name__
        raise
    finally:
        if project:
            ids = command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
            report['owned_container_ids'] = ids
            if ids:
                report['cleanup_stop_exit'] = subprocess.call(['docker', 'stop', '-t', '10', *ids], stdout=subprocess.DEVNULL)
            report['running_after_cleanup'] = command(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]).splitlines()
            report['volumes_deleted'] = False
        report['elapsed_seconds'] = time.monotonic() - started
        report['finished_at_epoch'] = time.time()
        report['artifacts'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in RUN.iterdir() if p.is_file()}
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
