import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url);
const read = name => JSON.parse(readFileSync(new URL('results/sdk1217/' + name, root), 'utf8').replace(/^\uFEFF/, ''));
const before = read('receipt-v2.json'), after = read('receipt-v4.json');
const overlay = read('overlay-manifest.json');
assert.equal(overlay.commit, '139596497d57b219ddcc3616aa66ec58e1b3cf3c');
for (const path of ['bifrost/client.py', 'bifrost/_tls.py', 'bifrost/_workspace_lock.py']) {
  const bytes = execFileSync('git', ['show', overlay.commit + ':api/' + path]);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), overlay.sha256[path]);
}
assert.equal(overlay.dependency.abi, 'py3-none-any');
assert.deepEqual(overlay.dependency.patches, []);
assert.equal(overlay.sha256['certifi/cacert.pem'], after.observation.body.certifi_api.contents_sha256);
assert.equal(before.observation.body.probes['bifrost.client'].missing_module, 'certifi');
assert.equal(before.engine_sdk_execution_verified, false);
assert.equal(after.sdk_import_constructor_passed, true);
assert.equal(after.engine_sdk_execution_verified, false);
assert.equal(after.cloud_invocations, 0);
assert.equal(after.running_after_cleanup, 'false');
assert.equal(after.observation.body.runtime.pyodide, '314.0.6');
assert.equal(after.observation.body.certifi_api.contents_bytes, 238641);
assert.equal(after.observation.body.certifi_api.contents_sha256, '16be3f6feb15408195dcfe3aa1a75ef9db72f646b96ebbefdc68f56255f799f8');
assert.equal(after.observation.body.certifi_api.certificate_store.x509_ca, 0);
for (const [local, packet] of [['ct128-sdk-probe.py', 'ct128-sdk-probe.py'],
  ['add-certifi-overlay.py', 'add-certifi-overlay.py'],
  ['latest-sdk-probe-entry.mjs', 'project/backend/latest-sdk-probe-entry.mjs']]) {
  const bytes = ['ct128-sdk-probe.py', 'add-certifi-overlay.py', 'latest-sdk-probe-entry.mjs'].includes(local)
    ? execFileSync('git', ['show', '4abf2981f2d9673ef340d4864c8eddabd0f66bb3:spikes/cloudflare-pyodide/backend/' + local])
    : readFileSync(new URL('backend/' + local, root));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),
    after.packet_sha256[packet], local);
}
const transport = read('transport-source.json');
assert.equal(createHash('sha256').update(transport.source).digest('hex'), transport.sha256);
assert.match(transport.source, /verify: ssl.SSLContext/);
assert.match(transport.source, /return js.fetch/);
console.log('Exact upstream SDK/pure-wheel import probe verified; TLS options and real operations remain unqualified.');
