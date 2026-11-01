import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {classifyCancellation} from './termination-evidence.mjs';
const read=async name=>JSON.parse((await readFile(new URL('../results/'+name,import.meta.url),'utf8')).replace(/^\uFEFF/,''));
const isolation=await read('isolation-cloud-fresh.json');
const engine=await read('engine-cloud-fresh.json');
const cpu=await read('cpu-limit-cloud-fresh.json');
const caught=await read('cpu-catch-cloud-fresh.json');
const ledger=await read('fresh-cloud-budget.json');
const states=isolation.results.filter(value=>value.request.action==='state');
assert.equal(states.length,4);
for(const {status,body} of states) {
  assert.equal(status,200);
  assert.equal(body.outcome.body.previous,null);
  assert.deepEqual(body.outcome.body.contamination,{env:null,module:false,filesystem:false});
  assert.equal(body.parent_canary_configured,true);
  assert.equal(body.outcome.body.parent_canary_visible,false);
}
assert.equal(new Set(states.map(value=>value.body.outcome.body.own_canary)).size,4);
assert.equal(isolation.results.find(value=>value.request.action==='egress').body.outcome.body.blocked,true);
const io=isolation.results.find(value=>value.request.action==='io').body;
assert.equal(io.abort_requested,true);
assert.deepEqual(io.effects,['before']);
const byFixture=Object.fromEntries(engine.results.map(value=>[value.request.fixture,value.body]));
assert.equal(byFixture.transform.status,'Success');
assert.equal(byFixture.transform.result.total,4950);
assert.equal(byFixture.async.result.async,true);
assert.equal(byFixture.failure.error_type,'ValueError');
assert.ok(byFixture.failure.logs.some(value=>value.message==='fixed cloud diagnostic log'));
assert.match(cpu.results[0].body.outcome.error,/CpuLimitExceeded/);
assert.deepEqual(cpu.results[0].body.effects,['before']);
assert.equal(caught.results[0].body.outcome.body.caught,'CpuLimitExceeded');
assert.deepEqual(caught.results[0].body.effects,['before','caught','after']);
assert.ok(ledger.reservations.length<=20);
const cpuAbort=isolation.results.find(value=>value.request.action==='cpu').body;
const cancellation=classifyCancellation(cpuAbort);
const report={supported_checks_passed:true,reserved_invocations:ledger.reservations.length,
  cpu_cancellation_test_exercised:cancellation.abort_request_observed,
  cpu_cancellation_classification:cancellation.classification,
  cpu_cancellation_prevents_later_effect:cancellation.cancellation_prevents_later_effect,
  provider_cpu_exception_is_uncatchable:!caught.results[0].body.effects.includes('after'),
  authorized_sdk_identity_verified:false,first_class_executor_acceptance:false};
// Historical reports are immutable evidence. Verification prints only; no writes.
console.log(JSON.stringify(report,null,2));
if(report.cpu_cancellation_prevents_later_effect!==true || !report.provider_cpu_exception_is_uncatchable) process.exitCode=1;
