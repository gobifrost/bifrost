import assert from 'node:assert/strict';
import {readFileSync, mkdtempSync, rmSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const read = path => readFileSync(new URL(path, import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(read('../results/canonical-cancel/v2.json'));
for (const name of ['ct128-canonical-failure.py', 'shared-native-pool.py', 'capability_revocation.py', 'private_http_pool.py']) {
  assert.equal(hash(read(name)), receipt.packet_sha256[name], name);
}
const entry = read('../results/canonical-cancel/entry.mjs');
assert.equal(hash(entry), receipt.packet_sha256['project/backend/authorized-local-entry.mjs']);
const temporary = mkdtempSync(join(tmpdir(), 'canonical-cancel-proof-'));
try {
  const output = join(temporary, 'entry.mjs');
  execFileSync(process.execPath, [fileURLToPath(new URL('prepare-canonical-https.mjs', import.meta.url)), output]);
  assert.deepEqual(readFileSync(output), entry);
} finally {rmSync(temporary, {recursive: true, force: true});}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
const proof = receipt.shared_pool_result;
assert.equal(proof.canonical_cancel_probe, true);
assert.deepEqual(proof.cancel_api_statuses, [200, 200]);
assert.equal(proof.live_peer_cancel_status, 403);
assert.equal(proof.ended_peer_cancel_status, 401);
assert.equal(proof.cancelling_credential_status, 200);
assert.equal(proof.physical_cancellation_guard_preserved, true);
assert.equal(proof.full_native_cancel_delivery_verified, false);
assert.equal(proof.capability_revocation_ack.physical_stopped, false);
assert.equal(proof.capability_revocation_ack.execution_settled, false);
assert.equal(proof.accounting_held_while_cancelling, true);
assert.equal(proof.cancel_flag_absent_at_cleanup, true);
assert.equal(proof.natural_success_after_cancel_request, true);
assert.deepEqual(proof.native_busy_counts, [2, 1, 1, 0]);
assert.deepEqual(Object.values(proof.launches), [1, 1]);
assert.ok(Object.values(proof.attempts).every(attempt => attempt.canonical_status === 'Success' &&
  attempt.access_after_terminal_status === 401 && attempt.evidence.runtime_cleanup.sdk_close_returned));
const target = Object.values(proof.attempts).find(attempt => attempt.label === 'fails-first');
assert.equal(target.canonical_result.value, 70);
assert.ok(target.evidence.api_calls.some(call => call.status === 410 && call.path.includes('/documents/upsert')));
assert.equal(proof.vendor_before_cancel_release.committed, false);
assert.equal(receipt.vendor_after_release.committed, true);
assert.equal(receipt.synthetic_https_effects.length, 5);
const allowed = new Set(['api/src/routers/executions.py', 'api/src/repositories/executions.py',
  'api/src/models/enums.py', 'api/src/services/authorization/authz.py']);
for (const line of read('../results/canonical-cancel/native-authority.sha256').toString().trim().split(/\r?\n/)) {
  const [digest, absolute] = line.split(/\s+/), relative = absolute.slice(absolute.indexOf('/api/') + 1);
  assert.ok(allowed.delete(relative));
  assert.equal(hash(execFileSync('git', ['show', `1d33150eb376b71f8a81b427920eda1b0447f829:${relative}`])), digest);
}
assert.equal(allowed.size, 0);
console.log('Authorized canonical Cancelling + effect revocation + natural Success verified; physical cancellation unqualified.');
