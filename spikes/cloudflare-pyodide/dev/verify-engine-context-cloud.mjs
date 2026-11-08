import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const read = name => JSON.parse(readFileSync(new URL(name, import.meta.url), 'utf8'));
const receipt = read('engine-context-cloud-v1.json');
const closure = read('engine-context-cloud-closure.json');
assert.equal(receipt.passed, true);
assert.equal(receipt.cloud_child_requests, 3);
assert.deepEqual(receipt.launch_counts, [1, 1]);
assert.deepEqual(receipt.native_busy_counts, [2, 2, 1, 0]);
assert.deepEqual(receipt.attempts.map(row => row.canonical_status), ['Failed', 'Success']);
assert.ok(receipt.attempts.every(row => row.sdk_close_returned && row.access_after_terminal_status === 401));
assert.equal(receipt.owned_test_running_after_cleanup, '');
assert.equal(receipt.relay_running_after_cleanup, '');
for (const key of ['duplicate_dispatch_reused_peer', 'stale_terminal_owner_rejected', 'lost_ack_reconnect_verified',
  'canonical_terminal_reconciliation_verified', 'fixture_worker_cascade_cleanup_verified'])
  assert.equal(receipt.contract_observations[key], true, key);
assert.equal(receipt.execution_overlay.source, '1796c6fcf68964aa2ff36421ccc7be697d6322f7');
const bytes = readFileSync(new URL('./executor/engine-context-cloud-entry.mjs', import.meta.url));
assert.equal(createHash('sha256').update(bytes).digest('hex'), receipt.candidate_files_sha256['dev/executor/engine-context-cloud-entry.mjs']);
assert.equal(closure.provider_matches_prebuilt, true);
assert.equal(closure.qualification_exit_code, 0);
assert.equal(closure.workers_dev, false); assert.equal(closure.previews, false);
assert.equal(closure.expiry, '0');
assert.deepEqual(closure.executor_secret_names, []); assert.deepEqual(closure.gateway_secret_names, []);
for (const key of ['tunnel_stopped', 'forwarding_stopped', 'temporary_profiles_removed']) assert.equal(closure[key], true);
assert.equal(receipt.physical_termination_verified, false);
console.log('Refreshed engine/worker/context passed deployed canonical SDK fixture; closure verified; arbitrary-Python stop remains unqualified.');
