import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const read = path => readFileSync(new URL(path, import.meta.url));
const hash = value => createHash('sha256').update(value).digest('hex');
const receipt = JSON.parse(read('../results/httpx-redirect/v6.json'));
const hashes = receipt.packet_sha256;
for (const [source, packet] of [
  ['approved-httpx-entry.mjs', 'project/backend/approved-https-entry.mjs'],
  ['approved-httpx-probe.mjs', 'project/backend/approved-https-probe.mjs'],
  ['httpx_redirect_patch.py', 'httpx_redirect_patch.py'],
  ['approved-https-candidate.mjs', 'project/backend/approved-https-candidate.mjs'],
  ['attempt-egress.mjs', 'project/backend/attempt-egress.mjs'],
  ['ct128-sdk-probe.py', 'ct128-sdk-probe.py'],
]) {
  const bytes = source === 'approved-httpx-probe.mjs'
    ? execFileSync('git', ['show', '96d7f2727c3d329c1fab637f70554d50575534df:spikes/cloudflare-pyodide/backend/'+source])
    : read(source);
  assert.equal(hash(bytes), hashes[packet], source);
}
assert.equal(hashes['project/backend/generated-runtime.mjs'], '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731');
const generated = read('../results/httpx-redirect/patch-code.mjs');
assert.equal(hash(generated), hashes['project/backend/httpx-redirect-code.mjs']);
assert.equal(JSON.parse(generated.toString().slice('export default '.length, -1)), read('httpx_redirect_patch.py').toString());
assert.equal(receipt.running_after_cleanup, 'false');
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.engine_sdk_execution_verified, false);
assert.equal(receipt.observation.status, 200);
const body = receipt.observation.body;
assert.equal(body.httpx, '0.28.1');
assert.equal(body.runtime.soabi, 'cpython-314-wasm32-emscripten');
assert.deepEqual(body.rows.map(row => row.history), [[], [302], [307]]);
assert.deepEqual(body.rows.map(row => row.status), [200, 403, 200]);
assert.deepEqual(body.rows.map(row => row.url), ['https://vendor.example/write', 'https://other.example/forbidden', 'https://vendor.example/final']);
assert.deepEqual(receipt.observation.effects.map(effect => effect.path), ['/write', '/redirect', '/same-origin', '/final']);
assert.ok(receipt.observation.effects.every(effect => effect.body === 'synthetic' && effect.method === 'POST' && effect.authorization === 'Bearer vendor-token'));
const before = JSON.parse(read('../results/httpx-redirect/v5-observation.json'));
assert.deepEqual(before.body.rows.map(row => row.history), [[], [], []]);
const failed = JSON.parse(read('../results/httpx-redirect/v5.json'));
assert.equal(hash(read('../results/httpx-redirect/v5-entry.mjs')), failed.packet_sha256['project/backend/approved-https-entry.mjs']);
assert.equal(failed.running_after_cleanup, 'false');
console.log('HTTPX redirect mismatch and pinned local repair verified; no canonical/cloud acceptance.');
