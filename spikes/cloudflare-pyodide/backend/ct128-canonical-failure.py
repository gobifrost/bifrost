"""Current canonical failure/result proof; local workerd is not deployed Cloudflare."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'upstream-025f6c0'
SOURCE = '1d33150eb376b71f8a81b427920eda1b0447f829'
PACKET = ROOT / 'inbox/canonical-failure-v1'
RUN = ROOT / 'runs/canonical-failure-v4'
OLD_SPIKE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'
NAME = 'pyodide-ddf316c1-canonical-failure-v4'


def out(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main(*, egress_gate=False, deadline_response_loss=False, deadline_retained=False, physical_pool=False, stream_pool=False, stream_loss=False, stream_interruption=False, automatic_loss=False, attempt_deadline=False, late_settlement=False, http_component=False, request_mux=False, shared_pool=False, private_pool=False, api_prepare=False, source_change=False, auto_delivery=False, late_adapter=False, retained_http=False, deadline_recovery=False, startup_inventory=False, oversized_result=False, numeric_result=False, oversized_body=False, memory_pressure=False, refresh_redaction=False, sdk_update=False, sdk_refresh=False, staged_runtime=False, https_fixture=False, https_deadline=False, capability_revocation=False, revision=1):
    global PACKET, RUN, NAME
    staged_runtime = staged_runtime or capability_revocation
    https_fixture = https_fixture or https_deadline
    late_adapter = late_adapter or https_deadline
    staged_runtime = staged_runtime or https_fixture
    if staged_runtime and (sdk_update or sdk_refresh):
        raise ValueError('One explicit runtime artifact profile required')
    sdk_update = sdk_update or sdk_refresh
    refresh_redaction = refresh_redaction or sdk_refresh
    api_prepare = api_prepare or source_change or auto_delivery or late_adapter or retained_http or deadline_recovery or startup_inventory or oversized_result or numeric_result or oversized_body or memory_pressure or refresh_redaction or sdk_update or staged_runtime
    private_pool = private_pool or api_prepare
    shared_pool = shared_pool or private_pool
    http_component = http_component or request_mux
    late_settlement = late_settlement or http_component
    stream_interruption = stream_interruption or automatic_loss or attempt_deadline
    physical_pool = physical_pool or stream_pool or stream_loss or stream_interruption or late_settlement or shared_pool
    if egress_gate or deadline_response_loss or deadline_retained or physical_pool:
        prefix = ('capability-revocation' if capability_revocation else 'https-deadline' if https_deadline else 'canonical-https' if https_fixture else 'staged-runtime' if staged_runtime else 'sdk-refresh' if sdk_refresh else 'sdk-execution' if sdk_update else 'refresh-redaction' if refresh_redaction else 'memory-pressure' if memory_pressure else 'body-budget' if oversized_body else 'numeric-fidelity' if numeric_result else 'output-integration' if oversized_result else 'startup-inventory' if startup_inventory else 'deadline-recovery' if deadline_recovery else 'retained-http' if retained_http else 'late-adapter' if late_adapter else 'auto-delivery' if auto_delivery else 'source-change' if source_change else 'api-prepare' if api_prepare else 'private-pool' if private_pool else 'shared-pool' if shared_pool else 'request-mux' if request_mux else 'http-component' if http_component else 'late-settlement' if late_settlement else 'attempt-deadline' if attempt_deadline else 'automatic-loss' if automatic_loss else 'stream-interruption' if stream_interruption else 'stream-loss' if stream_loss else 'stream-pool' if stream_pool else 'physical-pool' if physical_pool else 'deadline-retained' if deadline_retained
                  else 'deadline-response-loss' if deadline_response_loss else 'egress-canonical')
        PACKET = ROOT / f'inbox/{prefix}-v{revision}'
        RUN = ROOT / f'runs/{prefix}-v{revision}'
        NAME = f'pyodide-ddf316c1-{prefix}-v{revision}'
    if (out(['hostname']) != 'bifrost-pyodide-test-01'
        or out(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE
        or shutil.disk_usage(ROOT).free < 8 * 1024**3):
        raise RuntimeError('Assigned guest/source/disk guard failed')
    project = out(['bash', '-c', 'source scripts/lib/test_helpers.sh; compute_project_name .'], cwd=CHECKOUT)
    if out(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]):
        raise RuntimeError('Reconcile owned running test project before launch')
    compose_hash = hashlib.sha256((CHECKOUT / 'docker-compose.test.yml').read_bytes()).hexdigest()
    if compose_hash != '33e78480090f97ea3f04be404c5a3d312ea8486ec6d55a60c67361a8ef0bdce9':
        raise RuntimeError('Resource-bounded test Compose changed')
    api_overlay = {path: hashlib.sha256((CHECKOUT / path).read_bytes()).hexdigest() for path in
                   ('api/src/worker/remote/protocol.py', 'api/src/worker/remote/remote_pool.py')}
    image_id = out(['docker', 'image', 'inspect', 'bifrost-test-api-dev:bifrost-test-7ddfac36', '--format', '{{.Id}}'])
    if (stream_interruption or late_settlement or shared_pool) and (api_overlay['api/src/worker/remote/remote_pool.py'] !=
        'dd14eb91761041a0f6ea6ea974295c2c610ad43c9e50e46dbb14028ef9f81789'
        or api_overlay['api/src/worker/remote/protocol.py'] != '91db42936f3235599243af681d24886fbb79180a7020633dfafe35887b3956d5'
        or image_id != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b'):
        raise RuntimeError('Qualified native pool/protocol/dependency image changed')
    for relative in ('canonical-local.py', 'task_cleanup.py', 'project/backend/authorized-local-entry.mjs'):
        if not (PACKET / relative).is_file():
            raise RuntimeError('Incomplete qualification packet: ' + relative)
    if egress_gate and not (PACKET / 'project/backend/attempt-egress.mjs').is_file():
        raise RuntimeError('Missing egress gate in qualification packet')
    if (deadline_response_loss or deadline_retained or stream_loss) and not (PACKET / 'provider-response-loss.py').is_file():
        raise RuntimeError('Missing response-loss probe in qualification packet')
    staged_provenance = None
    if staged_runtime:
        from staged_runtime_input import validate_staged_runtime
        staged_provenance = validate_staged_runtime(PACKET)
    elif hashlib.sha256((PACKET / 'project/backend/generated-runtime.mjs').read_bytes()).hexdigest() != 'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0':
        raise RuntimeError('Qualified runtime closure changed')
    if sdk_update:
        candidate = PACKET / 'project/backend/generated-runtime-sdk.mjs'
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731':
            raise RuntimeError('Exact SDK update candidate changed')
        if "from './generated-runtime-sdk.mjs'" not in (PACKET / 'project/backend/runtime-code.mjs').read_text():
            raise RuntimeError('SDK update candidate is not selected')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    if stream_interruption:
        (RUN / 'control').mkdir()
        os.chown(RUN / 'control', 1000, 1000)
    shutil.copytree(PACKET / 'project', RUN / 'project')
    cache = ROOT / 'runs/runtime-source-bundle-v1/bundle-cache'
    wrapper = ROOT / 'runs/runtime-source-bundle-v1/workerd-with-cache.sh'
    if hashlib.sha256(wrapper.read_bytes()).hexdigest() != '9f9ad4a47455ff2f29a5600471855260f88f0bc42a690917fa47a46ce34bffe7':
        raise RuntimeError('Pinned workerd wrapper changed')
    if (hashlib.sha256((cache / 'pyodide_314.0.6_2026-08-17_6.capnp.bin').read_bytes()).hexdigest()
        != '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260'):
        raise RuntimeError('Runtime bundle cache changed')
    env = {'PATH': os.environ['PATH'], 'HOME': str(RUN / 'home'), 'BIFROST_SKIP_BUILD': '1',
           'BIFROST_TEST_IMAGE_TAG': 'bifrost-test-7ddfac36', 'BIFROST_TEST_UNIT_ONLY': '1',
           'COMPOSE_PARALLEL_LIMIT': '1'}
    compose = ['docker', 'compose', '-p', project, '-f', 'docker-compose.test.yml']
    provider = ['docker', 'run', '-d', '--name', NAME, '--network', project + '_default',
        '--network-alias', 'bifrost-pyodide-ddf316c1', '--cpus=1', '--memory=2g', '--memory-swap=2g',
        '--pids-limit=128', '--label', 'com.docker.compose.project=' + project,
        '--mount', f'type=bind,src={OLD_SPIKE},dst=/spike,readonly',
        '--mount', f'type=bind,src={RUN},dst=/qualification',
        '--mount', f'type=bind,src={OLD_SPIKE}/node_modules,dst=/qualification/project/node_modules,readonly',
        '--mount', f'type=bind,src={cache},dst=/bundle-cache,readonly',
        '--mount', f'type=bind,src={wrapper},dst=/cache-wrapper.sh,readonly',
        '--env', 'MINIFLARE_WORKERD_PATH=/cache-wrapper.sh', '--env', 'HOME=/qualification/home',
        '--env', 'CI=true', '--env', 'WRANGLER_SEND_METRICS=false', '--workdir', '/qualification/project',
        '--entrypoint', 'node', 'bifrost-test-api-dev:bifrost-test-7ddfac36',
        '/qualification/project/node_modules/cf/bin/cf', 'dev']
    commands = [['./test.sh', 'stack', 'up'], [*compose, 'stop', 'worker', 'scheduler'], provider,
        [*compose, '--profile', 'test', 'run', '--rm', '--no-deps',
         '--volume', f'{CHECKOUT}/spikes/cloudflare-pyodide:/spike:ro',
         '--volume', f'{PACKET}:/qualification:ro',
         *(['--volume', f'{RUN}/control:/interruption', '--env', 'PROVIDER_INTERRUPTION_CONTROL=/interruption/ready.json'] if stream_interruption else []),
         'test-runner',
         'python', '/qualification/provider-response-loss.py' if deadline_response_loss or deadline_retained or stream_loss else '/qualification/canonical-local.py',
         '--late-settlement-probe' if late_settlement else '--attempt-deadline-probe' if attempt_deadline else '--automatic-loss-probe' if automatic_loss else '--stream-interruption-probe' if stream_interruption else '--stream-retained-response' if stream_loss else '--stream-pool-probe' if stream_pool else '--physical-pool-probe' if physical_pool else '--deadline-retained-response' if deadline_retained
         else '--deadline-response-loss' if deadline_response_loss else '--failure-probe']]
    if shared_pool:
        commands[-1][-2:] = ['/qualification/shared-native-pool.py']
        if https_fixture:
            commands[-1].append('--https-fixture')
        if https_deadline:
            commands[-1].append('--https-deadline')
        if source_change:
            commands[-1].append('--source-change')
        if late_adapter:
            commands[-1].append('--late-result')
        if retained_http:
            commands[-1].append('--retained-http')
        if deadline_recovery:
            commands[-1].extend(['--late-result', '--deadline-ack-loss'])
        if refresh_redaction:
            commands[-1].append('--refresh-redaction')
        if memory_pressure:
            commands[-1].append('--memory-pressure')
        if oversized_body:
            commands[-1].append('--oversized-body')
        if numeric_result:
            commands[-1].append('--numeric-result')
        if oversized_result:
            commands[-1].append('--oversized-result')
        if startup_inventory:
            commands[-1].append('--startup-inventory')
    if capability_revocation:
        commands[-1][-1] = '/qualification/capability-revocation-db.py'
    report = {'api_source': SOURCE, 'runtime_engine_source': 'f49d4ba99cebd24aa497f1244833b940e640885f',
        'project': project, 'cloud_invocations': 0, 'physical_termination_verified': False,
        'qualification_passed': False, 'commands': [], 'compose_sha256': compose_hash,
        'api_overlay_sha256': api_overlay, 'dependency_image_id': image_id,
        'packet_sha256': {str(path.relative_to(PACKET)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in PACKET.rglob('*') if path.is_file() and path.suffix != '.gz'}}
    if staged_runtime:
        report['staged_runtime_provenance'] = staged_provenance
    started = time.monotonic()
    if sdk_update:
        report['sdk_overlay'] = json.loads((PACKET / 'sdk-update-manifest.json').read_text())
    try:
        for index, command in enumerate(commands):
            if index == 3:
                for _ in range(45):
                    state = json.loads(out(['docker', 'inspect', NAME, '--format', '{{json .State}}']))
                    if not state['Running']:
                        raise RuntimeError('Local provider stopped before readiness')
                    ready = subprocess.run(['docker', 'exec', NAME, 'node', '-e',
                        "fetch('http://127.0.0.1:8799/health').then(r=>process.exit(r.status===405?0:1)).catch(()=>process.exit(1))"],
                        capture_output=True, timeout=5)
                    if ready.returncode == 0:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError('Local provider readiness deadline')
            if index == 3 and capability_revocation:
                out(['docker', 'exec', '-d', NAME, 'node', '/qualification/project/backend/explicit-revoke-observer.mjs'])
            if index == 3 and https_fixture:
                out(['docker', 'exec', '-d', NAME, 'node', '/qualification/project/backend/' +
                     ('canonical-https-deadline-observer.mjs' if https_deadline else 'canonical-https-observer.mjs')])
            with (RUN / f'phase-{index}.private.log').open('x') as log:
                if index == 3 and stream_interruption:
                    process = subprocess.Popen(command, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT)
                    try:
                        while process.poll() is None:
                            if time.monotonic() - started > 360 or shutil.disk_usage(ROOT).free < 4 * 1024**3:
                                raise RuntimeError('Interruption qualification wall/disk bound')
                            if not report.get('provider_process_stop_verified'):
                                try:
                                    descriptor = os.open(RUN / 'control/ready.json', os.O_RDONLY | os.O_NOFOLLOW)
                                except FileNotFoundError:
                                    pass
                                else:
                                    with os.fdopen(descriptor) as marker:
                                        content = marker.read(257)
                                    if len(content) > 256 or json.loads(content) != {'sdk_write_observed': True, 'value': 70}:
                                        raise RuntimeError('Invalid interruption positive control')
                                    subprocess.run(['docker', 'stop', '-t', '1', NAME], capture_output=True, check=True, timeout=15)
                                    stopped = json.loads(out(['docker', 'inspect', NAME, '--format', '{{json .State}}']))
                                    if stopped['Running'] or stopped['OOMKilled']:
                                        raise RuntimeError('Provider-process interruption readback failed')
                                    report.update(provider_process_stop_verified=True, sdk_write_observed_before_stop=True,
                                                  provider_state_after_interruption=stopped)
                            time.sleep(0.1)
                        exit_code = process.returncode
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=5)
                else:
                    result = subprocess.run(command, cwd=CHECKOUT, env=env, stdout=log,
                                            stderr=subprocess.STDOUT, timeout=max(1, 360 - (time.monotonic() - started)))
                    exit_code = result.returncode
            report['commands'].append({'argv': command, 'exit_code': exit_code})
            if exit_code:
                raise RuntimeError('Canonical failure qualification failed; inspect private log')
        records = [json.loads(line) for line in (RUN / 'phase-3.private.log').read_text().splitlines() if line.startswith('{')]
        if capability_revocation:
            probe = next(row for row in reversed(records) if row.get('capability_revocation_probe'))
            if not probe.get('owner_rotation_observed_waiting_on_postgres_lock') or not probe.get('completed_collection_retired'):
                raise RuntimeError('Capability revocation did not qualify fencing/collection')
            report['capability_revocation_result'] = probe
        elif shared_pool:
            probe = next(row for row in reversed(records) if row.get('shared_native_pool_probe'))
            if (not probe.get('single_native_pool_verified')
                or (not source_change and not probe.get('both_real_sdk_effects_held_concurrently'))
                or not probe.get('mux_retired_after_canonical_ack') or len(probe.get('attempts', {})) != 2
                or list(probe.get('launches', {}).values()) != [1, 1]):
                raise RuntimeError('Shared pool did not qualify two independent native attempts')
            report['shared_pool_result'] = probe
            if (sdk_update or staged_runtime) and not all(attempt['evidence'].get('runtime_cleanup', {}).get('sdk_close_returned') is True
                                      for attempt in probe['attempts'].values()):
                raise RuntimeError('Awaited SDK close missing from updated runtime completion')
            if oversized_result and not probe.get('oversized_terminal_failure_verified'):
                raise RuntimeError('Oversized terminal failure was not qualified')
            if startup_inventory and (not probe.get('native_runtime_cache_projection_verified')
                or not probe.get('native_runtime_cache_removed')
                or probe.get('full_native_startup_qualified') is not False):
                raise RuntimeError('Runtime inventory/cache projection was not qualified')
            if deadline_recovery and (not probe.get('explicit_deadline_receipt_recovery_verified')
                or not probe.get('original_deadline_receipt_failure_preserved')
                or not probe.get('late_success_preserved_timeout')):
                raise RuntimeError('Explicit deadline receipt recovery was not qualified')
            if retained_http and (not probe.get('retained_http_preack_loss_verified')
                or probe.get('retained_posts') != 1 or probe.get('parent_accepted_observed') is not False
                or not probe.get('retained_collector_eof_complete')):
                raise RuntimeError('Actual retained HTTP fault path was not qualified')
            if late_adapter and not probe.get('integrated_late_result_verified'):
                raise RuntimeError('Integrated deadline/late-result path was not qualified')
            if auto_delivery and not probe.get('automatic_terminal_delivery_verified'):
                raise RuntimeError('Automatic canonical terminal delivery was not qualified')
            if private_pool and not probe.get('reusable_private_adapter_verified'):
                raise RuntimeError('Reusable private backend was not exercised')
            if api_prepare and (not probe.get('api_preparation_verified') or probe.get('invalid_preparation_credential_status') != 401):
                raise RuntimeError('Authenticated API preparation was not qualified')
            if source_change and (not probe.get('changed_dependency_rejected_before_workflow_effects')
                or probe.get('failed_document_status_before_terminal') != 404):
                raise RuntimeError('Changed dependency was not rejected before workflow effects')
            if source_change:
                diagnostic = probe.get('source_integrity_diagnostic_seen', False)
                report['source_integrity_observer'] = 'engine_result' if diagnostic else 'provider_log'
                if not diagnostic:
                    diagnostic = 'Workspace module digest mismatch' in out(['docker', 'logs', '--tail', '200', NAME],
                                                                         stderr=subprocess.STDOUT)
                report['source_integrity_diagnostic_seen'] = diagnostic
                if not diagnostic:
                    raise RuntimeError('Source failure lacks a specific integrity-rejection diagnostic')
        elif late_settlement:
            probe = next(row for row in reversed(records) if row.get('late_settlement_probe'))
            if (not probe.get('canonical_timeout_preserved') or not probe.get('actual_terminal_released_handle')
                or not probe.get('fixture_worker_cascade_cleanup_verified') or probe.get('launches') != 1):
                raise RuntimeError('Late terminal did not preserve Timeout and release the settled handle')
            report['late_settlement_result'] = probe
            if http_component and not probe.get('http_invocation_component_verified'):
                raise RuntimeError('Reusable HTTP component was not exercised by the real engine path')
            if request_mux and not probe.get('request_mux_verified'):
                raise RuntimeError('Attempt-scoped request mux was not exercised')
        elif stream_interruption:
            probe = next(row for row in reversed(records) if row.get('stream_interruption_probe'))
            if (not report.get('provider_process_stop_verified') or probe.get('launches') != 1
                or probe.get('canonical_recovery_status') != ('Timeout' if attempt_deadline else 'Failed') or probe.get('result_envelope_received') is not False
                or not probe.get('opaque_obligation_retained_after_canonical_failure')
                or not probe.get('fixture_worker_cascade_cleanup_verified')):
                raise RuntimeError('Native streamed provider loss did not preserve recovery/resource distinction')
            report['stream_interruption_result'] = probe
            if attempt_deadline and (not probe.get('early_deadline_rejected') or not probe.get('deadline_replay_consumed')
                or not probe.get('relay_remains_ready') or not probe.get('no_worker_wide_recovery')):
                raise RuntimeError('Per-attempt deadline did not preserve live relay semantics')
            if automatic_loss and (not probe.get('existing_api_control_recovery_verified')
                or not probe.get('expired_owner_duplicate_dispatch_rejected')
                or probe.get('recovered_worker_end_reason') != 'heartbeat_lost'):
                raise RuntimeError('Existing API control did not automatically recover the silent fixture relay')
        elif stream_loss:
            probe = next(row for row in reversed(records) if row.get('stream_loss_probe'))
            if (not probe.get('local_recovery_verified') or probe.get('launches') != 1
                or not probe.get('native_opaque_route_released_after_reconciliation')
                or not probe.get('fixture_execution_cleanup_verified') or not probe.get('original_deadline_preserved')):
                raise RuntimeError('Accepted stream loss did not preserve canonical recovery/cleanup')
            report['stream_loss_result'] = probe
        elif deadline_response_loss or deadline_retained:
            probe = next(row for row in reversed(records) if row.get('deadline_response_loss_probe'))
            if (probe.get('launches') != 1 or not probe.get('canonical_success_result_missing')
                or not probe.get('fixture_execution_cleanup_verified') or probe.get('independent_post_deadline_value') != 71):
                raise RuntimeError('Required deadline/uncertain-effect observations missing')
            if deadline_retained and (not probe.get('local_recovery_verified')
                or not probe.get('terminal_ack_preserved_deadline') or not probe.get('upstream_ack_consumed')):
                raise RuntimeError('Retained result reconciliation did not preserve original deadline')
            report['deadline_probe_result'] = probe
        elif physical_pool:
            probe = next(row for row in reversed(records) if row.get('physical_pool_probe'))
            if (not probe.get('canonical_terminal_consumption_verified') or probe.get('launches') != 1
                or not probe.get('native_opaque_route_released_after_terminal_ack') or not probe.get('no_pid_fabricated')):
                raise RuntimeError('Native opaque pool did not preserve canonical terminal semantics')
            report['physical_pool_result'] = probe
            if stream_pool and (not probe.get('dispatch_ack_before_workflow_completion')
                or probe.get('dispatch_ack_after_workflow_completion')
                or probe['dispatch_accepted_monotonic'] >= probe['terminal_collected_monotonic']):
                raise RuntimeError('Stream did not qualify early ACK and terminal collection')
        else:
            canonical = next(row for row in reversed(records) if row.get('canonical_claim_verified'))
            if (not canonical.get('stdout_capture_verified') or not canonical.get('canonical_terminal_consumption_verified')
                or not canonical.get('persisted_failure_stdout_redaction_verified')):
                raise RuntimeError('Required real failure/stdout consumption not observed')
            report['canonical_result'] = canonical
        if https_fixture:
            observed = json.loads(out(['docker', 'exec', NAME, 'node', '-e',
                "fetch('http://127.0.0.1:8820/snapshot').then(r=>r.text()).then(console.log)"]))
            if https_deadline:
                report['vendor_after_release'] = observed
                if not observed['committed'] or observed['events'] != ['hold_received', 'parent_release', 'hold_committed']:
                    raise RuntimeError('In-flight vendor commit receipt missing')
                observed = observed['effects']
            expected = sorted((label, path) for label in ('fails-first', 'succeeds-second') for path in ('/write', '/redirect'))
            if https_deadline:
                expected.append(('fails-first', '/hold'))
                expected.sort()
            if sorted((row['body'], row['path']) for row in observed) != expected or not all(row['vendor_authorization_matches'] for row in observed):
                raise RuntimeError('Independent HTTPS destination evidence mismatch')
            report['synthetic_https_effects'] = observed
        report['qualification_passed'] = True
    finally:
        if source_change and (RUN / 'phase-3.private.log').exists():
            for line in (RUN / 'phase-3.private.log').read_text().splitlines():
                if line.startswith('{'):
                    diagnostic = json.loads(line)
                    if diagnostic.get('source_change_peer_diagnostic'):
                        report['peer_control_diagnostic'] = diagnostic
        state = subprocess.run(['docker', 'inspect', NAME, '--format', '{{json .State}}'], capture_output=True, text=True)
        if state.returncode == 0:
            report['provider_state_before_cleanup'] = json.loads(state.stdout)
            subprocess.run(['docker', 'stop', '-t', '5', NAME], capture_output=True, timeout=15, check=True)
        subprocess.run([*compose, '--profile', 'e2e', '--profile', 'test', 'stop'],
                       cwd=CHECKOUT, env=env, timeout=60, check=True)
        report['running_after_cleanup'] = out(['docker', 'ps', '-q', '--filter',
                                               'label=com.docker.compose.project=' + project]).splitlines()
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'canonical_result'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--egress-gate', action='store_true')
    mode.add_argument('--deadline-response-loss', action='store_true')
    mode.add_argument('--deadline-retained', action='store_true')
    mode.add_argument('--physical-pool', action='store_true')
    mode.add_argument('--stream-pool', action='store_true')
    mode.add_argument('--stream-loss', action='store_true')
    mode.add_argument('--stream-interruption', action='store_true')
    mode.add_argument('--automatic-loss', action='store_true')
    mode.add_argument('--attempt-deadline', action='store_true')
    mode.add_argument('--late-settlement', action='store_true')
    mode.add_argument('--http-component', action='store_true')
    mode.add_argument('--request-mux', action='store_true')
    mode.add_argument('--shared-pool', action='store_true')
    mode.add_argument('--private-pool', action='store_true')
    mode.add_argument('--api-prepare', action='store_true')
    mode.add_argument('--source-change', action='store_true')
    mode.add_argument('--auto-delivery', action='store_true')
    mode.add_argument('--late-adapter', action='store_true')
    mode.add_argument('--retained-http', action='store_true')
    mode.add_argument('--deadline-recovery', action='store_true')
    mode.add_argument('--sdk-refresh', action='store_true')
    mode.add_argument('--staged-runtime', action='store_true')
    mode.add_argument('--https-fixture', action='store_true')
    mode.add_argument('--https-deadline', action='store_true')
    mode.add_argument('--capability-revocation', action='store_true')
    mode.add_argument('--sdk-update', action='store_true')
    mode.add_argument('--refresh-redaction', action='store_true')
    mode.add_argument('--memory-pressure', action='store_true')
    mode.add_argument('--oversized-body', action='store_true')
    mode.add_argument('--numeric-result', action='store_true')
    mode.add_argument('--oversized-result', action='store_true')
    mode.add_argument('--startup-inventory', action='store_true')
    parser.add_argument('--revision', type=int, choices=range(1, 100), default=1)
    args = parser.parse_args()
    main(egress_gate=args.egress_gate, deadline_response_loss=args.deadline_response_loss,
         deadline_retained=args.deadline_retained, physical_pool=args.physical_pool, stream_pool=args.stream_pool,
         stream_loss=args.stream_loss, stream_interruption=args.stream_interruption,
         automatic_loss=args.automatic_loss, attempt_deadline=args.attempt_deadline,
         late_settlement=args.late_settlement, http_component=args.http_component, request_mux=args.request_mux,
         shared_pool=args.shared_pool, private_pool=args.private_pool, api_prepare=args.api_prepare,
         source_change=args.source_change, auto_delivery=args.auto_delivery, late_adapter=args.late_adapter, retained_http=args.retained_http, deadline_recovery=args.deadline_recovery, startup_inventory=args.startup_inventory, oversized_result=args.oversized_result, numeric_result=args.numeric_result, oversized_body=args.oversized_body, memory_pressure=args.memory_pressure, refresh_redaction=args.refresh_redaction, sdk_update=args.sdk_update, sdk_refresh=args.sdk_refresh, staged_runtime=args.staged_runtime, https_fixture=args.https_fixture, https_deadline=args.https_deadline, capability_revocation=args.capability_revocation, revision=args.revision)
