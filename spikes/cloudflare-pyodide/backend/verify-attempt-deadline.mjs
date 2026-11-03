import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const base = new URL('../results/attempt-deadline/', import.meta.url);
const bytes = name => readFile(new URL(name, base));
const read = async name => JSON.parse(await bytes(name));
const initial = await read('attempt-deadline-v1/receipt.json');
const failed = await read('attempt-deadline-v2/receipt.json');
const final = await read('v3.json');
assert.equal(initial.qualification_passed, true);
assert.equal(failed.qualification_passed, false);
assert.equal(failed.commands.at(-1).exit_code, 1);
assert.equal((await read('v2-error.json')).error_type, 'AttributeError');
assert.equal(hash(await bytes('v1-helper.txt')), initial.packet_sha256['attempt_deadline.py']);
assert.equal(hash(await bytes('v2-helper.txt')), failed.packet_sha256['attempt_deadline.py']);
const before = await read('stall-before.json');
assert.equal(before.failures, 1);
assert.equal(before.exit_code, 1);
assert.equal(before.before_helper_sha256, failed.packet_sha256['attempt_deadline.py']);
assert.equal(hash(await bytes('stall-test.txt')), before.test_source_sha256);
assert.equal(final.qualification_passed, true);
assert.ok(final.commands.every(command => command.exit_code === 0));
for (const report of [initial, failed, final]) {
  assert.deepEqual(report.running_after_cleanup, []);
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.physical_termination_verified, false);
}
for (const name of ['attempt_deadline.py', 'test_attempt_deadline.py', 'canonical-local.py',
  'ct128-canonical-failure.py', 'task_cleanup.py', 'invocation_stream.py']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), final.packet_sha256[name], name);
}
for (const [path, digest] of Object.entries(final.api_overlay_sha256)) {
  assert.equal(hash(await readFile(new URL('../../../' + path, import.meta.url))), digest, path);
}
const run = final.stream_interruption_result;
for (const name of ['attempt_deadline_probe', 'early_deadline_rejected', 'owned_deadline_supervisor_verified',
  'deadline_delivery_receipt_lost', 'deadline_replay_consumed', 'relay_remains_ready', 'no_worker_wide_recovery',
  'opaque_obligation_retained_after_canonical_failure', 'original_identity_and_deadline_preserved',
  'duplicate_reused_failed_receipt', 'no_redispatch_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[name], true, name);
assert.equal(run.canonical_recovery_status, 'Timeout');
assert.equal(run.runtime_access_after_recovery_status, 401);
assert.equal(run.runtime_refresh_after_recovery_status, 401);
assert.equal(run.launches, 1);
assert.equal(run.independent_document_value, 70);
assert.equal(run.result_envelope_received, false);
assert.equal(run.physical_stop_inferred_from_canonical_failure, false);
assert.equal(run.persisted_log_count, 1);
assert.deepEqual(run.retained_log_messages, ['integration secret=[REDACTED]']);
assert.equal(run.repeated_terminal_log_drain.flushed, 0);
assert.equal(run.collection_inventory[0].acknowledged, false);
const quality = await read('quality-checks.json');
assert.equal(quality.unit.tests, 10);
for (const phase of ['unit', 'ruff', 'pyright']) assert.equal(quality[phase].exit_code, 0);
console.log('Owned original-deadline task delivers real canonical Timeout with a healthy relay; lost delivery receipt replays, resources stay held, 10 focused tests pass. Physical cancellation/admission remain unqualified.');
