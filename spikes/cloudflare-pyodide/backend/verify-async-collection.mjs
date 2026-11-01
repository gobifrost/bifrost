// Local before/after qualification; Running is not successful recovery.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root=new URL('../../../',import.meta.url);
for(const [name,loss] of [['canonical-refresh-async-collection',false],['canonical-refresh-response-loss-async',true]]) {
  const receipt=JSON.parse(await readFile(new URL('../results/'+name+'.json',import.meta.url)));
  assert.equal(receipt.process_exit_code,0);
  assert.notEqual(receipt.unknown_process_state,true);
  const r=receipt.result;
  assert.equal(r.routing_returned_before_collection_wait,true);
  assert.equal(r.launches,1);
  assert.equal(r.cloud_invocations,0);
  assert.equal(r.physical_termination_verified,false);
  assert.equal(r.independent_document_value,70);
  if(loss) {
    assert.equal(r.recovery_acceptance,false);
    assert.equal(r.provider_success_before_drop,true);
    assert.equal(r.collector_error_type,'RemoteProtocolError');
    assert.equal(r.canonical_status,'Running');
    assert.equal(r.canonical_error_message,null);
    assert.equal(r.canonical_success_result_missing,true);
    assert.equal(r.credential_active_pending_recovery_status,200);
    assert.equal(r.credential_revoked_by_fixture_cleanup_status,401);
    assert.equal(r.fixture_execution_cleanup_verified,true);
    assert.equal(r.fixture_worker_cleanup_verified,true);
  } else {
    for(const key of ['transport_waiter_probe','cancelled_waiter_collection_retained',
      'canonical_claim_verified','canonical_terminal_consumption_verified',
      'lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified']) assert.equal(r[key],true,key);
    assert.equal(r.credential_revoked_after_terminal_status,401);
    assert.equal(r.result.result.success,true);
    assert.equal(r.result.result.result.credential_redaction_probe,'[REDACTED]');
  }
  for(const [path,digest] of Object.entries(receipt.source_sha256)) {
    assert.equal(createHash('sha256').update(await readFile(new URL(path,root))).digest('hex'),digest,path);
  }
}
console.log('Asynchronous routing verified: canonical success/reconnect retained; lost response stays Running without fabricated failure or replay');
