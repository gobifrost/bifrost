// Offline evidence verification; no Worker or API invocation.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root=new URL('../../../',import.meta.url);
const initial=JSON.parse(await readFile(new URL('../results/canonical-refresh-interruption.json',import.meta.url),'utf8'));
assert.equal(initial.process_exit_code,0);
for(const [snapshot,source] of [['refresh-interruption-initial-gate.txt','refresh-gate.mjs'],
  ['refresh-interruption-initial-entry.txt','authorized-local-entry.mjs']]) {
  assert.equal(createHash('sha256').update(await readFile(new URL('../results/'+snapshot,import.meta.url))).digest('hex'),
    initial.source_sha256['spikes/cloudflare-pyodide/backend/'+source]);
}
const receipt=JSON.parse(await readFile(new URL('../results/canonical-refresh-interruption-cleanup.json',import.meta.url),'utf8'));
assert.equal(receipt.process_exit_code,0);assert.equal(receipt.scenario,'interruption');
assert.notEqual(receipt.unknown_process_state,true);assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.physical_termination_verified,false);
const r=receipt.result;
for(const field of ['refresh_interruption_probe','canonical_claim_verified','canonical_terminal_consumption_verified',
  'lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified']) assert.equal(r[field],true,field);
assert.equal(r.launches,1);assert.equal(r.independent_document_value,70);assert.equal(r.cross_tenant_status,403);
assert.equal(r.credential_revoked_after_terminal_status,401);assert.equal(r.refresh_revoked_after_terminal_status,401);
const result=r.result.result.result;
for(const field of ['cancelled_refresh_waiter','server_response_received','replacement_adopted']) assert.equal(result[field],true,field);
assert.equal(result.pending_refresh_at_cancel,1);assert.equal(result.pending_refresh_after_adoption,0);
assert.equal(result.value,70);assert.equal(result.parent_secret_present,false);
assert.equal(result.credential_redaction_probe,'[REDACTED]');
assert.deepEqual(r.result.refresh_gate_events,['server_response_held','ready_observed','release_requested','response_forwarded']);
assert.deepEqual(r.result.api_calls.filter(c=>c.path==='/auth/refresh'),[{method:'POST',path:'/auth/refresh',status:200}]);
const refresh=r.result.api_calls.findIndex(c=>c.path==='/auth/refresh');
const write=r.result.api_calls.findIndex(c=>c.path.includes('/documents/upsert'));
assert.ok(write>refresh);
for(const [path,digest] of Object.entries(receipt.source_sha256)) {
  assert.equal(createHash('sha256').update(await readFile(new URL(path,root))).digest('hex'),digest,path);
}
const manifest=JSON.parse(await readFile(new URL('../results/'+receipt.runtime_manifest,import.meta.url),'utf8'));
assert.equal(manifest.payload_sha256,receipt.payload_sha256);
console.log('Actual API-issued refresh response, waiter cancellation, core completion/adoption and canonical revocation verified; no execution-cancellation or physical-stop claim');
