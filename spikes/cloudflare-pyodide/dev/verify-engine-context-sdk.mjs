import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const receipt = JSON.parse(readFileSync(new URL('engine-context-sdk-v1.json', import.meta.url), 'utf8'));
assert.equal(receipt.qualification_passed, true);
assert.deepEqual(receipt.command_exit_codes, [0, 0, 0, 0]);
assert.equal(receipt.runtime_engine_source, receipt.execution_overlay.source);
assert.equal(receipt.execution_overlay.accepted_bundle_promoted, false);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.provider_oom, false);
assert.deepEqual(receipt.launch_counts, [1, 1]);
assert.deepEqual(receipt.native_busy_counts, [2, 2, 1, 0]);
assert.deepEqual(receipt.attempts.map(row => row.canonical_status), ['Failed', 'Success']);
assert.ok(receipt.attempts.every(row => row.sdk_close_returned && row.access_after_terminal_status === 401));
for (const key of ['both_real_sdk_effects_held_concurrently', 'stale_terminal_owner_rejected',
  'duplicate_dispatch_reused_peer', 'lost_ack_reconnect_verified', 'canonical_terminal_reconciliation_verified',
  'canonical_settled_accounting_verified', 'fixture_worker_cascade_cleanup_verified'])
  assert.equal(receipt.contract_observations[key], true, key);
for (const name of ['canonical-local-entry.mjs', 'engine-context-local-entry.mjs']) {
  const digest = createHash('sha256').update(readFileSync(new URL('./executor/'+name, import.meta.url))).digest('hex');
  assert.equal(digest, receipt.packet_sha256['project/dev/executor/'+name]);
}
for (const attempt of receipt.attempts)
  assert.deepEqual(attempt.runtime_identity_context, {is_agent: false, execution_origin: 'unknown',
    agent_id: null, original_user_is_platform_admin: false, original_user_id: null});
console.log('Refreshed engine/worker/context passed real run-scoped SDK and canonical fixture locally; no cloud or physical-stop promotion.');
