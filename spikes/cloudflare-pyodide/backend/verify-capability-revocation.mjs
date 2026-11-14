import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const read = path => readFileSync(new URL(path, import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(read('../results/capability-revocation/v6.json'));
for (const name of ['capability_revocation.py', 'test_capability_revocation.py',
  'capability-revocation-db.py', 'ct128-canonical-failure.py']) {
  const bytes = name === 'ct128-canonical-failure.py'
    ? execFileSync('git', ['show', '9dd2e6639c98e97b198362999a5dc4882a8c4336:spikes/cloudflare-pyodide/backend/' + name]) : read(name);
  assert.equal(hash(bytes), receipt.packet_sha256[name], name);
}
for (const [source, packet] of [['explicit-revoke-entry.mjs', 'authorized-local-entry.mjs'],
  ['https-disconnect-entry.mjs', 'https-disconnect-entry.mjs'],
  ['explicit-revoke-observer.mjs', 'explicit-revoke-observer.mjs'],
  ['invocation-stream.mjs', 'invocation-stream.mjs'],
  ['approved-https-candidate.mjs', 'approved-https-candidate.mjs']]) {
  assert.equal(hash(read(source)), receipt.packet_sha256[`project/backend/${packet}`], source);
}
const previous = JSON.parse(read('../results/capability-revocation/v4.json'));
const original = read('../results/capability-revocation/before-snapshot.py');
assert.equal(hash(original), previous.packet_sha256['capability_revocation.py']);
assert.equal(read('capability_revocation.py').toString().replace('                    response = dict(response)\n', ''), original.toString());
const failed = JSON.parse(read('../results/capability-revocation/v3.json'));
assert.equal(failed.qualification_passed, false);
assert.deepEqual(failed.running_after_cleanup, []);
assert.equal(receipt.qualification_passed, true);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.provider_state_before_cleanup.OOMKilled, false);
assert.equal(receipt.cloud_invocations, 0);
const proof = receipt.capability_revocation_result;
assert.equal(proof.canonical_execution_verified, false);
assert.equal(proof.physical_termination_verified, false);
for (const field of ['stale_owner_refused_before_close', 'owner_rotation_observed_waiting_on_postgres_lock',
  'retirement_refused_while_unsettled', 'completed_collection_retired', 'revocation_did_not_settle_collection']) {
  assert.equal(proof[field], true, field);
}
assert.equal(proof.revocation.capabilities_revoked, true);
assert.equal(proof.revocation.physical_stopped, false);
assert.equal(proof.revocation.execution_settled, false);
assert.equal(proof.physical_close_calls, 1);
assert.deepEqual(proof.evidence.result.result.statuses, [200, 200, 410]);
assert.equal(proof.vendor_before_release.committed, false);
assert.equal(proof.vendor_after_release.committed, true);
assert.deepEqual(proof.vendor_after_release.effects, ['/before', '/hold']);
const allowed = new Set(['api/src/worker/remote/fencing.py', 'api/src/worker/remote/ownership.py']);
for (const line of read('../results/capability-revocation/native-authority.sha256').toString().trim().split(/\r?\n/)) {
  const [digest, absolute] = line.split(/\s+/);
  const relative = absolute.slice(absolute.indexOf('/api/') + 1);
  assert.ok(allowed.delete(relative));
  assert.equal(hash(execFileSync('git', ['show', `1d33150eb376b71f8a81b427920eda1b0447f829:${relative}`])), digest);
}
assert.equal(allowed.size, 0);
console.log('Real PostgreSQL fence and explicit Pyodide capability revocation verified; physical/canonical cancellation unqualified.');
