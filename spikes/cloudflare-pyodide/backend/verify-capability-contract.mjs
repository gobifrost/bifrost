import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL('results/capability-contract/' + name, root), 'utf8');
const receipt = JSON.parse(read('receipt.json'));
for (const name of ['private_http_pool.py', 'shared-native-pool.py', 'test_private_http_pool.py']) {
  const digest = createHash('sha256').update(readFileSync(new URL('backend/' + name, root))).digest('hex');
  assert.equal(digest, receipt.packet_sha256[name], name);
}
assert.match(read('before.txt'), /FAILED \(errors=9\)/);
assert.match(read('focused.txt'), /Ran 25 tests[\s\S]*\bOK\b/);
assert.match(read('final-quality.txt'), /0 errors, 0 warnings/);
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
const proof = receipt.shared_pool_result;
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
for (const key of ['api_preparation_verified', 'run_refresh_generation_redaction_verified',
  'stale_terminal_owner_rejected', 'duplicate_dispatch_reused_peer', 'lost_ack_reconnect_verified',
  'failed_replay_unchanged_verified', 'canonical_settled_accounting_verified',
  'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
for (const attempt of Object.values(proof.attempts)) {
  assert.deepEqual(attempt.preparation.requirements, ['json', 'async-workflow', 'pinned-workspace', 'bifrost-http-sdk']);
}
console.log('Private declared-capability refusal and real upstream SDK control verified locally; no arbitrary-code admission claim.');
