import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url);
const read = name => JSON.parse(readFileSync(new URL('results/sdk-refresh/' + name, root), 'utf8').replace(/^\uFEFF/, ''));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = read('receipt.json'), interrupted = read('interrupted-close.json');
assert.equal(hash(readFileSync(new URL('backend/ct128-canonical-failure.py', root))), receipt.packet_sha256['ct128-canonical-failure.py']);
assert.equal(hash(execFileSync('git', ['show', 'ddb00323eab176783af02e5bb4fdc2acd651d07c:spikes/cloudflare-pyodide/backend/latest-sdk-probe-entry.mjs'])), interrupted.packet_sha256['project/backend/latest-sdk-probe-entry.mjs']);
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.sdk_overlay.sdk_commit, '139596497d57b219ddcc3616aa66ec58e1b3cf3c');
const proof = receipt.shared_pool_result;
assert.equal(proof.run_refresh_generation_redaction_verified, true);
assert.equal(proof.failed_replay_unchanged_verified, true);
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
for (const attempt of Object.values(proof.attempts)) assert.equal(attempt.evidence.runtime_cleanup.sdk_close_returned, true);
const subject = Object.values(proof.attempts).find(value => value.label === 'fails-first');
assert.equal(subject.canonical_error, 'refresh_probe original=[REDACTED] replacement=[REDACTED] refresh=[REDACTED]');
assert.ok(subject.evidence.api_calls.some(call => call.path === '/auth/refresh' && call.status === 200));
assert.deepEqual(interrupted.observation.body.interrupted_close, {
  waiter_cancelled: true, async_closed_after_release: true, owner_cleanup_done: true,
  sync_closed_at_cancel: false, sync_closed_without_followup: false, sync_closed_after_followup: true,
});
assert.equal(interrupted.running_after_cleanup, 'false');
assert.equal(interrupted.cloud_invocations, 0);
console.log('Updated SDK refresh/normal close passes; partial interrupted cleanup preserved, no physical-stop claim.');
