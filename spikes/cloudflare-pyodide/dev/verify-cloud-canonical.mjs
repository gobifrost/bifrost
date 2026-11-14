import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const read = name => JSON.parse(readFileSync(new URL(name, import.meta.url), 'utf8'));
const receipt = read('cloud-canonical-acceptance.json');
assert.deepEqual(receipt.runs.map(row => row.passed), [false, false, false, true]);
assert.deepEqual(receipt.runs.map(row => row.child_requests), [3, 0, 3, 3]);
const accepted = receipt.runs[3];
assert.deepEqual(accepted.launch_counts, [1, 1]);
assert.deepEqual(accepted.native_busy_counts, [2, 2, 1, 0]);
assert.deepEqual(accepted.attempts.map(row => row.canonical_status), ['Failed', 'Success']);
assert.ok(accepted.attempts.every(row => row.sdk_close_returned && row.access_after_terminal_status === 401));
for (const key of ['duplicate_dispatch_reused_peer', 'stale_terminal_owner_rejected', 'lost_ack_reconnect_verified',
  'canonical_terminal_reconciliation_verified', 'fixture_worker_cascade_cleanup_verified'])
  assert.equal(accepted.contract_observations[key], true, key);
assert.equal(accepted.physical_termination_verified, false);
assert.equal(receipt.owned_test_running_after_cleanup, '');
assert.equal(receipt.relay_running_after_cleanup, '');
for (const name of ['canonical-entry.mjs', 'canonical-api.mjs']) {
  const bytes = readFileSync(new URL('./executor/'+name, import.meta.url));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), receipt.candidate_files_sha256['dev/executor/'+name]);
}
const closure = read('cloud-canonical-closure.json');
for (const target of [closure.executor, closure.gateway]) {
  assert.equal(target.workers_dev, false); assert.equal(target.previews, false);
  assert.equal(target.expiry, '0'); assert.deepEqual(target.secret_names, []);
}
assert.equal(closure.tunnel_stopped, true);
console.log('Pinned deployed canonical fixture passed; closed afterward; arbitrary admission and physical stop remain unqualified.');
