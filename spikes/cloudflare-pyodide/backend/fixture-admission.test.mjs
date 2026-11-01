import assert from 'node:assert/strict';
import test from 'node:test';
import {admitFixture,fixtureRejection} from './fixture-admission.mjs';
const id='11111111-1111-4111-8111-111111111111';
const suffix='a'.repeat(32),hash='b'.repeat(64);
const path=`workflows/pyodide_qualification_${suffix}.py`;
function request(){return {mode:'canonical',worker_id:id,execution_id:id,
  context:{execution_id:id,file_path:path,function_name:'qualified',engine_token:'synthetic-test-only',
    timeout_seconds:30,content_hash:hash,code:null,solution_id:null,artifact_workspace_id:null},
  manifest:{[path]:hash,[`pyodide_helper_${suffix}.py`]:hash}};}
test('supported fixture is passed unchanged to the runtime callback exactly once',async()=>{
  const body=request();let calls=0;
  const response=await admitFixture(body,0,()=>{calls++;return Response.json({accepted:true});});
  assert.equal(response.status,200);assert.equal(calls,1);
  assert.equal(body.context.engine_token,'synthetic-test-only');
  assert.equal(fixtureRejection({...body,mode:undefined,worker_id:undefined},0),null);
});
const cases=[
  ['missing context',b=>{b.context=null;}],
  ['different attempt',b=>{b.context.execution_id='22222222-2222-4222-8222-222222222222';}],
  ['malformed worker',b=>{b.worker_id='-'.repeat(36);}],
  ['unknown mode',b=>{b.mode='linux';}],
  ['unbounded duration',b=>{b.context.timeout_seconds=1800;}],
  ['fractional duration',b=>{b.context.timeout_seconds=0.5;}],
  ['inline script',b=>{b.context.code='';}],
  ['service',b=>{b.context.service={};}],
  ['admin',b=>{b.context.is_platform_admin=true;}],
  ['solution remapping',b=>{b.context.solution_id=id;}],
  ['artifact workspace',b=>{b.context.artifact_workspace_id=id;}],
  ['unmatched entry digest',b=>{b.context.content_hash='c'.repeat(64);}],
  ['bad helper digest',b=>{b.manifest[`pyodide_helper_${suffix}.py`]='not-a-digest';}],
  ['unpinned extra module',b=>{b.manifest['evil.py']=hash;}],
  ['missing helper',b=>{delete b.manifest[`pyodide_helper_${suffix}.py`];}],
  ['oversized credential',b=>{b.context.engine_token='x'.repeat(16385);}],
];
for(const [name,mutate] of cases)test(`${name} is refused before runtime or API effects`,async()=>{
  const body=request();mutate(body);let effects=0;
  const response=await admitFixture(body,0,()=>{effects++;throw new Error('Must not create runtime/API capability');});
  assert.equal(response.status,422);assert.equal(effects,0);
  const result=await response.json();assert.equal(result.error,'Fixture not admitted');
  assert.ok(result.reason);assert.ok(!JSON.stringify(result).includes('synthetic-test-only'));
});
test('full or unknown capacity cannot invoke runtime',async()=>{
  for(const active of [4,5,-1,NaN,undefined]) {
    const response=await admitFixture(request(),active,()=>{throw new Error('Must not launch');});
    assert.equal(response.status,422);
  }
});
