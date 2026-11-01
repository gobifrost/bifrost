// Verify preserved publisher-build, actual-workerd ABI and negative termination evidence.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../results/',import.meta.url);
function receipt(path,hash) {
  const bytes=readFileSync(new URL(path,root));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),hash);
  return JSON.parse(bytes);
}
const expected='3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260';
const build=receipt('publisher-build-resume/v7-receipt.json','44e4d9c8727d1f503f72a419c3d141c38be342b2281c115a794fcbd9d8d4f98d');
assert.equal(build.exit_code,0);
assert.equal(build.bundle_verified,true);
assert.equal(build.candidates[0].sha256,expected);
assert.deepEqual(build.running_after_cleanup,[]);
const abi=receipt('source-runtime-resume/receipt.json','d194934a6d3d5693b72cbf7fa9585cba9a1ecc1064d006a98bb5209012d4a4b5');
assert.equal(abi.network,'none');
assert.equal(abi.runtime_qualified,true);
assert.equal(abi.bundle_cache.sha256,expected);
assert.equal(abi.response.status,200);
assert.equal(abi.abi.pyodide,'314.0.6');
assert.deepEqual(abi.abi.python,[3,14,2]);
assert.equal(abi.abi.cache_tag,'cpython-314');
assert.equal(abi.abi.soabi,'cpython-314-wasm32-emscripten');
assert.equal(abi.abi.platform,'emscripten-5.0.3-wasm32');
assert.equal(abi.abi.pydantic_core,'2.50.2');
assert.deepEqual(abi.abi.validated,{values:[1,2]});
assert.equal(abi.abi.invalid_rejected,true);
assert.equal(abi.state_after_stop.Running,false);
const stop=receipt('source-termination-resume/receipt.json','242432e69e5ee0313ab9b4f60328166095b1ceb5eeba6f0b82754e11ba64336d');
assert.equal(stop.observer_exit_code,0);
assert.deepEqual(stop.running_after_cleanup,[]);
const control=stop.observations.records.find(r=>r.mode==='control');
assert.ok(control.events.some(e=>e.event==='after'));
const abort=stop.observations.records.find(r=>r.mode==='abort');
const requested=abort.events.find(e=>e.event==='abort_requested');
const after=abort.events.find(e=>e.event==='after');
assert.ok(after.elapsed_ms>requested.elapsed_ms);
assert.equal(abort.classification,'late_effect_after_abort');
for(const report of [build,abi,stop]) {
  assert.equal(report.cloud_invocations,0);
  assert.equal(report.physical_termination_verified,false);
}
assert.equal(stop.execution_admission_qualified,false);
console.log('Exact bundle and actual-workerd ABI verified; later CPU effect after abort confirms termination remains unqualified.');
