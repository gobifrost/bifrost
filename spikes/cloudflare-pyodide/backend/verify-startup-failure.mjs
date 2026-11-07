import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const hash=value=>createHash('sha256').update(value).digest('hex');
const receipt=JSON.parse(readFileSync(new URL('../results/startup-failure-probe/v1.json',import.meta.url)));
assert.equal(hash(readFileSync(new URL('startup-failure-probe.py',import.meta.url))),'81414c82b34f2a173a7d8d20708112cf4a234eb3eea321d07f492982783e057e');
assert.equal(hash(readFileSync(new URL('../../../api/src/worker/remote/remote_pool.py',import.meta.url))),receipt.remote_pool_sha256);
assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.execution_admission_qualified,false);
assert.deepEqual(receipt.cases.map(value=>[value.case,value.errors,value.started,value.lifecycle_tasks_created]),[
  ['configure',['RuntimeError','RuntimeError'],false,0],
  ['registration',['RuntimeError',null],true,0],
  ['tagging',['RuntimeError',null],true,0],
  ['healthy',[null,null],true,4],
]);
console.log('Exact upstream partial-startup counterexample preserved; source/mock scope only, no fix or admission claim.');
