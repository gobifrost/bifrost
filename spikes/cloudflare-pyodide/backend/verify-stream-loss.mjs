import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const read = async path => JSON.parse(await readFile(new URL(path, import.meta.url)));
const first = await read('../results/stream-loss/v1.json');
const final = await read('../results/stream-loss/v2.json');
const quality = await read('../results/stream-loss/quality-checks.json');
assert.equal(quality.initial_pyright.exit_code, 1);
assert.equal(quality.intermediate_pyright.exit_code, 1);
assert.equal(quality.final_pyright.exit_code, 0);
assert.equal(quality.ruff.exit_code, 0);
for (const [path, digest] of Object.entries(quality.native_overlay_readback)) {
  assert.equal(hash(execFileSync('git', ['show', `89464d31d:${path}`])), digest, path);
}
assert.equal(hash(await readFile(new URL('../results/stream-loss/v1-provider.txt', import.meta.url))),
  first.packet_sha256['provider-response-loss.py']);
for (const name of ['provider-response-loss.py', 'ct128-canonical-failure.py', 'canonical-local.py',
  'invocation-stream.mjs', 'invocation_stream.py', 'authorized-local-entry.mjs', 'task_cleanup.py',
  'pyright-guest-fixtures.json']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), final.packet_sha256[name], name);
}
for (const receipt of [first, final]) {
  assert.equal(receipt.qualification_passed, true);
  assert.ok(receipt.commands.every(command => command.exit_code === 0));
  assert.deepEqual(receipt.running_after_cleanup, []);
  assert.equal(receipt.provider_state_before_cleanup.OOMKilled, false);
  assert.equal(receipt.packet_sha256['project/backend/generated-runtime.mjs'],
    'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
  const run = receipt.stream_loss_result;
  for (const name of ['stream_loss_probe', 'control_transport_simulated', 'stream_accepted_forwarded',
    'terminal_frame_deliberately_withheld', 'opaque_obligation_retained_after_loss',
    'failed_collection_unacknowledged', 'duplicate_reused_failed_receipt',
    'stale_retained_owner_rejected', 'native_opaque_route_released_after_reconciliation',
    'original_deadline_preserved', 'local_retained_result_consumed', 'retained_event_survived_lost_ack',
    'upstream_ack_consumed', 'fixture_worker_cleanup_verified', 'fixture_execution_cleanup_verified']) {
    assert.equal(run[name], true, name);
  }
  assert.match(run.accepted_runtime_handle, /^[a-f0-9-]{36}$/);
  assert.equal(run.collector_error_type, 'RemoteProtocolError');
  assert.equal(run.canonical_status, 'Running');
  assert.equal(run.canonical_error_message, null);
  assert.equal(run.canonical_status_after_recovery, 'Success');
  assert.equal(run.canonical_result_value, 70);
  assert.equal(run.independent_document_value, 70);
  assert.equal(run.launches, 1);
  assert.equal(run.successful_upserts, 1);
  assert.equal(run.persisted_log_count, 1);
  assert.equal(run.credential_active_pending_recovery_status, 200);
  assert.equal(run.credential_revoked_by_canonical_success_status, 401);
  assert.equal(run.refresh_revoked_by_canonical_success_status, 401);
  assert.equal(run.provider_execution_id, run.canonical_execution_id);
  assert.equal(run.provider_worker_id, run.canonical_worker_id);
  assert.ok(Object.values(run.workspace_sha256).includes(run.observed_code_hash));
  assert.equal(run.recovery_acceptance, false); // Not deployed recovery acceptance.
  for (const report of [receipt, run]) {
    assert.equal(report.cloud_invocations, 0);
    assert.equal(report.physical_termination_verified, false);
  }
}
console.log('Accepted TCP stream loss retains native obligation; one real SDK effect reconciles with owner fencing, unchanged deadline, lost ACK and cleanup. Control reconnect remains simulated.');
