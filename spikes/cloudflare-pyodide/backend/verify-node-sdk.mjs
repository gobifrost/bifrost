// Offline verification of the preserved guest receipt; performs no API calls.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../',import.meta.url);
const receipt=JSON.parse(readFileSync(new URL('results/node-sdk-resume/success.json',root)));
assert.equal(receipt.qualification_passed,true);
assert.deepEqual(receipt.running_after_cleanup,[]);
assert.equal(receipt.volumes_deleted,false);
assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.physical_termination_verified,false);
for(const [name,expected] of Object.entries(receipt.packet_sha256)) {
  assert.equal(createHash('sha256').update(readFileSync(new URL('backend/'+name,root))).digest('hex'),expected);
}
const run=receipt.canonical_result;
for(const key of ['canonical_claim_verified','canonical_terminal_consumption_verified','lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified']) assert.equal(run[key],true,key);
assert.equal(run.launches,1);
assert.equal(run.independent_document_value,70);
assert.equal(run.cross_tenant_status,403);
assert.equal(run.credential_revoked_after_terminal_status,401);
assert.equal(run.persisted_log_count,1);
assert.equal(run.result.runtime,'official-pyodide-on-node');
assert.equal(run.result.result.result.value,70);
assert.equal(run.result.result.result.credential_redaction_probe,'[REDACTED]');
assert.equal(run.result.result.result.parent_secret_present,false);
const calls=run.result.api_calls;
assert.equal(calls.filter(c=>c.path.endsWith('/documents/upsert')&&c.status===200).length,1);
assert.ok(calls.some(c=>c.path==='/api/sdk/config/get'&&c.status===200));
assert.ok(calls.some(c=>c.path==='/api/sdk/modules-resolve'&&c.status===200));
assert.ok(calls.some(c=>c.path.startsWith('/api/sdk/modules/')&&c.status===200));
console.log('Preserved real SDK/canonical-consumer evidence verified; Cloudflare and physical termination remain unqualified.');
