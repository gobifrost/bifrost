import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const receipt = JSON.parse(await readFile(new URL('../results/disposal-probe/disposal-v4.json', import.meta.url), 'utf8'));
assert.equal(receipt.runtime_probe_passed, true);
assert.equal(receipt.observation.status, 200);
assert.deepEqual(receipt.observation.body.abi, {python: [3, 14, 2], pyodide: '314.0.6', soabi: 'cpython-314-wasm32-emscripten'});
for (const name of ['worker_dispose', 'worker_async_dispose', 'entrypoint_dispose', 'entrypoint_async_dispose']) {
  assert.equal(receipt.observation.body.features[name].available, false);
}
assert.equal(receipt.observation.body.completed_call_dispose_returned, false);
assert.equal(receipt.physical_termination_verified, false);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.cf_cli_version, '1.0.0-beta.26');
assert.equal(receipt.wrangler_version, '4.156.0');
assert.equal(receipt.bundle_sha256, '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260');
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.state_before_cleanup.OOMKilled, false);
assert.ok(receipt.command.includes('--network=none'));
assert.equal(receipt.command.at(-1), 'dev');
for (const [packet, local] of Object.entries({
  'guest-disposal-entry.mjs': './guest-disposal-entry.mjs',
  'cloudflare.config.ts': './disposal-fixture/cloudflare.config.ts',
  'wrangler.config.ts': './disposal-fixture/wrangler.config.ts',
  'package.json': './disposal-fixture/package.json',
})) {
  const digest = createHash('sha256').update(await readFile(new URL(local, import.meta.url))).digest('hex');
  assert.equal(digest, receipt.packet_sha256[packet], packet);
}
console.log('Pinned local Python ABI passed; WorkerStub/entrypoint disposers absent. Physical stop remains unverified.');
