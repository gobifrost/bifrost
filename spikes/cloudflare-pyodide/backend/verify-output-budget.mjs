import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

const root = new URL('../', import.meta.url);
const receipt = JSON.parse(readFileSync(new URL('results/output-budget/receipt-v1.json', root), 'utf8').replace(/^\uFEFF/, ''));
for (const path of ['shared-native-pool.py', 'ct128-canonical-failure.py']) {
  const digest = createHash('sha256').update(readFileSync(new URL('backend/' + path, root))).digest('hex');
  assert.equal(digest, receipt.packet_sha256[path]);
}
const digest = createHash('sha256').update(readFileSync(new URL('backend/invocation-stream.mjs', root))).digest('hex');
assert.equal(digest, receipt.packet_sha256['project/backend/invocation-stream.mjs']);
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.deepEqual(receipt.running_after_cleanup, []);
const proof = receipt.shared_pool_result;
for (const key of ['oversized_terminal_failure_verified', 'both_real_sdk_effects_held_concurrently',
  'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified',
  'duplicate_dispatch_reused_peer', 'canonical_settled_accounting_verified',
  'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
const attempts = Object.values(proof.attempts);
const failed = attempts.find(value => value.label === 'fails-first');
const peer = attempts.find(value => value.label === 'succeeds-second');
assert.equal(failed.canonical_status, 'Failed');
assert.equal(failed.canonical_result, null);
assert.equal(failed.evidence.result.error_type, 'ExecutorFrameTooLargeError');
assert.equal(failed.evidence.provider_outcome.engine_outcome_delivered, false);
assert.equal(peer.canonical_status, 'Success');
assert.equal(peer.canonical_result.value, 78);
for (const attempt of attempts) {
  assert.equal(attempt.access_after_terminal_status, 401);
  assert.ok(attempt.persisted_logs.length > 0);
}
console.log('Source-bound local upstream output-budget proof verified; no cloud or CPU-stop claim.');
