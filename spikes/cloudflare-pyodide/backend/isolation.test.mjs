import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const results=[];
async function probe(body) {
  const response=await fetch('http://127.0.0.1:8795',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body),signal:AbortSignal.timeout(30000)});
  const result={request:body,status:response.status,body:await response.json()};
  results.push(result);
  return result.body;
}
try {
  const first=await probe({action:'state',marker:'tenant-A'});
  assert.equal(first.revision,5,'Expected current supervisor code, not a pre-reload version');
  const second=await probe({action:'state',marker:'tenant-B'});
  assert.equal(first.outcome.body.previous,null);
  assert.equal(second.outcome.body.previous,null);
  assert.notEqual(first.outcome.body.own_canary,second.outcome.body.own_canary);
  for(const result of [first,second]) {
    assert.deepEqual(result.outcome.body.contamination,{env:null,module:false,filesystem:false});
    assert.equal(result.parent_canary_configured,true,'Positive control: parent has the synthetic binding');
    assert.equal(result.outcome.body.parent_canary_visible,false);
  }
  const parallel=await Promise.all([probe({action:'state',marker:'parallel-A'}),probe({action:'state',marker:'parallel-B'})]);
  for(const result of parallel) {
    assert.equal(result.outcome.body.previous,null);
    assert.deepEqual(result.outcome.body.contamination,{env:null,module:false,filesystem:false});
  }
  const egress=await probe({action:'egress'});
  assert.equal(egress.outcome.body.blocked,true);
  for(const action of ['io','cpu']) {
    const result=await probe({action,cancel:true});
    assert.ok(result.effects.includes('before'),'Child must start before cancellation is tested');
    assert.equal(result.abort_requested,true);
    assert.ok(result.outcome.error_type,'Cancellation must interrupt the invocation');
    assert.ok(!result.effects.includes('after'),'Canceled child must not deliver a later effect');
  }
  console.log('Fresh Python runtime, canary separation, egress denial and bounded cancellation probes passed.');
} finally {
  await writeFile(new URL('../results/isolation-local.json',import.meta.url),JSON.stringify({runtime:'local-workerd',results},null,2)+'\n');
}
