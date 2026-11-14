import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url);
const read = name => JSON.parse(readFileSync(new URL('results/owned-sdk-close/' + name + '.json', root), 'utf8').replace(/^\uFEFF/, ''));
const hash = value => createHash('sha256').update(value).digest('hex');
const probe = read('probe');
assert.equal(hash(execFileSync('git', ['show', '0b3b84f8ebb2406bb9cd627b976065c1e97bb044:spikes/cloudflare-pyodide/backend/latest-sdk-probe-entry.mjs'])), probe.packet_sha256['project/backend/latest-sdk-probe-entry.mjs']);
assert.deepEqual(probe.observation.body.interrupted_close, {
  waiter_cancelled: true, async_closed_after_release: true, owner_cleanup_done: true,
  sync_closed_at_cancel: false, sync_closed_without_followup: true,
  owned_pending_at_cancel: 1, owned_pending_after_completion: 0, sync_closed_after_followup: true,
});
assert.equal(probe.running_after_cleanup, 'false');
for (const name of ['updated', 'baseline']) {
  const receipt = read(name);
  assert.equal(receipt.qualification_passed, true);
  assert.equal(receipt.cloud_invocations, 0);
  assert.equal(receipt.physical_termination_verified, false);
  assert.deepEqual(receipt.running_after_cleanup, []);
  assert.equal(hash(readFileSync(new URL('backend/sdk-cleanup-code.mjs', root))), receipt.packet_sha256['project/backend/sdk-cleanup-code.mjs']);
  let code = readFileSync(new URL('backend/runtime-code.mjs', root), 'utf8');
  if (name === 'updated') code = code.replace("'./generated-runtime.mjs'", "'./generated-runtime-sdk.mjs'");
  assert.equal(hash(code), receipt.packet_sha256['project/backend/runtime-code.mjs']);
  const proof = receipt.shared_pool_result;
  assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
  assert.equal(proof.failed_replay_unchanged_verified, true);
  assert.equal(proof.canonical_settled_accounting_verified, true);
  for (const attempt of Object.values(proof.attempts)) assert.equal(attempt.evidence.runtime_cleanup.sdk_close_returned, true);
  if (name === 'updated') assert.equal(proof.run_refresh_generation_redaction_verified, true);
}
console.log('Owned SDK close passes cooperative cancellation and both real workflow controls locally; platform cancellation unproven.');
