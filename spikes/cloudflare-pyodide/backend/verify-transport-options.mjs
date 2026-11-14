import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root = new URL('../', import.meta.url);
const receipt = JSON.parse(readFileSync(new URL('results/transport-options/receipt.json', root), 'utf8'));
const source = JSON.parse(readFileSync(new URL('results/sdk1217/transport-source.json', root), 'utf8').replace(/^\uFEFF/, ''));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
assert.equal(hash(readFileSync(new URL('backend/latest-sdk-probe-entry.mjs', root))), receipt.packet_sha256['project/backend/latest-sdk-probe-entry.mjs']);
assert.equal(receipt.observation.status, 200);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.engine_sdk_execution_verified, false);
assert.equal(receipt.running_after_cleanup, 'false');
const body = receipt.observation.body;
assert.equal(body.runtime.soabi, 'cpython-314-wasm32-emscripten');
assert.equal(body.transport_source_sha256, hash(source.source));
assert.equal(body.transport_source_sha256, source.sha256);
const cases = ['default', 'verify_false', 'ca_path', 'ssl_context', 'client_certificate',
  'proxy', 'unix_socket', 'local_address', 'retries', 'http2', 'http1_disabled', 'pool_limits', 'socket_options'];
for (const name of ['JavascriptFetchTransport', 'AsyncJavascriptFetchTransport']) {
  const observed = body.transport_options[name];
  assert.deepEqual(Object.keys(observed).sort(), [...cases].sort());
  for (const value of Object.values(observed)) assert.deepEqual(value, {accepted: true, instance_keys: []});
}
assert.equal(body.client_close.cleanup_done, true);
assert.equal(body.interrupted_close.sync_closed_without_followup, true);
console.log('26 local Pyodide constructor observations reproduce silently ignored transport options; no network/TLS compatibility claim.');
