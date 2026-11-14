import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const report = JSON.parse(await readFile(new URL('../results/automatic-loss/v1.json', import.meta.url)));
const control = JSON.parse(await readFile(new URL('../results/automatic-loss/control-source-readback.json', import.meta.url)));
for (const [path, digest] of Object.entries(control.source_sha256)) {
  assert.equal(hash(execFileSync('git', ['show', `${control.source_commit}:${path}`])), digest, path);
}
const quality = JSON.parse(await readFile(new URL('../results/automatic-loss/quality-checks.json', import.meta.url)));
assert.equal(quality.ruff.exit_code, 0);
assert.equal(quality.pyright.exit_code, 0);
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_process_stop_verified, true);
for (const name of ['canonical-local.py', 'ct128-canonical-failure.py', 'task_cleanup.py',
  'invocation_stream.py', 'pyright-guest-fixtures.json']) {
  assert.equal(hash(execFileSync('git', ['show', `f2cc3f29c:spikes/cloudflare-pyodide/backend/${name}`])), report.packet_sha256[name], name);
}
for (const [path, digest] of Object.entries(report.api_overlay_sha256)) {
  assert.equal(hash(execFileSync('git', ['show', `f2cc3f29c:${path}`])), digest, path);
}
assert.equal(report.packet_sha256['project/backend/generated-runtime.mjs'],
  'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
assert.equal(report.dependency_image_id, 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b');
const run = report.stream_interruption_result;
for (const name of ['automatic_loss_probe', 'existing_api_control_recovery_verified',
  'no_direct_recovery_call', 'no_clock_or_heartbeat_backdating', 'expired_owner_duplicate_dispatch_rejected',
  'dispatch_ack_before_workflow_completion', 'opaque_obligation_retained_before_recovery',
  'opaque_obligation_retained_after_canonical_failure', 'original_identity_and_deadline_preserved',
  'no_redispatch_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[name], true, name);
assert.equal(run.provider_only_loss_with_live_relay_status, 'Running');
assert.ok(run.live_relay_negative_control_seconds >= 46);
assert.equal(run.recovered_worker_state, 'lost');
assert.equal(run.recovered_worker_end_reason, 'heartbeat_lost');
assert.ok(run.recovered_heartbeat_age_seconds >= 30);
assert.ok(run.silence_to_observed_failure_seconds > 0 && run.silence_to_observed_failure_seconds < 75);
assert.equal(run.canonical_before_recovery_status, 'Running');
assert.equal(run.canonical_recovery_status, 'Failed');
assert.equal(run.provider_collection_error_type, 'RemoteProtocolError');
assert.equal(run.result_envelope_received, false);
assert.equal(run.duplicate_reused_failed_receipt, false); // Expired owner is refused instead.
assert.equal(run.launches, 1);
assert.equal(run.independent_document_value, 70);
assert.equal(run.runtime_access_after_recovery_status, 401);
assert.equal(run.runtime_refresh_after_recovery_status, 401);
assert.deepEqual(run.retained_log_messages, ['integration secret=[REDACTED]']);
assert.equal(run.terminal_log_drain.flushed, 1);
assert.equal(run.repeated_terminal_log_drain.flushed, 0);
assert.equal(run.collection_inventory[0].acknowledged, false);
for (const item of [report, run]) {
  assert.equal(item.cloud_invocations, 0);
  assert.equal(item.physical_termination_verified, false);
}
console.log('Existing API control automatically recovers real relay silence; live-relay provider loss remains Running in the negative control. No clocks patched, direct recovery call or physical settlement inference.');
