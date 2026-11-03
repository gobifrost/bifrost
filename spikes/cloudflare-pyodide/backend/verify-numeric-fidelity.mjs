import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {parseLosslessJson} from './lossless-json.mjs';

const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL('results/numeric-fidelity/' + name, root), 'utf8').replace(/^\uFEFF/, '');
const before = JSON.parse(read('receipt-v1.json'));
assert.equal(before.qualification_passed, false);
assert.equal(before.commands.at(-1).exit_code, 1);
assert.match(read('failure-v1.txt'), /Integer transport fidelity lost/);
assert.equal(parseLosslessJson(read('receipt-v2.json')).qualification_passed, true);
const receipt = parseLosslessJson(read('receipt-v3.json'));
for (const [local, packet] of [
  ['shared-native-pool.py', 'shared-native-pool.py'],
  ['ct128-canonical-failure.py', 'ct128-canonical-failure.py'],
  ['authorized-local-entry.mjs', 'project/backend/authorized-local-entry.mjs'],
  ['lossless-json.mjs', 'project/backend/lossless-json.mjs'],
]) {
  const source = local === 'lossless-json.mjs' ? readFileSync(new URL('backend/' + local, root))
    : execFileSync('git', ['show', 'a63033f6575cb22c901e141351c3f6eb915881d8:spikes/cloudflare-pyodide/backend/' + local]);
  const digest = createHash('sha256').update(source).digest('hex');
  assert.equal(digest, receipt.packet_sha256[packet], local);
}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.deepEqual(receipt.running_after_cleanup, []);
const proof = receipt.shared_pool_result;
for (const key of ['exact_integer_roundtrip_verified', 'both_real_sdk_effects_held_concurrently',
  'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified',
  'canonical_settled_accounting_verified', 'mux_retired_after_canonical_ack',
  'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
const peer = Object.values(proof.attempts).find(attempt => attempt.label === 'succeeds-second');
assert.equal(peer.canonical_status, 'Success');
assert.equal(peer.canonical_result.value, 78);
assert.equal(JSON.stringify(peer.canonical_result.exact_integer), '9007199254740993');
assert.equal(JSON.stringify(peer.evidence.result.result.exact_integer), '9007199254740993');
assert.match(read('unit.txt'), /pass 8/);
console.log('Exact numeric parameters/results qualified in local upstream engine/SDK transport; cloud unproven.');
