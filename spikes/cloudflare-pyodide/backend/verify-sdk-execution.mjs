import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url);
const read = name => JSON.parse(readFileSync(new URL('results/sdk-execution/' + name, root), 'utf8').replace(/^\uFEFF/, ''));
const receipt = read('receipt-v1.json'), manifest = read('manifest.json');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
assert.deepEqual(receipt.sdk_overlay, manifest);
assert.equal(manifest.sdk_commit, '139596497d57b219ddcc3616aa66ec58e1b3cf3c');
assert.equal(manifest.bundle_sha256, '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731');
assert.equal(receipt.packet_sha256['project/backend/generated-runtime-sdk.mjs'], manifest.bundle_sha256);
assert.equal(manifest.preserved_module_count, 453);
assert.equal(manifest.module_count, 466);
assert.ok(Object.keys(manifest.overlay_sha256).some(name => /dist-info\/.*LICENSE/.test(name)));
for (const path of ['bifrost/client.py', 'bifrost/_tls.py', 'bifrost/_workspace_lock.py']) {
  assert.equal(hash(execFileSync('git', ['show', manifest.sdk_commit + ':api/' + path])), manifest.overlay_sha256[path]);
}
for (const name of ['ct128-canonical-failure.py', 'add-certifi-overlay.py', 'merge-sdk-overlay.mjs']) {
  const source = name === 'ct128-canonical-failure.py'
    ? execFileSync('git', ['show', '151ee99625f5b3aa55ccccd285511f8ceda7b08d:spikes/cloudflare-pyodide/backend/' + name])
    : readFileSync(new URL('backend/' + name, root));
  assert.equal(hash(source), receipt.packet_sha256[name], name);
}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
const proof = receipt.shared_pool_result;
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
for (const key of ['both_real_sdk_effects_held_concurrently', 'stale_terminal_owner_rejected',
  'failed_replay_unchanged_verified', 'duplicate_dispatch_reused_peer',
  'canonical_settled_accounting_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
const peer = Object.values(proof.attempts).find(value => value.label === 'succeeds-second');
assert.equal(peer.canonical_status, 'Success');
assert.equal(peer.canonical_result.value, 78);
assert.ok(peer.evidence.api_calls.some(call => call.path.includes('/documents/upsert') && call.status === 200));
assert.equal(peer.access_after_terminal_status, 401);
console.log('Exact updated upstream SDK executes real local engine/API workflows; TLS/deployed guarantees remain unqualified.');
