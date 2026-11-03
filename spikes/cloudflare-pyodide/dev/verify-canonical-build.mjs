import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const source = JSON.parse(read('canonical-build-source-v1.json'));
const runtime = JSON.parse(read('canonical-build-runtime-v1.json'));
const worker = JSON.parse(read('canonical-build-worker-v2.json'));
const receipt = JSON.parse(read('canonical-build-v2-receipt.json'));
for (const [name, digest] of Object.entries(source.files_sha256)) {
  if (name.endsWith('cooperative-code.mjs') || name.endsWith('rpc-serialization-code.mjs')) continue;
  const bytes = readFileSync(new URL('../'+name, import.meta.url));
  const actual = name === 'dev/executor/cloudflare.config.ts'
    ? bytes.toString().replace("entrypoint: './entry.mjs'", "entrypoint: './canonical-entry.mjs'") : bytes;
  assert.equal(sha(actual), digest, name);
}
for (const [file, generated] of [['cooperative_execution.py', 'cooperative-code.mjs'], ['rpc_serialization.py', 'rpc-serialization-code.mjs']]) {
  assert.equal(source.files_sha256['dev/executor/'+generated],
    sha(`export default ${JSON.stringify(read('../backend/'+file))};`));
}
assert.equal(runtime.bundle_sha256, '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731');
assert.equal(worker.name, 'bifrost-pyodide-spike-ddf316c1');
assert.equal(worker.workersDev, false);
assert.equal(worker.previewUrls, false);
assert.equal(worker.env.DEV_EXPIRES_AT.value, '0');
assert.equal(worker.env.DEV_AUTH, undefined);
assert.equal(worker.limits.cpuMs, 1000);
assert.equal(worker.env.DEV_API.worker, 'bifrost-pyodide-dev-ui-ddf316c1');
assert.equal(receipt.exit_code, 0);
assert.equal(sha(read('canonical-build-worker-v2.json').trim()), receipt.worker_config_sha256);
assert.equal(receipt.container_bounds.network, 'none');
assert.equal(receipt.deployment_performed, false);
assert.equal(receipt.runtime_acceptance, false);
assert.deepEqual(receipt.builder_containers_remaining, []);
console.log('Guarded canonical cf CLI build/source evidence verified; runtime/deployment remain unqualified.');
