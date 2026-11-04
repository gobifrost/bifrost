import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
import {boundedText, diagnosticJson} from '../probe-io.mjs';
const response = await fetch('http://127.0.0.1:8799/', {method: 'POST', signal: AbortSignal.timeout(45000)});
const text = await boundedText(response);
writeFileSync('/qualification/rpc-serialization-observation.json', diagnosticJson({status: response.status, text}), {flag: 'wx'});
assert.equal(response.status, 200);
const body = JSON.parse(text);
assert.equal(body.tests_run, 8);
assert.equal(body.failures, 0);
assert.equal(body.errors, 0);
assert.equal(body.wire_limit, 4194304);
assert.equal(body.abi, 'cpython-314-wasm32-emscripten');
assert.equal(typeof body.allocation.available, 'boolean');
if (body.allocation.available) {
  assert.deepEqual(body.allocation.samples.map(item => item.delivery_failure), [false, false, true]);
  for (const item of body.allocation.samples) assert.ok(item.python_traced_peak_bytes >= item.python_traced_current_bytes);
}
console.log(JSON.stringify({status: response.status, body, cloud_invocations: 0}));
