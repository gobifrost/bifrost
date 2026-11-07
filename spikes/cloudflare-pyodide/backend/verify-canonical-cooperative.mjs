import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const receipt = JSON.parse(read('canonical-cooperative-v5.json'));
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.packet_sha256['project/backend/cooperative-runtime.mjs'], sha(execFileSync('git',
  ['show', '5f74ccea1:spikes/cloudflare-pyodide/backend/cooperative-runtime.mjs'])));
const staged = `export default ${JSON.stringify(read('cooperative_execution.py'))};`;
assert.equal(receipt.packet_sha256['project/backend/cooperative-code.mjs'], sha(staged));
assert.equal(receipt.staged_runtime_provenance.bundle_sha256, '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731');
const result = receipt.shared_pool_result;
assert.deepEqual(Object.values(result.launches), [1, 1]);
const attempts = Object.values(result.attempts);
assert.deepEqual(attempts.map(item => item.canonical_status).sort(), ['Failed', 'Success']);
for (const item of attempts) {
  assert.equal(item.access_after_terminal_status, 401);
  assert.equal(item.evidence.runtime_cleanup.sdk_close_returned, true);
}
for (const key of ['duplicate_dispatch_reused_peer', 'lost_ack_reconnect_verified',
  'stale_terminal_owner_rejected', 'canonical_terminal_reconciliation_verified',
  'terminal_obligations_released', 'fixture_worker_cascade_cleanup_verified']) assert.equal(result[key], true);
assert.equal(receipt.synthetic_https_effects.length, 4);
console.log('Canonical cooperative RPC normal/error evidence verified; cancellation and cloud remain unqualified.');
