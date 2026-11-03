import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL('results/rpc-results/' + name, root), 'utf8');
const hash = value => createHash('sha256').update(value).digest('hex');
const reports = [1, 2, 3, 4].map(version => JSON.parse(read('v' + version + '.json')));
for (const [index, receipt] of reports.entries()) {
  assert.equal(receipt.cloud_invocations, 0);
  assert.equal(receipt.engine_sdk_execution_verified, false);
  assert.equal(receipt.sdk_import_constructor_passed, false);
  assert.equal(receipt.running_after_cleanup, 'false');
  assert.equal(receipt.observation.status, 200);
  const code = index === 3 ? readFileSync(new URL('backend/rpc-result-entry.mjs', root)) : read('v' + (index + 1) + '-entry.mjs.txt');
  assert.equal(hash(code), receipt.packet_sha256['project/backend/rpc-result-entry.mjs']);
}
assert.equal('value' in reports[0].observation.body, false, 'invalid v1 field access stays visible');
const latest = reports[3], body = latest.observation.body;
assert.equal(hash(readFileSync(new URL('backend/ct128-sdk-probe.py', root))), latest.packet_sha256['ct128-sdk-probe.py']);
assert.deepEqual(body.abi, {pyodide: '314.0.6', python: [3, 14, 2], cache_tag: 'cpython-314',
  soabi: 'cpython-314-wasm32-emscripten', platform: 'emscripten-5.0.3-wasm32'});
assert.deepEqual(body.result_shape, {tag: '[object Map]', is_map: true, own_keys: []});
for (const key of ['result_features', 'promise_features', 'pending_features']) assert.equal(body[key].available, true);
for (const key of ['result_disposed', 'promise_disposed', 'pending_disposed', 'released_after_disposal']) assert.equal(body[key], true);
for (const key of ['value', 'promise_value', 'primitive']) assert.equal(body[key], 7);
assert.equal(body.primitive_features.available, false);
assert.deepEqual(body.before, {started: 1, after: 1});
assert.deepEqual(body.at_disposal, {started: 2, after: 1});
assert.deepEqual(body.pending_outcome, {returned: true, value: 7});
assert.deepEqual(body.after, {started: 2, after: 2});
assert.equal(body.physical_termination_verified, false);
assert.match(read('quality.txt'), /0 errors, 0 warnings/);
console.log('Actual local RPC Map/promise disposal and causally ordered post-disposal I/O completion verified; no deployed or physical-stop claim.');
