// Offline success verification; never invokes a runtime or API.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root=new URL('../../../',import.meta.url);
const receipt=JSON.parse(await readFile(new URL('../results/canonical-refresh-routed.json',import.meta.url),'utf8'));
assert.equal(receipt.process_exit_code,0);assert.equal(receipt.phase,'observed');
assert.notEqual(receipt.unknown_process_state,true);
assert.equal(receipt.cloud_invocations,0);assert.equal(receipt.physical_termination_verified,false);
const r=receipt.result;
for(const field of ['refresh_probe','canonical_claim_verified','canonical_terminal_consumption_verified',
  'lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified']) assert.equal(r[field],true,field);
assert.equal(r.launches,1);assert.equal(r.cross_tenant_status,403);
assert.equal(r.credential_revoked_after_terminal_status,401);
assert.equal(r.refresh_revoked_after_terminal_status,401);
assert.equal(r.independent_document_value,70);
assert.equal(r.result.result.success,true);
assert.equal(r.result.result.result.value,70);
assert.equal(r.result.result.result.parent_secret_present,false);
assert.equal(r.result.result.result.credential_redaction_probe,'[REDACTED]');
assert.equal(r.result.api_calls.filter(c=>c.path==='/auth/refresh' && c.status===200).length,1);
for(const [path,digest] of Object.entries(receipt.source_sha256)) {
  assert.equal(createHash('sha256').update(await readFile(new URL(path,root))).digest('hex'),digest,path);
}
console.log('Actual local SDK refresh, canonical completion/reconciliation, revocation and provenance verified; no cloud or physical-stop claim');
