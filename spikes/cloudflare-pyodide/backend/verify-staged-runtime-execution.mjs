import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../results/staged-runtime-execution/',import.meta.url);
const receipt=JSON.parse(readFileSync(new URL('v1.json',root)));
const preflight=JSON.parse(readFileSync(new URL('preflight.json',root)));
const hash=value=>createHash('sha256').update(value).digest('hex');
for(const file of ['ct128-canonical-failure.py','staged_runtime_input.py','probe_staged_runtime_input.py'])
  assert.equal(hash(readFileSync(new URL(file,import.meta.url))),receipt.packet_sha256[file]);
assert.equal(receipt.qualification_passed,true);
assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.physical_termination_verified,false);
assert.deepEqual(receipt.running_after_cleanup,[]);
assert.equal(receipt.packet_sha256['project/backend/generated-runtime.mjs'],'61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731');
for(const [name,digest] of Object.entries(receipt.staged_runtime_provenance.loader_files_sha256)) {
  assert.equal(hash(readFileSync(new URL(name,import.meta.url))),digest);
  assert.equal(receipt.packet_sha256['project/backend/'+name],digest);
}
assert.deepEqual(preflight.refused,['generated-runtime.mjs','runtime-code.mjs','sdk-cleanup-code.mjs','runtime-provenance.json']);
assert.equal(preflight.cloud_invocations,0);
const proof=receipt.shared_pool_result;
for(const key of ['single_native_pool_verified','both_real_sdk_effects_held_concurrently','stale_terminal_owner_rejected',
  'failed_replay_unchanged_verified','peer_live_after_failure_reconcile','duplicate_dispatch_reused_peer',
  'lost_ack_reconnect_verified','canonical_settled_accounting_verified','mux_retired_after_canonical_ack',
  'api_preparation_verified','fixture_worker_cascade_cleanup_verified'])assert.equal(proof[key],true);
assert.deepEqual(proof.native_busy_counts,[2,2,1,0]);
assert.deepEqual(Object.values(proof.launches),[1,1]);
assert.deepEqual(Object.values(proof.attempts).map(attempt=>attempt.canonical_status).sort(),['Failed','Success']);
for(const attempt of Object.values(proof.attempts)) {
  assert.equal(attempt.evidence.runtime_cleanup.sdk_close_returned,true);
  assert.equal(attempt.access_after_terminal_status,401);
}
console.log('Fresh staged runtime executes actual local engine/SDK and canonical replay/cleanup; no deployed or physical-stop acceptance.');
