import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const base = new URL('../results/shared-pool/', import.meta.url);
const bytes = name => readFile(new URL(name, base));
const json = async name => JSON.parse(await bytes(name));
const initialQuality = await json('v1-quality-failure.json');
assert.equal(initialQuality.pyright_exit_code, 1);
assert.equal(initialQuality.actual_workflow_invocations, 0);
const earlier = await json('v2.json');
assert.equal(hash(await bytes('v2-source.txt')), earlier.packet_sha256['shared-native-pool.py']);
const accounting = await json('v3.json');
assert.equal(hash(await bytes('v3-mux.txt')), accounting.packet_sha256['workflow_request_mux.py']);
const report = await json('v4.json');
const quality = await json('quality-checks.json');
assert.equal(quality.unit_tests.count, 11);
for (const name of ['unit_tests', 'ruff', 'pyright']) assert.equal(quality[name].exit_code, 0);
for (const name of ['shared-native-pool.py', 'workflow_request_mux.py', 'test_workflow_request_mux.py',
  'ct128-canonical-failure.py', 'http_invocation.py', 'invocation_stream.py', 'task_cleanup.py']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), report.packet_sha256[name], name);
}
for (const [path, digest] of Object.entries(report.api_overlay_sha256)) {
  assert.equal(hash(await readFile(new URL('../../../' + path, import.meta.url))), digest, path);
}
for (const receipt of [earlier, accounting, report]) {
  assert.equal(receipt.qualification_passed, true);
  assert.equal(receipt.cloud_invocations, 0);
  assert.equal(receipt.physical_termination_verified, false);
  assert.ok(receipt.commands.every(command => command.exit_code === 0));
  assert.deepEqual(receipt.running_after_cleanup, []);
  assert.equal(receipt.provider_state_before_cleanup.Running, true);
  assert.equal(receipt.provider_state_before_cleanup.OOMKilled, false);
  const run = receipt.shared_pool_result;
  for (const field of ['credential_free_prewarm', 'single_native_pool_verified',
    'both_real_sdk_effects_held_concurrently', 'stale_terminal_owner_rejected',
    'failed_replay_unchanged_verified', 'peer_live_after_failure_reconcile',
    'duplicate_dispatch_reused_peer', 'lost_ack_reconnect_verified',
    'terminal_obligations_released', 'canonical_terminal_reconciliation_verified',
    'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[field], true, field);
  assert.equal(run.failed_access_status, 401);
  assert.equal(run.peer_access_status, 200);
  const attempts = Object.entries(run.attempts);
  assert.equal(attempts.length, 2);
  assert.equal(new Set(attempts.map(([, attempt]) => attempt.runtime_handle)).size, 2);
  assert.equal(new Set(attempts.map(([, attempt]) => attempt.route)).size, 2);
  assert.equal(new Set(attempts.map(([, attempt]) => attempt.evidence.result.execution_context.scope)).size, 2);
  for (const [id, attempt] of attempts) {
    assert.equal(run.launches[id], 1);
    assert.equal(attempt.access_after_terminal_status, 401);
    const evidence = attempt.evidence, result = evidence.result;
    assert.equal(result.execution_id, id);
    assert.equal(result.worker_id, run.worker_id);
    assert.equal(result.execution_context.execution_id, id);
    assert.equal(result.execution_context.workflow_timeout_seconds, 30);
    assert.equal(evidence.runtime_handle, attempt.runtime_handle);
    assert.ok(Object.values(run.workspace_sha256).includes(result.metrics.code_hash));
    assert.ok(Object.values(result.variables.fresh).every(value => value === null));
    assert.equal(result.metrics.peak_memory_bytes, null);
    assert.equal(result.metrics.cpu_total_seconds, null);
    assert.ok(evidence.api_calls.some(call => call.path === '/api/sdk/config/get' && call.status === 403));
    assert.equal(evidence.api_calls.filter(call => call.path.endsWith('/documents/upsert') && call.status === 200).length, 1);
    assert.ok(attempt.persisted_logs.includes('shared label=' + attempt.label + ' credential=[REDACTED]'));
    assert.ok(!attempt.persisted_logs.some(message => message.startsWith('shared label=') && !message.includes(attempt.label)));
    if (attempt.label === 'fails-first') {
      assert.equal(result.success, false);
      assert.equal(result.error_type, 'ValueError');
      assert.equal(attempt.canonical_status, 'Failed');
      assert.equal(attempt.canonical_result, null);
      assert.equal(attempt.canonical_error, 'shared fixture failed credential=[REDACTED]');
      assert.equal(result.variables.doc.data.value, 70);
    } else {
      assert.equal(attempt.label, 'succeeds-second');
      assert.equal(result.success, true);
      assert.equal(attempt.canonical_status, 'Success');
      assert.equal(attempt.canonical_result.value, 78);
      assert.equal(attempt.canonical_result.peer_status, 403);
      assert.equal(attempt.canonical_result.parent_secret_present, false);
      assert.equal(attempt.canonical_result.credential, '[REDACTED]');
    }
  }
  assert.ok(Math.max(...attempts.map(([, attempt]) => attempt.accepted_monotonic)) <
    Math.min(...attempts.map(([, attempt]) => attempt.terminal_monotonic)));
}
assert.deepEqual(report.shared_pool_result.native_busy_counts, [2, 2, 1, 0]);
assert.equal(report.shared_pool_result.canonical_peer_accounting_verified, true);
assert.equal(report.shared_pool_result.canonical_settled_accounting_verified, true);
console.log('Two actual tenant engine/SDK runs share one native pool, retain independent effects/credentials, fence stale replay and release acknowledged obligations. Warm local qualification, not deployed isolation/cancellation acceptance.');
