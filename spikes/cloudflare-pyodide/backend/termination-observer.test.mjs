import test from 'node:test';
import assert from 'node:assert/strict';
import {createTerminationObserver} from './termination-observer.mjs';
import {classifyTimedCancellation} from './termination-evidence.mjs';
test('late previous-case effect stays correlated after the next case starts',()=>{
  let clock=0;
  const observer=createTerminationObserver(()=>clock);
  const first=observer.register('first','abort');
  clock=1;observer.record('first','before');
  clock=2;observer.record('first','abort_requested');
  observer.register('second','control');
  clock=3;assert.equal(observer.record('first','after'),true);
  assert.equal(classifyTimedCancellation(first.events).classification,'late_effect_after_abort');
  assert.deepEqual(observer.get('second').events,[]);
});
test('unknown IDs and invalid events cannot add observations; fixed bounds hold',()=>{
  const observer=createTerminationObserver(()=>0);
  observer.register('first','control');
  assert.equal(observer.record('unknown','after'),false);
  assert.equal(observer.record('first','invented'),false);
  for(let i=0;i<16;i++) assert.equal(observer.record('first','before'),true);
  assert.equal(observer.record('first','after'),false);
  assert.throws(()=>observer.register('first','control'),TypeError);
  observer.register('second','catch');observer.register('third','abort');
  assert.throws(()=>observer.register('fourth','abort'),TypeError);
});
