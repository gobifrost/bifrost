import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, mkdtempSync, rmSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const read = path => readFileSync(new URL(path, import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(read('../results/https-deadline/v1.json'));
for (const name of ['ct128-canonical-failure.py', 'shared-native-pool.py', 'private_http_pool.py', 'https_policy_binding.py']) {
  const bytes = ['ct128-canonical-failure.py', 'shared-native-pool.py'].includes(name)
    ? execFileSync('git', ['show', 'bc6f644ffff25c000555acb6deb089f2cf5067b6:spikes/cloudflare-pyodide/backend/' + name]) : read(name);
  assert.equal(hash(bytes), receipt.packet_sha256[name], name);
}
for (const name of ['canonical-https-deadline-observer.mjs', 'approved-https-candidate.mjs', 'attempt-egress.mjs', 'fixture-https-policy.mjs']) {
  assert.equal(hash(read(name)), receipt.packet_sha256[`project/backend/${name}`], name);
}
const entry = read('../results/https-deadline/entry.mjs');
assert.equal(hash(entry), receipt.packet_sha256['project/backend/authorized-local-entry.mjs']);
const temporary = mkdtempSync(join(tmpdir(), 'https-deadline-proof-'));
try {
  const output = join(temporary, 'entry.mjs');
  const generator = join(temporary, 'prepare.mjs');
  writeFileSync(generator, execFileSync('git', ['show', '9dd2e6639c98e97b198362999a5dc4882a8c4336:spikes/cloudflare-pyodide/backend/prepare-canonical-https.mjs']));
  writeFileSync(join(temporary, 'authorized-local-entry.mjs'), read('authorized-local-entry.mjs'));
  execFileSync(process.execPath, [generator, output]);
  assert.deepEqual(readFileSync(output), entry);
} finally {rmSync(temporary, {recursive: true, force: true});}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
const proof = receipt.shared_pool_result;
assert.deepEqual(Object.values(proof.launches), [1, 1]);
assert.deepEqual(proof.native_busy_counts, [2, 1, 1, 0]);
assert.deepEqual(Object.values(proof.attempts).map(attempt => attempt.canonical_status).sort(), ['Success', 'Timeout']);
for (const field of ['original_cutoff_preserved', 'stale_deadline_owner_rejected',
  'timeout_committed_before_physical_settlement', 'late_success_preserved_timeout',
  'post_deadline_sdk_write_denied', 'denied_document_absent',
  'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[field], true, field);
assert.equal(proof.access_revoked_before_late_result_status, 401);
const before = proof.vendor_before_release, after = receipt.vendor_after_release;
assert.equal(before.received, true); assert.equal(before.committed, false); assert.equal(before.released, false);
assert.equal(before.effects.length, 4); assert.deepEqual(before.events, ['hold_received']);
assert.equal(after.committed, true); assert.equal(after.released, true);
assert.deepEqual(after.events, ['hold_received', 'parent_release', 'hold_committed']);
assert.equal(after.effects.length, 5);
assert.deepEqual(after.effects.at(-1), {path: '/hold', body: 'fails-first', vendor_authorization_matches: true});
assert.ok(after.effects.every(effect => effect.body !== 'late-forbidden'));
const timedOut = Object.values(proof.attempts).find(attempt => attempt.canonical_status === 'Timeout');
assert.equal(timedOut.evidence.result.success, true);
assert.equal(timedOut.evidence.runtime_cleanup.sdk_close_returned, true);
console.log('Local in-flight vendor commit after Timeout retained accounting; new writes refused, no cancellation claim.');
