import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const base = new URL('../results/private-pool/', import.meta.url);
const json = async name => JSON.parse(await readFile(new URL(name, base)));
const initial = await json('v1-quality-failure.json');
assert.equal(initial.unit_cases, 17);
assert.equal(initial.ruff_exit_code, 1);
assert.equal(initial.actual_workflow_invocations, 0);
const quality = await json('quality-checks.json');
assert.equal(quality.unit_tests.count, 17);
for (const name of ['unit_tests', 'ruff', 'pyright']) assert.equal(quality[name].exit_code, 0);
const report = await json('v2.json');
for (const name of ['private_http_pool.py', 'test_private_http_pool.py', 'attempt_journal.py',
  'shared-native-pool.py', 'http_invocation.py', 'invocation_stream.py', 'workflow_request_mux.py',
  'test_workflow_request_mux.py', 'ct128-canonical-failure.py', 'task_cleanup.py']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), report.packet_sha256[name], name);
}
for (const [path, digest] of Object.entries(report.api_overlay_sha256)) {
  assert.equal(hash(await readFile(new URL('../../../' + path, import.meta.url))), digest, path);
}
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_state_before_cleanup.Running, true);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
const run = report.shared_pool_result;
for (const name of ['reusable_private_adapter_verified', 'single_native_pool_verified',
  'both_real_sdk_effects_held_concurrently', 'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified',
  'peer_live_after_failure_reconcile', 'duplicate_dispatch_reused_peer', 'lost_ack_reconnect_verified',
  'canonical_peer_accounting_verified', 'canonical_settled_accounting_verified', 'terminal_obligations_released',
  'canonical_terminal_reconciliation_verified', 'mux_retired_after_canonical_ack',
  'credential_free_prewarm', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[name], true, name);
assert.deepEqual(run.native_busy_counts, [2, 2, 1, 0]);
assert.equal(run.failed_access_status, 401);
assert.equal(run.peer_access_status, 200);
const attempts = Object.entries(run.attempts);
assert.equal(attempts.length, 2);
assert.equal(new Set(attempts.map(([, attempt]) => attempt.runtime_handle)).size, 2);
assert.equal(new Set(attempts.map(([, attempt]) => attempt.evidence.result.execution_context.scope)).size, 2);
for (const [id, attempt] of attempts) {
  assert.equal(run.launches[id], 1);
  assert.equal(attempt.access_after_terminal_status, 401);
  const {result, api_calls: calls} = attempt.evidence;
  assert.equal(result.execution_id, id);
  assert.equal(result.worker_id, run.worker_id);
  assert.equal(attempt.evidence.runtime_handle, attempt.runtime_handle);
  assert.ok(Object.values(run.workspace_sha256).includes(result.metrics.code_hash));
  assert.ok(Object.values(result.variables.fresh).every(value => value === null));
  assert.equal(result.metrics.cpu_total_seconds, null);
  assert.equal(result.metrics.peak_memory_bytes, null);
  assert.ok(calls.some(call => call.path === '/api/sdk/config/get' && call.status === 403));
  assert.equal(calls.filter(call => call.path.endsWith('/documents/upsert') && call.status === 200).length, 1);
  assert.ok(attempt.persisted_logs.includes('shared label=' + attempt.label + ' credential=[REDACTED]'));
  if (attempt.label === 'fails-first') {
    assert.equal(result.success, false);
    assert.equal(attempt.canonical_status, 'Failed');
    assert.equal(attempt.canonical_error, 'shared fixture failed credential=[REDACTED]');
    assert.equal(attempt.canonical_result, null);
    assert.equal(result.variables.doc.data.value, 70);
  } else {
    assert.equal(attempt.label, 'succeeds-second');
    assert.equal(result.success, true);
    assert.equal(attempt.canonical_status, 'Success');
    assert.equal(attempt.canonical_result.value, 78);
    assert.equal(attempt.canonical_result.credential, '[REDACTED]');
    assert.equal(attempt.canonical_result.parent_secret_present, false);
  }
}
assert.ok(Math.max(...attempts.map(([, attempt]) => attempt.accepted_monotonic)) <
  Math.min(...attempts.map(([, attempt]) => attempt.terminal_monotonic)));
console.log('Reusable private HTTP adapter passed two real tenant engine/SDK runs with canonical fencing, peer-preserving ACK replay and retirement. Public startup/provenance resolution and deployed cancellation remain unaccepted.');
