import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {classifyCancellation} from './termination-evidence.mjs';

test('an abort that never fired is not an exercised cancellation test',()=>{
  for(const effects of [['before'],['before','after']]) {
    const r=classifyCancellation({abort_requested:false,effects});
    assert.equal(r.classification,'not_exercised');
    assert.equal(r.cancellation_prevents_later_effect,null);
    assert.equal(r.physical_termination_verified,false);
  }
});

test('observed effects or bounded absence never become physical-stop proof',()=>{
  for(const effects of [['before'],['before','after']]) {
    const r=classifyCancellation({abort_requested:true,effects});
    assert.equal(r.abort_request_observed,true);
    assert.equal(r.later_effect_observed,effects.includes('after'));
    assert.equal(r.cancellation_prevents_later_effect,null);
    assert.equal(r.effect_ordering_relative_to_abort_verified,false);
    assert.equal(r.physical_termination_verified,false);
  }
});

test('malformed observations cannot produce a positive classification',()=>{
  for(const body of [null,{}, {abort_requested:'true',effects:[]}, {abort_requested:true,effects:[1]}]) {
    assert.throws(()=>classifyCancellation(body),TypeError);
  }
});

test('actual historical cloud verification fails closed without rewriting evidence',async()=>{
  const historical=new URL('../results/fresh-cloud-verification.json',import.meta.url);
  const before=await readFile(historical);
  const result=spawnSync(process.execPath,[fileURLToPath(new URL('./verify-fresh-cloud.mjs',import.meta.url))],
    {encoding:'utf8',timeout:10000});
  assert.equal(result.error,undefined);
  assert.equal(result.status,1,'Physical-stop acceptance must remain failed');
  const report=JSON.parse(result.stdout);
  assert.equal(report.cpu_cancellation_test_exercised,false);
  assert.equal(report.cpu_cancellation_classification,'not_exercised');
  assert.equal(report.cpu_cancellation_prevents_later_effect,null);
  assert.equal(report.provider_cpu_exception_is_uncatchable,false);
  assert.equal(report.first_class_executor_acceptance,false);
  assert.deepEqual(await readFile(historical),before);
});
