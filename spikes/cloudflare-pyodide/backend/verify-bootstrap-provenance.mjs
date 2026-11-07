import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root = new URL('../', import.meta.url);
const receipt = JSON.parse(readFileSync(new URL('results/bootstrap-provenance/receipt.json', root), 'utf8').replace(/^\uFEFF/, ''));
for (const name of ['workspace_prepare.py', 'test_workspace_prepare.py']) {
  const digest = createHash('sha256').update(readFileSync(new URL('backend/' + name, root))).digest('hex');
  assert.equal(digest, receipt.packet_sha256[name], name);
}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
const proof = receipt.shared_pool_result;
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
for (const key of ['api_preparation_verified', 'both_real_sdk_effects_held_concurrently',
  'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified',
  'canonical_settled_accounting_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
const focused = readFileSync(new URL('results/bootstrap-provenance/focused.txt', root), 'utf8');
assert.match(focused, /Ran 10 tests/);
assert.match(focused, /0 errors, 0 warnings/);
console.log('Bootstrap namespace refusal and real local workspace/SDK control verified; deployed isolation remains unproven.');
