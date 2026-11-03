import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const read = async path => JSON.parse(await readFile(new URL(path, import.meta.url)));
const report = await read('../results/stream-interruption/v2.json');
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_process_stop_verified, true);
assert.equal(report.sdk_write_observed_before_stop, true);
for (const state of [report.provider_state_after_interruption, report.provider_state_before_cleanup]) {
  assert.equal(state.Status, 'exited');
  assert.equal(state.Running, false);
  assert.equal(state.OOMKilled, false);
  assert.equal(state.Pid, 0);
  assert.equal(state.ExitCode, 143);
}
for (const name of ['canonical-local.py', 'ct128-canonical-failure.py', 'task_cleanup.py',
  'invocation_stream.py', 'pyright-guest-fixtures.json']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), report.packet_sha256[name], name);
}
for (const [path, digest] of Object.entries(report.api_overlay_sha256)) {
  assert.equal(hash(await readFile(new URL('../../../' + path, import.meta.url))), digest, path);
}
assert.equal(report.dependency_image_id, 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b');
assert.equal(report.packet_sha256['project/backend/generated-runtime.mjs'],
  'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
const run = report.stream_interruption_result;
for (const name of ['stream_interruption_probe', 'dispatch_ack_before_workflow_completion',
  'routing_returned_before_collection_wait', 'opaque_obligation_retained_before_recovery',
  'opaque_obligation_retained_after_canonical_failure', 'original_identity_and_deadline_preserved',
  'duplicate_reused_failed_receipt', 'canonical_claim_verified', 'provider_interruption_recovery_verified',
  'no_redispatch_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[name], true, name);
assert.equal(run.canonical_terminal_consumption_verified, false);
assert.equal(run.result_envelope_received, false);
assert.equal(run.physical_stop_inferred_from_canonical_failure, false);
assert.equal(run.provider_collection_error_type, 'RemoteProtocolError');
assert.equal(run.canonical_before_recovery_status, 'Running');
assert.equal(run.canonical_recovery_status, 'Failed');
assert.equal(run.independent_document_value, 70);
assert.equal(run.launches, 1);
assert.equal(run.runtime_access_after_recovery_status, 401);
assert.equal(run.runtime_refresh_after_recovery_status, 401);
assert.equal(run.persisted_log_count, 1);
assert.deepEqual(run.retained_log_messages, ['integration secret=[REDACTED]']);
assert.equal(run.terminal_log_drain.flushed, 1);
assert.equal(run.repeated_terminal_log_drain.flushed, 0);
assert.equal(run.collection_inventory.length, 1);
assert.equal(run.collection_inventory[0].collection_state, 'failed');
assert.equal(run.collection_inventory[0].acknowledged, false);
for (const row of [report, run, run.collection_inventory[0]]) assert.equal(row.physical_termination_verified, false);
for (const row of [report, run]) assert.equal(row.cloud_invocations, 0);
assert.equal((await read('../results/stream-interruption/v1-preflight.json')).exit_code, 1);
const quality = await read('../results/stream-interruption/quality-checks.json');
assert.equal(quality.ruff.exit_code, 0);
assert.equal(quality.pyright.exit_code, 0);
assert.equal(quality.pyright.errors, 0);
assert.equal(quality.pyright.memory_mib, 1024);
assert.equal((await read('../results/stream-interruption/v2-checker-oom.json')).memory_mib, 512);
console.log('Actual local streamed opaque provider loss: canonical Failed, SDK effect retained, credentials revoked, log drained once, no redispatch; physical invocation obligation remains distinct. No cloud cancellation acceptance.');
