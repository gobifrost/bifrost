import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/startup-inventory/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
assert.match(await read('startup-inventory-quality-v1/unit.txt'), /Ran 61 tests[\s\S]*\bOK\b/);
assert.match(await read('startup-inventory-quality-v1/quality.txt'), /2 errors/);
assert.match(await read('startup-inventory-quality-v2/quality.txt'), /0 errors, 0 warnings/);
const raw = await read('startup-inventory-v2/receipt.json');
assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
const report = JSON.parse(raw);
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.deepEqual(report.running_after_cleanup, []);
for (const name of ['runtime_inventory.py', 'runtime_guard.py', 'test_runtime_inventory.py',
  'private_http_pool.py', 'test_private_http_pool.py', 'shared-native-pool.py', 'ct128-canonical-failure.py', 'runtime-code.mjs']) {
  const source = ['shared-native-pool.py', 'ct128-canonical-failure.py', 'private_http_pool.py', 'test_private_http_pool.py', 'runtime-code.mjs'].includes(name)
    ? execFileSync('git', ['show', '62fcac679c139148d9529afc1445541e40a9a0f3:spikes/cloudflare-pyodide/backend/' + name])
    : await readFile(new URL('./' + name, import.meta.url));
  assert.equal(hash(source), report.packet_sha256[name], name);
}
assert.equal(report.packet_sha256['runtime-code.mjs'], report.packet_sha256['project/backend/runtime-code.mjs']);
for (const line of (await read('unit-source.sha256')).trim().split(/\r?\n/)) {
  const [digest, path] = line.split(/\s+/);
  const name = path.split('/').at(-1);
  assert.equal(digest, report.packet_sha256[name], name + ' unchanged unit input');
}
const probe = report.shared_pool_result;
assert.equal(probe.native_runtime_cache_projection_verified, true);
assert.equal(probe.native_runtime_cache_removed, true);
assert.equal(probe.full_native_startup_qualified, false);
assert.equal(probe.failed_replay_unchanged_verified, true);
assert.deepEqual(probe.native_busy_counts, [2, 2, 1, 0]);
assert.equal(probe.actual_runtime_inventory.package_environment, 'pyodide');
assert.equal(probe.actual_runtime_inventory.runtime.soabi, 'cpython-314-wasm32-emscripten');
assert.equal(probe.actual_runtime_inventory.packages.length, 11);
const packages = Object.fromEntries(probe.actual_runtime_inventory.packages.map(item => [item.name, item.version]));
assert.equal(packages.httpx, '0.28.1');
assert.equal(packages.pydantic, '2.14.2');
assert.equal(packages['pydantic-core'], '2.50.2');
console.log('Actual Pyodide package/ABI probe and unchanged upstream Redis projection verified with real engine/SDK control and cleanup. Full native/signed startup remains unqualified.');
