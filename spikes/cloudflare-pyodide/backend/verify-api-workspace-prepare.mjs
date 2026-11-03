import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const base = new URL('../results/api-prepare/', import.meta.url);
const bytes = name => readFile(new URL(name, base));
const json = async name => JSON.parse(await bytes(name));
const failed = await json('v1-fail-first.json');
assert.equal(failed.cases, 23);
assert.equal(failed.failures, 1);
assert.equal(failed.exit_code, 1);
assert.equal(failed.actual_workflow_invocations, 0);
assert.equal(hash(await bytes('v1-source.txt')), failed.before_sha256);
assert.equal(hash(await bytes('v1-test.txt')), failed.test_sha256);
const quality = await json('quality-checks.json');
assert.equal(quality.unit_tests.count, 23);
for (const name of ['unit_tests', 'ruff', 'pyright']) assert.equal(quality[name].exit_code, 0);
const report = await json('v2.json');
const nativeSource = await json('native-source-readback.json');
assert.equal(nativeSource.clean_against_base, true);
assert.equal(nativeSource.api_source, report.api_source);
assert.deepEqual(Object.keys(nativeSource.sha256).sort(), ['api/shared/sdk_modules.py', 'api/src/routers/sdk_modules.py']);
for (const name of ['workspace_prepare.py', 'test_workspace_prepare.py', 'shared-native-pool.py',
  'private_http_pool.py', 'ct128-canonical-failure.py', 'http_invocation.py', 'attempt_journal.py',
  'workflow_request_mux.py', 'invocation_stream.py', 'task_cleanup.py']) {
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
for (const name of ['api_preparation_verified', 'reusable_private_adapter_verified', 'single_native_pool_verified',
  'both_real_sdk_effects_held_concurrently', 'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified',
  'peer_live_after_failure_reconcile', 'duplicate_dispatch_reused_peer', 'lost_ack_reconnect_verified',
  'canonical_peer_accounting_verified', 'canonical_settled_accounting_verified', 'terminal_obligations_released',
  'canonical_terminal_reconciliation_verified', 'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) {
  assert.equal(run[name], true, name);
}
assert.equal(run.invalid_preparation_credential_status, 401);
assert.deepEqual(run.native_busy_counts, [2, 2, 1, 0]);
assert.equal(run.peer_access_status, 200);
assert.equal(run.failed_access_status, 401);
const attempts = Object.entries(run.attempts);
assert.equal(attempts.length, 2);
assert.equal(new Set(attempts.map(([, value]) => value.runtime_handle)).size, 2);
for (const [id, attempt] of attempts) {
  assert.equal(run.launches[id], 1);
  assert.equal(attempt.access_after_terminal_status, 401);
  const prep = attempt.preparation, result = attempt.evidence.result;
  assert.equal(prep.original_content_hash, null);
  assert.equal(Object.keys(prep.manifest).length, 2);
  for (const [path, digest] of Object.entries(prep.manifest)) assert.equal(run.workspace_sha256[path], digest);
  assert.equal(prep.observations.length, 2);
  assert.ok(prep.observations.every(call => call.method === 'GET' && call.status === 200));
  assert.ok(prep.observations[0].path.startsWith('/api/sdk/modules/workflows/'));
  assert.equal(prep.observations[1].path, '/api/sdk/modules-resolve');
  assert.ok(prep.response_bytes > 0 && prep.response_bytes <= 4 * 1024**2);
  assert.equal(result.execution_id, id);
  assert.equal(result.worker_id, run.worker_id);
  assert.ok(Object.values(prep.manifest).includes(result.metrics.code_hash));
  assert.ok(Object.values(result.variables.fresh).every(value => value === null));
  assert.ok(attempt.evidence.api_calls.some(call => call.path === '/api/sdk/config/get' && call.status === 403));
  assert.equal(attempt.evidence.api_calls.filter(call => call.path.endsWith('/documents/upsert') && call.status === 200).length, 1);
  assert.ok(attempt.persisted_logs.includes('shared label=' + attempt.label + ' credential=[REDACTED]'));
  if (attempt.label === 'fails-first') {
    assert.equal(attempt.canonical_status, 'Failed');
    assert.equal(attempt.canonical_error, 'shared fixture failed credential=[REDACTED]');
    assert.equal(result.variables.doc.data.value, 70);
  } else {
    assert.equal(attempt.label, 'succeeds-second');
    assert.equal(attempt.canonical_status, 'Success');
    assert.equal(attempt.canonical_result.value, 78);
    assert.equal(attempt.canonical_result.credential, '[REDACTED]');
    assert.equal(attempt.canonical_result.parent_secret_present, false);
  }
}
console.log('Authenticated API-derived entry/dependency pins, invalid credential refusal and real two-tenant native/engine/SDK lifecycle verified. Declared imports only; no atomic workspace or deployed cancellation acceptance.');
