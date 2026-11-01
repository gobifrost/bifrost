// Actual local Worker/SDK result; simulated control link is explicitly checked.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const args=process.argv.slice(2);
if(args.length && (args.length!==2 || args[0]!=='--commit' || !/^[a-f0-9]{40}$/.test(args[1]))) {
  throw new Error('Use no arguments or --commit with an exact historical commit');
}
const root=new URL('../../../',import.meta.url);
const receipt=JSON.parse(await readFile(new URL('../results/canonical-refresh-retained-response.json',import.meta.url)));
assert.equal(receipt.phase,'observed');
assert.equal(receipt.process_exit_code,0);
assert.notEqual(receipt.unknown_process_state,true);
const r=receipt.result;
for(const key of ['provider_retained_response_probe','provider_success_before_drop',
  'routing_returned_before_collection_wait','upstream_retained_before_response_drop',
  'stale_retained_owner_rejected','retained_event_survived_lost_ack','local_retained_result_consumed',
  'local_recovery_verified','upstream_ack_consumed','fixture_worker_cleanup_verified',
  'fixture_execution_cleanup_verified','control_transport_simulated']) assert.equal(r[key],true,key);
assert.equal(r.physical_termination_verified,false);
assert.equal(r.recovery_acceptance,false);
assert.equal(r.cloud_invocations,0);
assert.equal(r.collector_error_type,'RemoteProtocolError');
assert.equal(r.canonical_status,'Running');
assert.equal(r.canonical_status_after_recovery,'Success');
assert.equal(r.independent_document_value,70);
assert.equal(r.canonical_result_value,70);
assert.equal(r.launches,1);
assert.equal(r.successful_upserts,1);
assert.ok(r.persisted_log_count>0);
assert.equal(r.credential_active_pending_recovery_status,200);
assert.equal(r.credential_active_after_owner_reconnect_status,200);
assert.equal(r.credential_revoked_by_canonical_success_status,401);
assert.equal(r.refresh_revoked_by_canonical_success_status,401);
assert.equal(r.credential_revoked_by_fixture_cleanup_status,401);
assert.equal(r.provider_execution_id,r.canonical_execution_id);
assert.equal(r.provider_worker_id,r.canonical_worker_id);
assert.ok(Object.values(r.workspace_sha256).includes(r.observed_code_hash));
for(const [path,digest] of Object.entries(receipt.source_sha256)) {
  const source=args.length ? execFileSync('git',['show',`${args[1]}:${path}`],{cwd:root}) : await readFile(new URL(path,root));
  assert.equal(createHash('sha256').update(source).digest('hex'),digest,path);
}
const manifest=JSON.parse(await readFile(new URL('../results/'+receipt.runtime_manifest,import.meta.url)));
assert.equal(receipt.payload_sha256,manifest.payload_sha256);
console.log('Actual local Worker/SDK result recovered through upstream retention after TCP response loss; one launch, canonical commit and revocation verified');
