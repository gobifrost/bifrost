import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const read = path => readFileSync(new URL(path, import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(read('../results/approved-https/v3.json'));
for (const name of ['approved-https-entry.mjs', 'approved-https-probe.mjs',
  'approved-https-candidate.mjs', 'attempt-egress.mjs']) {
  assert.equal(hash(read(name)), receipt.packet_sha256[`project/backend/${name}`]);
}
assert.equal(hash(read('ct128-sdk-probe.py')), receipt.packet_sha256['ct128-sdk-probe.py']);
for (const name of ['package.json', 'cloudflare.config.ts', 'wrangler.config.ts']) {
  assert.equal(hash(read(`approved-https-fixture/${name}`)), receipt.packet_sha256[`project/${name}`]);
}
assert.equal(receipt.running_after_cleanup, 'false');
assert.equal(receipt.provider_state_before_cleanup.OOMKilled, false);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.engine_sdk_execution_verified, false);
assert.equal(receipt.observation.status, 200);
assert.equal(receipt.observation.body.abi, 'cpython-314-wasm32-emscripten');
assert.deepEqual(receipt.observation.body.observed.map(item => item.status), [403, 403, 200, 302, 429]);
assert.deepEqual(receipt.observation.effects, [
  {path: '/write', authorization: 'Bearer vendor-token', body: 'synthetic'},
  {path: '/redirect', authorization: null, body: 'synthetic'},
]);
for (const revision of [1, 2]) {
  const failed = JSON.parse(read(`../results/approved-https/v${revision}.json`));
  assert.equal(failed.observation, undefined);
  assert.equal(failed.running_after_cleanup, 'false');
}
console.log('Pinned local Pyodide HTTPS policy receipt verified; no cloud or cancellation acceptance.');
