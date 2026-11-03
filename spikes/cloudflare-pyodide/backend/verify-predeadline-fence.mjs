import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL('results/predeadline-fence/' + name, root), 'utf8').replace(/^\uFEFF/, '');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(read('receipt.json'));
assert.equal(hash(readFileSync(new URL('backend/private_http_pool.py', root))), receipt.packet_sha256['private_http_pool.py']);
const tested = execFileSync('git', ['show', '69c92680f5ef57470910f5578b0d153943e423d0:spikes/cloudflare-pyodide/backend/test_private_http_pool.py']);
assert.ok(read('source-readback.sha256').includes(hash(tested)));
assert.equal(receipt.qualification_passed, true);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
const proof = receipt.shared_pool_result;
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
for (const key of ['both_real_sdk_effects_held_concurrently', 'failed_replay_unchanged_verified',
  'stale_terminal_owner_rejected', 'duplicate_dispatch_reused_peer',
  'canonical_settled_accounting_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
console.log('Source-bound pre-deadline fencing and real local engine/SDK control verified.');
