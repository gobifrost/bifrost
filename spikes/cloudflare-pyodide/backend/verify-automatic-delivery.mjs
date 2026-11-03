import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/auto-delivery/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const raw = await read('auto-delivery-v1/receipt.json');
assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
const receipt = JSON.parse(raw);
assert.equal(receipt.qualification_passed, true);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.provider_state_before_cleanup.Running, true);
assert.equal(receipt.provider_state_before_cleanup.OOMKilled, false);
for (const name of ['private_http_pool.py', 'test_private_http_pool.py', 'attempt_journal.py',
  'test_attempt_delivery.py', 'shared-native-pool.py', 'ct128-canonical-failure.py']) {
  const source = execFileSync('git', ['show', 'ebc2be4a095446fa73d3983a50f799d5b350b68a:spikes/cloudflare-pyodide/backend/' + name]);
  assert.equal(createHash('sha256').update(source).digest('hex'), receipt.packet_sha256[name], name);
}
const probe = receipt.shared_pool_result;
for (const field of ['automatic_terminal_delivery_verified', 'both_real_sdk_effects_held_concurrently',
  'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified', 'lost_ack_reconnect_verified',
  'canonical_peer_accounting_verified', 'canonical_settled_accounting_verified',
  'terminal_obligations_released', 'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) {
  assert.equal(probe[field], true, field);
}
assert.deepEqual(probe.native_busy_counts, [2, 2, 1, 0]);
for (const [id, attempt] of Object.entries(probe.attempts)) {
  assert.equal(probe.launches[id], 1);
  assert.equal(attempt.access_after_terminal_status, 401);
  assert.equal(attempt.canonical_status, attempt.label === 'fails-first' ? 'Failed' : 'Success');
  if (attempt.label === 'succeeds-second') assert.equal(attempt.canonical_result.value, 78);
}
assert.match(await read('auto-delivery-quality-v1/unit.txt'), /Ran 27 tests[\s\S]*\bOK\b/);
assert.match(await read('auto-delivery-quality-v1/quality.txt'), /0 errors, 0 warnings/);
console.log('Automatic upstream terminal delivery, lost-ACK recovery, two actual engine/SDK runs and cleanup verified locally; cloud cancellation remains unqualified.');
