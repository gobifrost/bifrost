import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';

const response = await fetch('http://127.0.0.1:8799/', {
  method: 'POST', signal: AbortSignal.timeout(45000),
});
const text = await response.text();
const observation = {status: response.status, text, cloud_invocations: 0};
writeFileSync('/qualification/cooperative-observation.json', JSON.stringify(observation, null, 2), {flag: 'wx'});
assert.equal(response.status, 200);
const body = JSON.parse(text);
for (const item of body.cases) {
  assert.equal(item.entered, true);
  assert.equal(item.wrongRun, false);
  assert.equal(item.lateCancel, false);
  assert.equal(item.status, 200);
  assert.equal(item.body.active_slots, 0);
  assert.deepEqual(item.body.cleaned, [true]);
  assert.match(item.body.abi, /cpython-314-wasm32-emscripten/);
}
assert.deepEqual(body.cases.map(item => item.mode), ['normal', 'cancel', 'suppress']);
assert.deepEqual(body.cases.map(item => item.requested), [false, true, true]);
assert.deepEqual(body.cases[0].body.outcome, {status: 'Success', result: 'normal'});
assert.deepEqual(body.cases[1].body.outcome, {
  cooperative_task_cancelled: true, physical_stopped: false, execution_settled: false,
});
assert.deepEqual(body.cases[2].body.outcome, {status: 'Success', result: 'suppressed'});
console.log(JSON.stringify({status: response.status, body, cloud_invocations: 0}));
