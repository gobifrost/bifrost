import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const source = name => execFileSync('git', ['show', 'b6e4dfd4c28e6859c4c6319a1e8a664c221eba84:spikes/cloudflare-pyodide/backend/' + name]);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const base = new URL('../results/source-change/', import.meta.url);
const bytes = name => readFile(new URL(name, base));
const json = async name => JSON.parse(await bytes(name));
for (const version of [1, 2, 3]) {
  const failed = await json('v' + version + '.json');
  assert.equal(failed.qualification_passed, false);
  assert.equal(failed.commands.at(-1).exit_code, 1);
  assert.equal(hash(await bytes('v' + version + '-fixture.txt')), failed.packet_sha256['shared-native-pool.py']);
}
const exposed = (await json('v3.json')).peer_control_diagnostic;
assert.equal(exposed.native_busy_count, 2);
assert.equal(exposed.peer_collection_done, true);
assert.equal(exposed.peer_success, false);
assert.equal(exposed.peer_error_type, 'BifrostAPIError');
assert.ok(exposed.peer_remaining_deadline_ms < 0);
assert.ok(exposed.peer_last_calls.some(call => call.status === 410));
const quality = await json('quality-checks.json');
assert.equal(quality.node_tests.count, 3);
for (const key of ['node_tests', 'ruff', 'pyright']) assert.equal(quality[key].exit_code, 0);
const baseline = JSON.parse(await readFile(new URL('../results/api-prepare/v2.json', import.meta.url)));
const reports = [await json('v4.json'), await json('v5.json')];
for (const report of reports) {
  for (const [path, digest] of Object.entries(report.api_overlay_sha256)) {
    assert.equal(hash(await readFile(new URL('../../../' + path, import.meta.url))), digest, path);
  }
  assert.equal(report.packet_sha256['project/backend/fixture-admission.mjs'],
    baseline.packet_sha256['project/backend/fixture-admission.mjs']);
  for (const name of ['workspace_prepare.py', 'test_workspace_prepare.py', 'private_http_pool.py',
    'test_private_http_pool.py', 'http_invocation.py', 'test_http_invocation.py', 'workflow_request_mux.py',
    'test_workflow_request_mux.py', 'attempt_journal.py', 'invocation_stream.py', 'task_cleanup.py']) {
    assert.equal(report.packet_sha256[name], baseline.packet_sha256[name], name + ' retained baseline');
  }
  for (const name of ['shared-native-pool.py', 'ct128-canonical-failure.py', 'source-integrity-response.mjs',
    'source-integrity-response.test.mjs', 'module-response.mjs']) {
    assert.equal(hash(source(name)), report.packet_sha256[name], name);
  }
  for (const name of ['authorized-local-entry.mjs', 'source-integrity-response.mjs']) {
    assert.equal(hash(source(name)), report.packet_sha256['project/backend/' + name], name);
  }
  assert.equal(report.qualification_passed, true);
  assert.ok(report.commands.every(command => command.exit_code === 0));
  assert.deepEqual(report.running_after_cleanup, []);
  assert.equal(report.provider_state_before_cleanup.Running, true);
  assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.physical_termination_verified, false);
  assert.equal(report.source_integrity_diagnostic_seen, true);
  assert.equal(report.source_integrity_observer, 'engine_result');
  const peerControl = report.peer_control_diagnostic;
  assert.equal(peerControl.peer_collection_done, false);
  assert.ok(peerControl.peer_remaining_deadline_ms > 0);
  const run = report.shared_pool_result;
  for (const field of ['source_change_probe', 'changed_dependency_rejected_before_workflow_effects',
    'upstream_load_error_empty_logs_verified', 'failed_replay_unchanged_verified', 'stale_terminal_owner_rejected',
    'peer_live_after_failure_reconcile', 'canonical_peer_accounting_verified', 'canonical_settled_accounting_verified',
    'terminal_obligations_released', 'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) {
    assert.equal(run[field], true, field);
  }
  assert.equal(run.both_real_sdk_effects_held_concurrently, false);
  assert.equal(run.failed_document_status_before_terminal, 404);
  assert.equal(run.source_mutation.api_readback_status, 200);
  assert.notEqual(run.source_mutation.prepared_sha256, run.source_mutation.changed_sha256);
  assert.equal(run.source_rejection_error_type, 'WorkflowLoadError');
  assert.equal(run.failed_engine_log_count, 0);
  assert.equal(run.failed_persisted_log_count, 0);
  assert.deepEqual(run.native_busy_counts, [2, 2, 1, 0]);
  assert.equal(run.failed_access_status, 401);
  assert.equal(run.peer_access_status, 200);
  const attempts = Object.entries(run.attempts);
  assert.equal(attempts.length, 2);
  for (const [id, attempt] of attempts) {
    assert.equal(run.launches[id], 1);
    assert.equal(attempt.access_after_terminal_status, 401);
    if (attempt.label === 'fails-first') {
      assert.equal(attempt.canonical_status, 'Failed');
      assert.equal(attempt.canonical_result, null);
      assert.ok(attempt.canonical_error);
      assert.deepEqual(attempt.persisted_logs, []);
      const calls = attempt.evidence.api_calls;
      assert.ok(calls.some(call => call.status === 409 && call.upstream_status === 200 &&
        call.integrity_error === 'Workspace module digest mismatch'));
      assert.ok(!calls.some(call => call.path.startsWith('/api/tables') || call.path.startsWith('/api/sdk/config/')));
    } else {
      assert.equal(attempt.label, 'succeeds-second');
      assert.equal(attempt.canonical_status, 'Success');
      assert.equal(attempt.canonical_result.value, 78);
      assert.equal(attempt.canonical_result.credential, '[REDACTED]');
      assert.equal(attempt.canonical_result.parent_secret_present, false);
      assert.ok(Object.values(attempt.canonical_result.fresh).every(value => value === null));
    }
  }
}
assert.deepEqual(reports[0].packet_sha256, reports[1].packet_sha256);
console.log('Changed helper rejected before eager-workflow effects; original peer/barrier/deadline passes in two exact-candidate local runs. Failed predecessors and native empty-log semantics preserved; no deployed isolation/cancellation claim.');
