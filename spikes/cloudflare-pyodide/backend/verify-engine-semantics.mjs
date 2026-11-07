// Offline historical evidence verification. No Worker, API or network invocation.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const directory=new URL('../results/engine-semantics-resume/',import.meta.url);
const receipt=JSON.parse(readFileSync(new URL('receipt.json',directory),'utf8'));
const observed=JSON.parse(readFileSync(new URL('observed.json',directory),'utf8'));
const repo=new URL('../../../',import.meta.url);
assert.equal(receipt.source,'785b560b9973020e1c13401383e1a73a87a54940');
const source=execFileSync('git',['show',receipt.source+':api/src/services/execution/engine.py'],{cwd:repo});
assert.equal(createHash('sha256').update(source).digest('hex'),receipt.engine_sha256);
assert.equal(receipt.exit_code,0);
for(const flag of ['cloudflare_runtime_qualified','sdk_operation_qualified','physical_termination_verified']) {
  assert.equal(receipt[flag],false,flag);
}
assert.equal(receipt.cloud_invocations,0);
assert.deepEqual(observed.abi,{pyodide:'314.0.6',python:[3,14,2],cache_tag:'cpython-314',
  soabi:'cpython-314-wasm32-emscripten',platform:'emscripten-5.0.3-wasm32'});
const cases=observed.observations;
assert.deepEqual(cases.map(item=>[item.name,item.result.status]),[
  ['json','Success'],['async','Success'],['partial','CompletedWithErrors'],['failure','Failed']]);
assert.equal(cases[0].result.result.total,6);
assert.equal(cases[0].result.result.execution_id,cases[0].result.execution_id);
assert.deepEqual(cases[1].result.result,{value:42,scope:'GLOBAL'});
assert.equal(cases[3].result.error_type,'ValueError');
assert.match(cases[3].result.error_message,/credential=\[REDACTED\]/);
assert.ok(!JSON.stringify(observed).includes('synthetic-engine-redaction-marker'));
console.log('Four actual upstream engine cases verified on guest Pyodide; SDK/cloud/physical-stop acceptance remains false');
