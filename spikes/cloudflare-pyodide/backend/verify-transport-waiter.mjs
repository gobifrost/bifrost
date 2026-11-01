// Offline evidence check; never invokes a provider or an API.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const receipt=JSON.parse(await readFile(new URL('../results/canonical-refresh-transport-waiter.json',import.meta.url)));
const report=receipt.result;
assert.equal(receipt.process_exit_code,0);
assert.equal(receipt.unknown_process_state,undefined);
assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.physical_termination_verified,false);
for(const key of ['transport_waiter_probe','provider_request_body_sent',
  'cancelled_waiter_collection_retained','canonical_claim_verified',
  'canonical_terminal_consumption_verified','lost_ack_reconnect_verified',
  'completed_claim_retained_as_upstream_specifies','fixture_worker_cascade_cleanup_verified']) {
  assert.equal(report[key],true,key);
}
assert.equal(report.launches,1);
assert.equal(report.independent_document_value,70);
assert.equal(report.cross_tenant_status,403);
assert.equal(report.credential_revoked_after_terminal_status,401);
assert.ok(report.persisted_log_count>0);
assert.equal(report.result.result.success,true);
assert.equal(report.result.result.result.value,70);
assert.equal(report.result.result.result.parent_secret_present,false);
assert.equal(report.result.result.result.credential_redaction_probe,'[REDACTED]');
assert.equal(report.result.api_calls.filter(c=>c.path.endsWith('/documents/upsert') && c.status===200).length,1);
assert.ok(report.result.api_calls.some(c=>c.path==='/api/sdk/config/get' && c.status===200));
const repo=new URL('../../../',import.meta.url);
for(const [path,digest] of Object.entries(receipt.source_sha256)) {
  assert.equal(createHash('sha256').update(await readFile(new URL(path,repo))).digest('hex'),digest,path);
}
const manifest=JSON.parse(await readFile(new URL('../results/'+receipt.runtime_manifest,import.meta.url)));
assert.equal(receipt.payload_sha256,manifest.payload_sha256);
console.log('Actual local engine/SDK result retained after in-flight waiter cancellation; canonical reconciliation and cleanup verified');
