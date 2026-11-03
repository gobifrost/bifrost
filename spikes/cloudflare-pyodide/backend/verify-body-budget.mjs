import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root = new URL('../', import.meta.url);
const receipt = JSON.parse(readFileSync(new URL('results/body-budget/receipt-v1.json', root), 'utf8').replace(/^\uFEFF/, ''));
for (const [local, packet] of [
  ['shared-native-pool.py', 'shared-native-pool.py'],
  ['ct128-canonical-failure.py', 'ct128-canonical-failure.py'],
  ['authorized-local-entry.mjs', 'project/backend/authorized-local-entry.mjs'],
  ['terminal-response.mjs', 'project/backend/terminal-response.mjs'],
]) {
  assert.equal(createHash('sha256').update(readFileSync(new URL('backend/' + local, root))).digest('hex'),
    receipt.packet_sha256[packet], local);
}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.deepEqual(receipt.running_after_cleanup, []);
const proof = receipt.shared_pool_result;
for (const key of ['oversized_body_failure_verified', 'both_real_sdk_effects_held_concurrently',
  'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified',
  'canonical_settled_accounting_verified', 'mux_retired_after_canonical_ack',
  'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
const failed = Object.values(proof.attempts).find(attempt => attempt.label === 'fails-first');
const peer = Object.values(proof.attempts).find(attempt => attempt.label === 'succeeds-second');
assert.equal(failed.canonical_status, 'Failed');
assert.equal(failed.canonical_result, null);
assert.equal(failed.evidence.result.error_type, 'ExecutorBodyTooLargeError');
assert.equal(failed.evidence.provider_outcome.response_eof, true);
assert.equal(failed.evidence.provider_outcome.engine_outcome_delivered, false);
assert.equal(failed.evidence.provider_outcome.response_bytes, 1202232);
assert.ok(failed.persisted_logs.length > 0);
assert.equal(peer.canonical_status, 'Success');
assert.equal(peer.canonical_result.value, 78);
assert.equal(failed.access_after_terminal_status, 401);
assert.equal(peer.access_after_terminal_status, 401);
console.log('Source-bound oversized child EOF/canonical failure qualified locally; no CPU-stop claim.');
