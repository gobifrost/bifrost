import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {classifyTimedCancellation} from './termination-evidence.mjs';
test('timestamped late effect is a counterexample, never stop acceptance',()=>{
  const result=classifyTimedCancellation([
    {event:'before',elapsed_ms:1},{event:'abort_requested',elapsed_ms:2},{event:'after',elapsed_ms:3}]);
  assert.equal(result.classification,'late_effect_after_abort');
  assert.equal(result.physical_termination_verified,false);
  assert.equal(result.canonical_late_result_fencing_verified,false);
});
test('caught quota and bounded absence cannot establish cessation',()=>{
  assert.equal(classifyTimedCancellation([{event:'caught',elapsed_ms:1},{event:'after',elapsed_ms:2}]).classification,'not_exercised');
  const result=classifyTimedCancellation([{event:'abort_requested',elapsed_ms:1}]);
  assert.equal(result.classification,'observation_only');
  assert.equal(result.physical_termination_verified,false);
  assert.throws(()=>classifyTimedCancellation([{event:'after',elapsed_ms:2},{event:'abort_requested',elapsed_ms:1}]),TypeError);
});
test('real driver refuses default invocation before binding or fetching',()=>{
  const result=spawnSync(process.execPath,[new URL('./guest-termination-probe.mjs',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],{encoding:'utf8'});
  assert.equal(result.status,1);
  assert.match(result.stderr,/Explicit --run required/);
});
